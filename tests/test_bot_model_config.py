import unittest
from pathlib import Path
from types import SimpleNamespace

import bot


class ModelConfigurationTests(unittest.TestCase):
    def test_default_model_is_available_to_new_gemini_users(self) -> None:
        self.assertEqual(bot.DEFAULT_MODEL_ID, "gemini-3.8-flash")

    def test_gemini_3_request_omits_deprecated_temperature(self) -> None:
        class RecordingModels:
            def __init__(self) -> None:
                self.config = None

            def generate_content(self, **kwargs):
                self.config = kwargs["config"]
                return SimpleNamespace(text="not-json")

        models = RecordingModels()
        client = SimpleNamespace(models=models)
        sample = Path("resume_data.md.example").read_text(encoding="utf-8")
        profile = bot.parse_resume_markdown(sample)

        bot.tailor_with_llm(client, profile, "A sufficiently detailed job description")

        self.assertIsNone(models.config.temperature)
        self.assertEqual(models.config.response_mime_type, "application/json")
        self.assertIn("source_fact_ids", str(models.config.response_json_schema))


if __name__ == "__main__":
    unittest.main()
