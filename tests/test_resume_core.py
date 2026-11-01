import json
import unittest
from pathlib import Path

from resume_core import (
    ResumeFormatError,
    build_tailoring_coverage_report,
    build_tailoring_prompt,
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

    def _all_roles_with_first_fact(self) -> list[dict[str, object]]:
        return [
            {
                "role_index": role_index,
                "bullets": [
                    {
                        "text": role.bullets[0],
                        "source_fact_ids": [f"r{role_index}f0"],
                    }
                ],
            }
            for role_index, role in enumerate(self.profile.experience)
        ]

    def test_accepts_grounded_rewrites_and_preserves_every_role(self) -> None:
        rewritten = (
            "Reduced production runtime failures by 35% by instrumenting GitLab "
            "CI/CD, Sentry, and SonarQube health metrics."
        )
        response = json.dumps(
            {
                "summary": "Full Stack Engineer with 3+ years of professional experience.",
                "summary_source_fact_ids": ["s0"],
                "experience": [
                    {
                        "role_index": 0,
                        "bullets": [
                            {
                                "text": rewritten,
                                "source_fact_ids": ["r0f6"],
                            }
                        ],
                    },
                    *self._all_roles_with_first_fact()[1:],
                ],
                "skills": [
                    {"category": "Frontend Ecosystem", "items": ["React", "TypeScript"]}
                ],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(plan.experience[0].bullet_indices, (6,))
        self.assertEqual(plan.experience[0].bullets[0].text, rewritten)
        self.assertEqual(len(plan.experience), len(self.profile.experience))
        self.assertEqual(plan.skills[0].items, ("React", "TypeScript"))
        self.assertFalse(plan.warnings)

    def test_rejects_invented_skills_and_unsupported_rewrite_claims(self) -> None:
        response = json.dumps(
            {
                "summary": self.profile.summary,
                "summary_source_fact_ids": ["s0"],
                "experience": [
                    {
                        "role_index": 0,
                        "bullets": [
                            {
                                "text": "Scaled Rust services to 99 million users.",
                                "source_fact_ids": ["r0f0"],
                            }
                        ],
                    }
                ],
                "skills": [{"category": "Frontend Ecosystem", "items": ["Rust"]}],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertLessEqual(len(plan.experience[0].bullets), 6)
        self.assertNotIn("99 million", plan.experience[0].bullets[0].text)
        self.assertNotIn("Rust", {item for group in plan.skills for item in group.items})
        self.assertTrue(plan.warnings)

    def test_rejects_a_job_keyword_absent_from_the_cited_role_fact(self) -> None:
        response = json.dumps(
            {
                "summary": self.profile.summary,
                "summary_source_fact_ids": ["s0"],
                "experience": [
                    {
                        "role_index": 0,
                        "bullets": [
                            {
                                "text": "Built Kubernetes applications for a scalable network.",
                                "source_fact_ids": ["r0f0"],
                            }
                        ],
                    }
                ],
                "skills": [
                    {"category": "Frontend Ecosystem", "items": ["React"]}
                ],
            }
        )

        plan = validate_tailoring_response(
            response, self.profile, "Build Kubernetes applications at scale."
        )

        self.assertNotIn("Kubernetes", plan.experience[0].bullets[0].text)
        self.assertTrue(plan.warnings)

    def test_rejects_summary_with_an_unsupported_number(self) -> None:
        response = json.dumps(
            {
                "summary": "Improved every system by 99%.",
                "summary_source_fact_ids": ["s0"],
                "experience": [],
                "skills": [],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(plan.summary, self.profile.summary)
        self.assertIn("unsupported claims", plan.warnings[0])

    def test_rejects_an_uncited_summary(self) -> None:
        response = json.dumps(
            {
                "summary": "Frontend engineer specializing in healthcare systems.",
                "experience": self._all_roles_with_first_fact(),
                "skills": [],
            }
        )

        plan = validate_tailoring_response(response, self.profile)

        self.assertEqual(plan.summary, self.profile.summary)
        self.assertEqual(plan.summary_source_fact_ids, ("s0",))
        self.assertIn("missing valid citations", plan.warnings[0])

    def test_keeps_every_source_backed_skill_explicitly_requested_by_the_jd(self) -> None:
        profile = parse_resume_markdown(
            SAMPLE_MARKDOWN.replace(
                "HTML5, CSS3, DOM Architecture",
                "HTML5, CSS3, DOM Architecture, Zustand",
            ).replace(
                "Web Accessibility (WCAG), Responsive Design",
                "Web Accessibility (WCAG), Responsive Design, Web Performance",
            )
        )
        job_description = (
            "Develop responsive applications using JavaScript, React, HTML, and CSS. "
            "Implement state management solutions. Tailwind CSS and TypeScript are a plus. "
            "Build modular applications and optimize application performance."
        )
        response = json.dumps(
            {
                "summary": self.profile.summary,
                "summary_source_fact_ids": ["s0"],
                "experience": [
                    {
                        "role_index": role_index,
                        "bullets": [
                            {
                                "text": role.bullets[0],
                                "source_fact_ids": [f"r{role_index}f0"],
                            }
                        ],
                    }
                    for role_index, role in enumerate(profile.experience)
                ],
                "skills": [
                    {"category": "Frontend Ecosystem", "items": ["React"]}
                ],
            }
        )

        plan = validate_tailoring_response(response, profile, job_description)
        selected = {item for group in plan.skills for item in group.items}

        self.assertTrue(
            {
                "JavaScript (ES6+)",
                "React",
                "TypeScript",
                "HTML5",
                "CSS3",
                "Zustand",
                "Tailwind CSS",
                "Responsive Design",
                "Web Performance",
            }.issubset(selected)
        )

    def test_orders_valid_bullets_by_jd_coverage_then_impact(self) -> None:
        role = self.profile.experience[0]
        response = json.dumps(
            {
                "summary": self.profile.summary,
                "summary_source_fact_ids": ["s0"],
                "experience": [
                    {
                        "role_index": 0,
                        "bullets": [
                            {"text": role.bullets[6], "source_fact_ids": ["r0f6"]},
                            {"text": role.bullets[5], "source_fact_ids": ["r0f5"]},
                        ],
                    },
                    *self._all_roles_with_first_fact()[1:],
                ],
                "skills": [
                    {"category": "Frontend Ecosystem", "items": ["React"]}
                ],
            }
        )

        plan = validate_tailoring_response(
            response,
            self.profile,
            "Build React and TypeScript interfaces and optimize frontend performance.",
        )

        indices = plan.experience[0].bullet_indices
        self.assertIn(5, indices)
        # Other source facts can match both React and TypeScript more directly.
        # Observability must not displace the relevant migration achievement.
        if 6 in indices:
            self.assertLess(indices.index(5), indices.index(6))

    def test_invalid_json_uses_a_concise_relevance_ranked_fallback(self) -> None:
        plan = validate_tailoring_response(
            "not-json", self.profile, "React TypeScript accessibility frontend"
        )

        self.assertEqual(plan.summary, self.profile.summary)
        self.assertEqual(len(plan.experience), len(self.profile.experience))
        self.assertLessEqual(len(plan.experience[0].bullets), 6)
        self.assertTrue(
            all(len(role.bullets) <= 2 for role in plan.experience[1:])
        )
        self.assertLessEqual(sum(len(group.items) for group in plan.skills), 24)
        self.assertTrue(plan.warnings)

    def test_prompt_assigns_fact_ids_and_requests_provenance(self) -> None:
        prompt = build_tailoring_prompt(self.profile, "React engineer")

        self.assertIn('"fact_id": "r0f0"', prompt)
        self.assertIn('"fact_id": "s0"', prompt)
        self.assertIn("source_fact_ids", prompt)
        self.assertIn("summary_source_fact_ids", prompt)
        self.assertIn("supported requirement coverage", prompt)
        self.assertIn("Never add an outcome", prompt)

    def test_coverage_report_shows_matches_and_source_gaps(self) -> None:
        job_description = (
            "Build responsive React interfaces with Tailwind CSS and Redux. "
            "Own features from conception to deployment."
        )
        plan = default_tailoring_plan(self.profile, job_description=job_description)

        report = build_tailoring_coverage_report(
            self.profile, plan, job_description
        )

        self.assertIn("Matched JD skills", report)
        self.assertIn("React", report)
        self.assertIn("Tailwind CSS", report)
        self.assertIn("feature ownership", report)
        self.assertIn("redux", report)


if __name__ == "__main__":
    unittest.main()
