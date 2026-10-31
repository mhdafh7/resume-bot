import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot


class SetupStorageErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_education_write_permission_error_gets_a_reply(self) -> None:
        setup = {
            "name": "Test Person",
            "email": "test@example.com",
            "phone": "+1 555 0100",
            "website": "",
            "linkedin": "",
            "summary": "Software engineer building reliable applications.",
            "experience": (
                "Software Engineer | 2024 - Present | Example Co | Remote\n"
                "- Built and maintained production applications."
            ),
            "skills": "Engineering: Python, Testing",
        }
        message = SimpleNamespace(
            text="Example University | Remote | BSc Computer Science | 2020 - 2024",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={"setup": setup})

        denied_path = SimpleNamespace(
            write_text=Mock(side_effect=PermissionError(13, "Permission denied"))
        )
        with patch.object(bot, "RESUME_DATA_PATH", denied_path):
            state = await bot.setup_education(update, context)

        self.assertEqual(state, bot.SETUP_EDUCATION)
        message.reply_text.assert_awaited_once()
        self.assertIn("could not save", message.reply_text.await_args.args[0].lower())
        self.assertIn("setup", context.user_data)


if __name__ == "__main__":
    unittest.main()
