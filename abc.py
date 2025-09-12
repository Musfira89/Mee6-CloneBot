import time
import asyncio
from maubot import Plugin
from maubot.handlers import event, command
from mautrix.types import EventType, MessageEvent

# Hardcoded actor id for power-level changes / bans
BOT_ACTOR = "@mee6bot:j5.chat"


class SpamHandler:
    def __init__(self, plugin):
        self.plugin = plugin
        self.message_log = {}
        self.warned = {}         # last warning time
        self.muted = {}          # mapping (room,user) -> {"until": ts, "mode": "pl"|"fallback"}
        self.prev_power = {}     # mapping (room,user) -> previous power level
        self.offenses = {}       # offense counts per (room,user)

    def _key(self, room_id, user):
        return (room_id, user)

    def _get_offense_count(self, room_id, user):
        return self.offenses.get(self._key(room_id, user), 0)

    def _increment_offense(self, room_id, user):
        k = self._key(room_id, user)
        self.offenses[k] = self.offenses.get(k, 0) + 1
        return self.offenses[k]

    def _reset_offenses(self, room_id, user):
        self.offenses.pop(self._key(room_id, user), None)

    async def check_and_handle_spam(self, room_id, user, now, evt):
        key = self._key(room_id, user)

        # If currently muted: redact messages (fallback) or ignore them (pl)
        if key in self.muted and now < self.muted[key]["until"]:
            mode = self.muted[key].get("mode", "fallback")
            if mode == "fallback":
                try:
                    await self.plugin._safe_redact(room_id, evt.event_id, "Muted: deleted instantly")
                except Exception:
                    self.plugin.log.exception("Failed to redact message from muted user")
                return True
            # if mode == "pl", just ignore incoming messages
            return True

        # If there is a recorded mute that has expired but no scheduled unmute ran (rare),
        # restore power now when the user posts again after expiry
        if key in self.muted and now >= self.muted[key]["until"]:
            try:
                await self.plugin._restore_user_power(room_id, user)
                # produce a notice in room explaining automatic unmute (optional)
                try:
                    await self.plugin.client.send_message_event(
                        room_id, "m.room.message",
                        {"msgtype": "m.notice", "body": f"✅ {user} has been unmuted automatically."}
                    )
                except Exception:
                    # send_message_event may differ by client versions; ignore if not available
                    pass
            except Exception:
                self.plugin.log.exception("Failed to restore user power on mute expiry")
            self.muted.pop(key, None)
            self.message_log[key] = []
            return False

        # Rolling spam window
        timestamps = self.message_log.get(key, [])
        timestamps = [t for t in timestamps if now - t <= self.plugin.SPAM_INTERVAL]
        timestamps.append(now)
        self.message_log[key] = timestamps
        msg_count = len(timestamps)

        if msg_count >= self.plugin.SPAM_WARNING:
            last_warn_time = self.warned.get(key, 0.0)
            warned_recently = (now - last_warn_time) < self.plugin.WARNING_COOLDOWN

            if not warned_recently:
                # Issue a warning
                self.warned[key] = now
                await evt.reply(f"⚠️ {user}, please slow down! This is your warning.")
                return False

            # Escalate punishment
            offense = self._increment_offense(room_id, user)

            # Durations: 1 -> 30s, 2 -> 60s, 3 -> 120s, 4 -> ban
            if offense == 1:
                duration = 30
                reason = "Spamming (1st mute)"
            elif offense == 2:
                duration = 60
                reason = "Spamming (2nd mute)"
            elif offense == 3:
                duration = 120
                reason = "Spamming (3rd mute)"
            elif offense >= 4:
               # 4th strike or higher → immediate ban (no warning)
               await evt.reply(f"🚫 {user} reached final spam offense — banning.")
               try:
                   await self.plugin._ban_user(room_id, user, "Spamming multiple times")
               except Exception:
                   self.plugin.log.exception("Failed to ban user on final offense")
               return True


            until = now + duration
            self.message_log[key] = []
            await evt.reply(f"🚫 {user} muted for {duration} seconds. Reason: {reason}")

            # Try to mute by changing power levels (preferred)
            try:
                ok = await self.plugin._mute_user(room_id, user, reason=reason, until_ts=until)
            except Exception:
                self.plugin.log.exception("Error in _mute_user")
                ok = False

            # Record muted state and schedule unmute (use asyncio.create_task to schedule)
            self.muted[key] = {"until": until, "mode": "pl" if ok else "fallback"}
            # schedule the unmute task correctly (avoid client.loop)
            asyncio.create_task(self.schedule_unmute(room_id, user, until))
            return True

        return False

    async def schedule_unmute(self, room_id, user, until_ts):
        delay = max(0.0, until_ts - time.time())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        key = self._key(room_id, user)
        if key in self.muted and time.time() >= self.muted[key]["until"]:
            try:
                await self.plugin._restore_user_power(room_id, user)
                # (system power-level state change will appear in Element timeline)
                # Optionally post a room notice about automatic unmute
                try:
                    await self.plugin.client.send_message_event(
                        room_id, "m.room.message",
                        {"msgtype": "m.notice", "body": f"✅ {user} has been unmuted automatically (mute expired)."}
                    )
                except Exception:
                    pass
            except Exception:
                self.plugin.log.exception("Failed to restore power in schedule_unmute")
            # cleanup
            self.muted.pop(key, None)
            self.message_log[key] = []


class Mee6(Plugin):
    SPAM_WARNING = 5
    SPAM_INTERVAL = 10
    WARNING_COOLDOWN = 30  # seconds between warnings to same user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.spam_handler = SpamHandler(self)

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent):
        # ignore messages from the hardcoded bot account
        if evt.sender == BOT_ACTOR:
            return

        now = time.time()
        user = evt.sender
        room_id = evt.room_id

        try:
            handled = await self.spam_handler.check_and_handle_spam(room_id, user, now, evt)
            if handled:
                return
        except Exception:
            self.log.exception("Spam handler failed")

    # ---------- Helpers for power-level changes, redaction, mute/ban/kick ----------

    async def _get_power_levels(self, room_id):
        try:
            # third arg is state_key; empty string used by many mautrix clients
            return await self.client.get_state_event(room_id, "m.room.power_levels", "")
        except Exception:
            self.log.exception("Failed to fetch power_levels")
            return None

    async def _set_power_levels(self, room_id, new_content):
        try:
            await self.client.send_state_event(room_id, "m.room.power_levels", new_content)
            return True
        except Exception:
            self.log.exception("Failed to set power_levels")
            return False

    async def _mute_user(self, room_id, user_id, reason=None, until_ts=None):
        """
        Set user's PL to -1 and store previous level; return True on success.
        """
        try:
            pl = await self._get_power_levels(room_id)
            if pl is None:
                return False

            users = pl.get("users", {})
            prev = users.get(user_id, pl.get("users_default", 0))

            # store previous power to restore later
            self.spam_handler.prev_power[(room_id, user_id)] = prev

            # set user power to -1 so Element shows "Custom (-1)"
            users[user_id] = -1
            pl["users"] = users

            ok = await self._set_power_levels(room_id, pl)
            if not ok:
                # cleanup storage on failure
                self.spam_handler.prev_power.pop((room_id, user_id), None)
                return False

            return True
        except Exception:
            self.log.exception("Mute user failed")
            self.spam_handler.prev_power.pop((room_id, user_id), None)
            return False

    async def _restore_user_power(self, room_id, user_id):
        """
        Restore previously-stored PL for the user (or fallback to users_default / 0).
        """
        try:
            pl = await self._get_power_levels(room_id)
            if pl is None:
                return False

            # prefer stored previous power; if missing, fall back to room users_default or 0
            prev = self.spam_handler.prev_power.get((room_id, user_id))
            if prev is None:
                prev = pl.get("users_default", 0)

            users = pl.get("users", {})
            users[user_id] = prev
            pl["users"] = users

            ok = await self._set_power_levels(room_id, pl)
            if ok:
                # remove stored previous power
                self.spam_handler.prev_power.pop((room_id, user_id), None)
                return True
            return False
        except Exception:
            self.log.exception("Restore power failed")
            return False

    async def _safe_redact(self, room_id, event_id, reason=""):
        try:
            await self.client.redact_event(room_id, event_id, reason=reason)
        except Exception:
            try:
                await self.client.redact(room_id, event_id, reason)
            except Exception:
                self.log.exception("Redact failed")

    async def _ban_user(self, room_id, user_id, reason=""):
        try:
            await self.client.ban_user(room_id, user_id, reason)
            return True
        except Exception:
            self.log.exception("Ban failed")
            return False

    async def _kick_user(self, room_id, user_id, reason=""):
        try:
            await self.client.kick_user(room_id, user_id, reason)
            return True
        except Exception:
            self.log.exception("Kick failed")
            return False

    # ---------- Commands ----------

    @command.new("unmute", help="Unmute a user. Usage: !unmute @user:server")
    async def cmd_unmute(self, evt, user_id: str):
        room_id = evt.room_id
        try:
            ok = await self._restore_user_power(room_id, user_id)
            self.spam_handler.muted.pop((room_id, user_id), None)
            if ok:
                await evt.reply(f"{user_id} has been unmuted.")
            else:
                await evt.reply(f"Attempted to restore {user_id}'s power but it may have failed.")
        except Exception:
            self.log.exception("Unmute command failed")
            await evt.reply("Failed to unmute user.")

    @command.new("forcemute", help="Force mute. Usage: !forcemute @user:server <seconds>")
    async def cmd_forcemute(self, evt, user_id: str, seconds: int = 30):
        room_id = evt.room_id
        until = time.time() + int(seconds)
        try:
            ok = await self._mute_user(room_id, user_id, reason="Manual forcemute", until_ts=until)
            # always record muted state and schedule unmute
            self.spam_handler.muted[(room_id, user_id)] = {"until": until, "mode": "pl" if ok else "fallback"}
            asyncio.create_task(self.spam_handler.schedule_unmute(room_id, user_id, until))
            if ok:
                await evt.reply(f"{user_id} muted for {seconds} seconds.")
            else:
                await evt.reply(f"{user_id} fallback-muted for {seconds} seconds (couldn't change PL).")
        except Exception:
            self.log.exception("Forcemute command failed")
            await evt.reply("Failed to forcemute user.")
