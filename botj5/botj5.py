from maubot import Plugin
from maubot.handlers import event, command
from mautrix.types import EventType, MessageEvent
import time
from spam import SpamHandler
from blacklist import BlacklistHandler
from autorole import AutoRoleHandler


class Mee6(Plugin):
    SPAM_WARNING = 5
    SPAM_INTERVAL = 10
    MUTE_DURATION = 30
    WARNING_COOLDOWN = 300

    BLACKLIST = [
        r"\b(badword1|badword2|scam)\b",        
        r"http[s]?://(?:[^\s]+)"
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xp = {}
        self.last_msg = {}
        self.room_pl_capable = {}

        # Initialize handlers
        self.spam_handler = SpamHandler(self)
        self.blacklist_handler = BlacklistHandler(self)
        self.autorole_handler = AutoRoleHandler(self)

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent):
        ...
        # (same code as before)

    @event.on(EventType.ROOM_MEMBER)
    async def on_member_join(self, evt):
        await self.autorole_handler.handle_member_join(evt)



    # ---------- Power level / utilities / commands ----------
    # Keep your existing _get_power_levels, _set_power_levels,
    # _mute_user, _restore_user_power, _safe_redact, !level, !unmute
    # from your current j5chat.py
