import unittest
from pathlib import Path

import bot


ROOT = Path(__file__).parents[1]


class ResumeFilenameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = bot.parse_resume_markdown(
            (ROOT / "resume_data.md.example").read_text(encoding="utf-8")
        )

    def test_does_not_treat_a_word_after_for_as_the_company(self) -> None:
        job_description = (
            "Build tooling for OpenAPI-generated SDKs with TypeScript and React."
        )

        filename = bot._build_resume_filename(self.profile, job_description)

        self.assertEqual(filename, "Muhammed_Aflah_C_P.pdf")

    def test_uses_explicit_company_and_job_title_labels(self) -> None:
        job_description = (
            "Company: Example Labs\n"
            "Job Title: Senior Frontend Engineer\n\n"
            "Build accessible React applications."
        )

        filename = bot._build_resume_filename(self.profile, job_description)

        self.assertEqual(
            filename,
            "Muhammed_Aflah_C_P_Example_Labs_Senior_Frontend_Engineer_Resume.pdf",
        )

    def test_uses_an_explicit_role_at_company_heading(self) -> None:
        filename = bot._build_resume_filename(
            self.profile,
            "Senior Software Engineer at Acme Corp\n\nAbout the role...",
        )

        self.assertEqual(
            filename,
            "Muhammed_Aflah_C_P_Acme_Corp_Senior_Software_Engineer_Resume.pdf",
        )

    def test_uses_explicit_company_without_guessing_a_role(self) -> None:
        filename = bot._build_resume_filename(
            self.profile,
            "Acme Corp is hiring engineers to build payment infrastructure.",
        )

        self.assertEqual(filename, "Muhammed_Aflah_C_P_Acme_Corp_Resume.pdf")


if __name__ == "__main__":
    unittest.main()
