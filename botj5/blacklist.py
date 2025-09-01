import re

class BlacklistHandler:
    def __init__(self, plugin):
        self.plugin = plugin

    async def check_blacklist(self, room_id, user, body, evt):
        for pattern in self.plugin.BLACKLIST:
            if re.search(pattern, body, flags=re.IGNORECASE):
                await self.plugin._safe_redact(room_id, evt.event_id, "Blacklisted content")
                await evt.reply(f"⚠️ {user}, your message was removed (blacklisted).")
                return True
        return False
