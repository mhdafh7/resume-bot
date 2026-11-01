"""Regressions for atomized source notes and a backend-heavy fullstack JD."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from resume_core import (
    SkillCategory, build_tailoring_coverage_report, default_tailoring_plan,
    parse_resume_markdown, validate_tailoring_response, tailoring_quality_issues,
    TailoredBullet, ExperienceSelection, TailoringPlan,
)

FIXTURES = Path(__file__).parent / 'fixtures'
JD = (FIXTURES / 'fullstack_jd.txt').read_text()


def atomic_profile():
    profile = parse_resume_markdown((FIXTURES / 'frontend_original.md').read_text())
    return replace(profile, experience=(replace(profile.experience[0], bullets=(
        'Architected an enterprise Design Language System and reusable component library documented with Storybook.',
        'Built extensible components tested with Vitest and Playwright, accelerating cross-team feature delivery by 40%.',
        'Built a Vite-powered microfrontend host that unified independently owned applications behind single sign-on.',
        'Created reusable UI foundations while preserving isolated builds and deployments for each team.',
        'Added lazy loading, shared-dependency controls, and error boundaries to improve performance and resilience.',
        'Contributed to a high-throughput Spring Boot, Kafka, and Aerospike pipeline enabling low-latency BIN metadata lookups.',
    )), replace(profile.experience[1], bullets=(
        'Built a browser extension.',
        'Used Node.js and Express synchronization to maintain consistent real-time state across browser contexts.',
    ))), skills=(
        SkillCategory('Frontend', ('React', 'TypeScript')),
        SkillCategory('Backend & Data', ('Java 17', 'Spring Boot', 'REST APIs', 'PostgreSQL')),
        SkillCategory('Tooling', ('Git', 'GitLab CI/CD')),
    ))


class FullstackRankingTests(unittest.TestCase):
    def test_short_complete_achievements_are_not_activity_fragments(self):
        for text in (
            'Reduced build times by 75%.',
            'Implemented OAuth2 authentication using Spring Security.',
            'Automated invoice approvals for the finance team.',
        ):
            with self.subTest(text=text):
                plan = TailoringPlan('', (ExperienceSelection(0, (
                    TailoredBullet(text, ('r0f0',)),
                )),), ())
                self.assertFalse(tailoring_quality_issues(plan))

    def test_direct_backend_requirement_precedes_generic_frontend_themes(self):
        plan = default_tailoring_plan(atomic_profile(), job_description=JD)
        self.assertIn('Spring Boot', plan.experience[0].bullets[0].text)

    def test_skill_aliases_are_retained_and_reported(self):
        profile = atomic_profile()
        response = json.dumps({
            'summary': profile.summary, 'summary_source_fact_ids': ['s0'],
            'experience': [],
            'skills': [{'category': 'Frontend', 'items': ['TypeScript']}],
        })
        plan = validate_tailoring_response(response, profile, JD)
        selected = {s for g in plan.skills for s in g.items}
        self.assertTrue({'Java 17', 'React', 'REST APIs', 'GitLab CI/CD'} <= selected)
        report = build_tailoring_coverage_report(profile, plan, JD)
        listed = next(line for line in report.splitlines() if line.startswith('Matched JD skills'))
        for skill in ('Java 17', 'React', 'REST APIs', 'GitLab CI/CD'):
            self.assertIn(skill, listed)

    def test_alias_does_not_allow_cross_fact_technology_claims(self):
        profile = atomic_profile()
        response = json.dumps({
            'summary': profile.summary, 'summary_source_fact_ids': ['s0'],
            'experience': [{'role_index': 1, 'bullets': [{
                'text': 'Built a ReactJS browser extension with synchronized state across browser contexts.',
                'source_fact_ids': ['r1f0', 'r1f1'],
            }]}],
            'skills': [],
        })
        plan = validate_tailoring_response(response, profile, JD)
        self.assertNotIn('ReactJS', ' '.join(b.text for b in plan.experience[1].bullets))

    def test_technology_alternatives_are_not_reported_as_individual_gaps(self):
        profile = atomic_profile()
        report = build_tailoring_coverage_report(
            profile, default_tailoring_plan(profile, job_description=JD), JD,
        )
        gaps = next(line for line in report.splitlines() if line.startswith('Evidence gaps:'))
        for satisfied_alternative in ('python', 'kotlin', 'django', 'fastapi'):
            self.assertNotIn(satisfied_alternative, gaps)
        self.assertIn('sql', gaps)

    def test_separately_required_alternative_still_reports_a_gap(self):
        profile = atomic_profile()
        jd = 'Java or Python. Python is required for data processing.'
        report = build_tailoring_coverage_report(
            profile, default_tailoring_plan(profile, job_description=jd), jd,
        )
        self.assertIn('python', report.split('Evidence gaps:')[1])

    def test_unfulfilled_alternatives_are_still_reported(self):
        profile = atomic_profile()
        jd = 'Kotlin or Python and Django or FastAPI.'
        report = build_tailoring_coverage_report(
            profile, default_tailoring_plan(profile, job_description=jd), jd,
        )
        gaps = report.split('Evidence gaps:')[1]
        for term in ('kotlin', 'python', 'django', 'fastapi'):
            self.assertIn(term, gaps)
