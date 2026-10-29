import json
import unittest
from pathlib import Path

from resume_core import (
    ResumeFormatError,
    default_tailoring_plan,
    latex_escape,
    parse_resume_markdown,
    profile_to_markdown,
    render_experience,
    render_heading,
    render_skills,
    validate_tailoring_response,
)


ROOT = Path(__file__).parents[1]
SAMPLE_MARKDOWN = (ROOT / "resume_data.md.example").read_text(encoding="utf-8")


class ResumeParsingTests(unittest.TestCase):
    def test_parses_the_documented_example(self) -> None:
        profile = parse_resume_markdown(SAMPLE_MARKDOWN)

        self.assertEqual(profile.name, "Muhammed Aflah C P")
        self.assertEqual(len(profile.experience), 4)
        self.assertEqual(len(profile.skills), 5)
        self.assertEqual(len(profile.education), 1)
        self.assertEqual(profile.experience[0].company, "Hatio Innovations - BillDesk subsidiary")

    def test_profile_round_trips_through_canonical_markdown(self) -> None:
        original = parse_resume_markdown(SAMPLE_MARKDOWN)
        reparsed = parse_resume_markdown(profile_to_markdown(original))

        self.assertEqual(reparsed, original)

    def test_rejects_experience_without_bullets(self) -> None:
        markdown = SAMPLE_MARKDOWN.replace(
            "- Built custom JavaScript plugins and modular, accessible UI patterns for "
            "internal admin dashboards, building clean frontend controls from wireframe "
            "schematics and optimizing complex form state flows.",
            "",
        )

        with self.assertRaisesRegex(ResumeFormatError, "at least one bullet"):
            parse_resume_markdown(markdown)

    def test_rejects_missing_required_sections(self) -> None:
        with self.assertRaisesRegex(ResumeFormatError, "Summary"):
            parse_resume_markdown("# Person\n\n## Contact\n- email: p@example.com")


class LatexRenderingTests(unittest.TestCase):
    def test_escapes_every_latex_control_character_once(self) -> None:
        escaped = latex_escape("A&B_%$#{x}\\path^~")

        self.assertEqual(
            escaped,
            r"A\&B\_\%\$\#\{x\}\textbackslash{}path\textasciicircum{}\textasciitilde{}",
        )

    def test_heading_escapes_contact_data(self) -> None:
        profile = parse_resume_markdown(
            SAMPLE_MARKDOWN.replace("Muhammed Aflah C P", "Person {R&D}", 1)
        )

        heading = render_heading(profile)

        self.assertIn(r"Person \{R\&D\}", heading)
        self.assertNotIn("Person {R&D}", heading)

    def test_default_rendering_uses_only_source_content(self) -> None:
        profile = parse_resume_markdown(SAMPLE_MARKDOWN)
        plan = default_tailoring_plan(profile)

        experience = render_experience(profile, plan)
        skills = render_skills(plan)

        self.assertIn("Full Stack Engineer", experience)
        self.assertIn(r"75\%", experience)
        self.assertIn("React", skills)


class TailoringValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = parse_resume_markdown(SAMPLE_MARKDOWN)

    def test_accepts_exact_source_selections_and_preserves_every_role(self) -> None:
        response = json.dumps(
            {
                "summary": "Full Stack Engineer with 3+ years of professional experience.",
                "experience": [{"role_index": 0, "bullet_indices": [6, 0]}],
                "skills": [
                    {"category": "Frontend Ecosystem", "items": ["React", "TypeScript"]}
                ],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(plan.experience[0].bullet_indices, (6, 0))
        self.assertEqual(len(plan.experience), len(self.profile.experience))
        self.assertEqual(plan.skills[0].items, ("React", "TypeScript"))
        self.assertFalse(plan.warnings)

    def test_rejects_invented_skills_and_invalid_bullet_indices(self) -> None:
        response = json.dumps(
            {
                "summary": self.profile.summary,
                "experience": [{"role_index": 0, "bullet_indices": [999]}],
                "skills": [{"category": "Frontend Ecosystem", "items": ["Rust"]}],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(
            plan.experience[0].bullet_indices,
            tuple(range(len(self.profile.experience[0].bullets))),
        )
        self.assertNotIn("Rust", {item for group in plan.skills for item in group.items})
        self.assertTrue(plan.warnings)

    def test_rejects_summary_with_an_unsupported_number(self) -> None:
        response = json.dumps(
            {
                "summary": "Improved every system by 99%.",
                "experience": [],
                "skills": [],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(plan.summary, self.profile.summary)
        self.assertIn("unsupported numeric claims", plan.warnings[0])

    def test_invalid_json_falls_back_to_the_complete_source_profile(self) -> None:
        plan = validate_tailoring_response("not-json", self.profile)

        self.assertEqual(plan.summary, self.profile.summary)
        self.assertEqual(len(plan.experience), len(self.profile.experience))
        self.assertEqual(len(plan.skills), len(self.profile.skills))
        self.assertTrue(plan.warnings)


if __name__ == "__main__":
    unittest.main()
