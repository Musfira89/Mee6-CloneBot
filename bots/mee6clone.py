from maubot import Plugin, MessageEvent
from maubot.handlers import event, command
import time

class Mee6Clone(Plugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xp = {}  
        self.last_msg = {}

    @event.on(MessageEvent)
    async def on_message(self, evt: MessageEvent) -> None:
        if evt.sender == self.client.mxid:
            return

        user = evt.sender
        now = time.time()

        # Basic XP system
        if user not in self.last_msg or now - self.last_msg[user] > 30:
            self.xp[user] = self.xp.get(user, 0) + 10
            self.last_msg[user] = now
            await evt.respond(f"{user} gained 10 XP! Total: {self.xp[user]}")

    @command.new("level")
    async def level(self, evt: MessageEvent) -> None:
        user = evt.sender
        xp = self.xp.get(user, 0)
        await evt.respond(f"{user}, you have {xp} XP.")
