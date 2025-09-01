import time
import asyncio

class SpamHandler:
    def __init__(self, plugin):
        self.plugin = plugin
        self.message_log = {}
        self.warned = {}
        self.muted = {}
        self.prev_power = {}

    async def check_and_handle_spam(self, room_id, user, now, evt):
        key = (room_id, user)

        # Already muted? handled in on_message
        if key in self.muted and now < self.muted[key]:
            return True

        # Cleanup expired mute
        if key in self.muted and now >= self.muted[key]:
            await self.plugin._restore_user_power(room_id, user)
            self.muted.pop(key, None)
            self.message_log[key] = []

        # Rolling message window
        timestamps = self.message_log.get(key, [])
        timestamps = [t for t in timestamps if now - t <= self.plugin.SPAM_INTERVAL]
        timestamps.append(now)
        self.message_log[key] = timestamps
        msg_count = len(timestamps)

        if msg_count >= self.plugin.SPAM_WARNING:
            last_warn_time = self.warned.get(key, 0.0)
            warned_recently = (now - last_warn_time) < self.plugin.WARNING_COOLDOWN

            if not warned_recently:
                # First warning
                self.warned[key] = now
                await evt.reply(f"⚠️ {user}, please slow down! This is your warning.")
                return False

            # Second time => mute
            until = now + self.plugin.MUTE_DURATION
            self.muted[key] = until
            self.message_log[key] = []

            await evt.reply(f"🚫 {user} has been muted for {self.plugin.MUTE_DURATION} seconds due to spamming.")
            await self.plugin._mute_user(room_id, user)
            await self.plugin._safe_redact(room_id, evt.event_id, "Muted due to spamming")

            self.plugin.client.loop.create_task(
                self.schedule_unmute(room_id, user, until)
            )
            return True

        return False

    async def schedule_unmute(self, room_id, user, until_ts):
        delay = max(0.0, until_ts - time.time())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        key = (room_id, user)
        if key in self.muted and time.time() >= self.muted[key]:
            await self.plugin._restore_user_power(room_id, user)
            self.muted.pop(key, None)
            self.message_log[key] = []

    async def is_currently_muted(self, room_id, user, now):
        key = (room_id, user)
        return key in self.muted and now < self.muted[key]

    async def add_xp(self, user, now):
        if user not in self.plugin.last_msg or now - self.plugin.last_msg[user] > 30:
            self.plugin.xp[user] = self.plugin.xp.get(user, 0) + 10
            self.plugin.last_msg[user] = now
            return self.plugin.xp[user]
        return None
