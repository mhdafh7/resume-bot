import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot


class BotCommandMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_supported_commands_for_slash_autocomplete(self) -> None:
        telegram_bot = SimpleNamespace(set_my_commands=AsyncMock())
        application = SimpleNamespace(bot=telegram_bot)

        await bot.register_bot_commands(application)

        telegram_bot.set_my_commands.assert_awaited_once_with(bot.BOT_COMMANDS)
        commands = {entry.command: entry.description for entry in bot.BOT_COMMANDS}
        self.assertIn("start", commands)
        self.assertIn("setup", commands)
        self.assertIn("setresume", commands)
        self.assertIn("showresume", commands)
        self.assertIn("exportdata", commands)
        self.assertIn("help", commands)
        self.assertIn("cancel", commands)
        self.assertIn("deletedata", commands)


if __name__ == "__main__":
    unittest.main()
