from mautrix.types import EventType, MembershipEvent

class AutoRoleHandler:
    """
    Automatically assigns a default role (power level) to new users
    when they join a room.
    """

    DEFAULT_POWER = 0  # Default "Member" power level

    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_member_join(self, evt: MembershipEvent):
        # Only handle new joins
        if evt.membership != "join":
            return

        user = evt.state_key
        room_id = evt.room_id

        # Don't assign role to the bot itself
        if user == self.plugin.client.mxid:
            return

        await self.set_user_power(room_id, user, self.DEFAULT_POWER)

    async def set_user_power(self, room_id: str, user: str, power: int):
        """
        Safely sets the power level of a user in a room.
        """
        try:
            content = await self.plugin._get_power_levels(room_id)
            users = content.get("users", {})
            users[user] = power
            content["users"] = users
            await self.plugin._set_power_levels(room_id, content)
        except Exception as e:
            self.plugin.log.warning(f"Failed to assign default role to {user} in {room_id}: {e}")
