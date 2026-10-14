import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from sentinel.bot import Sentinel, reply, hierarchy, role_safe
from sentinel.db import Database
from sentinel.settings import Settings
from sentinel.domain import RuleError


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.bot = Sentinel(self.db, Settings(demo=True))

    async def asyncTearDown(self):
        await self.bot.close()
        self.db.close()

    async def test_every_registered_command_serializes_for_discord(self):
        counts = {1:0, 2:0, 3:0}
        for command in self.bot.tree.get_commands():
            data = command.to_dict(self.bot.tree)
            self.assertTrue(data["name"])
            counts[data.get("type", 1)] += 1
            if hasattr(command, "commands"):
                self.assertLessEqual(len(command.commands), 25)
        self.assertLessEqual(counts[1], 100)
        self.assertLessEqual(counts[3], 5)
        self.assertIn("alliances", self.bot.command_modules.values())

    async def test_template_reply_handles_content_without_duplicate_argument(self):
        i = SimpleNamespace(response=SimpleNamespace(is_done=lambda:False,send_message=AsyncMock()))
        await reply(i, content="Hello", embed=discord.Embed(title="Test"))
        self.assertEqual(i.response.send_message.await_args.kwargs["content"], "Hello")

    async def test_hierarchy_blocks_self_owner_and_peer(self):
        guild = SimpleNamespace(owner_id=1, me=SimpleNamespace(id=99, top_role=9))
        actor = SimpleNamespace(id=2, top_role=5)
        for uid, rank in [(2,1),(1,1),(3,5),(4,6),(99,1)]:
            with self.assertRaises(RuleError): hierarchy(guild, actor, SimpleNamespace(id=uid,top_role=rank))
        hierarchy(guild, actor, SimpleNamespace(id=3, top_role=4))

    async def test_unsafe_role_assignment_is_rejected(self):
        class Role:
            managed=False
            def is_default(self):return False
            def __ge__(self,other):return False
        role = Role()
        role.permissions=discord.Permissions(administrator=True)
        with self.assertRaises(RuleError): role_safe(role,SimpleNamespace(me=SimpleNamespace(top_role=100)))


if __name__ == "__main__":
    unittest.main()
