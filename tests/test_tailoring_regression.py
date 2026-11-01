"""Regression cases for a generic frontend JD and source-evidence retention."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from resume_core import (
    SkillCategory,
    build_tailoring_coverage_report,
    default_tailoring_plan,
    parse_resume_markdown,
    validate_tailoring_response,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class EvidenceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.profile = parse_resume_markdown(
            (FIXTURES / 'frontend_original.md').read_text()
        )
        self.jd = (FIXTURES / 'frontend_jd.txt').read_text()
        self.response = json.loads((FIXTURES / 'frontend_degraded.json').read_text())

    def validate(self, profile=None, jd=None):
        return validate_tailoring_response(
            json.dumps(self.response), profile or self.profile,
            self.jd if jd is None else jd,
        )

    def test_original_evidence_survives_degraded_candidate(self):
        plan = self.validate()
        text = ' '.join(b.text for r in plan.experience for b in r.bullets)
        for phrase in (
            'session handling', 'route-level application loading',
            'isolated builds and deployments', 'shared dependency controls',
            'improve startup performance', 'contain runtime failures',
            'into responsive interfaces', 'keeping the customer journey responsive',
            '2,000+', '40%', '75%', '35%',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        skills = {s for group in plan.skills for s in group.items}
        self.assertTrue({'Tailwind CSS', 'Zustand', 'HTML5', 'CSS3'} <= skills)
        self.assertEqual(plan.summary, self.profile.summary)
        self.assertEqual(len(plan.experience), 4)
        self.assertEqual(len(plan.experience[0].bullets), 6)
        self.assertTrue(any('detail' in warning for warning in plan.warnings))
        report = build_tailoring_coverage_report(self.profile, plan, self.jd)
        self.assertNotIn('Supported evidence omitted:', report)
        self.assertIn('cross-functional collaboration', report)

    def test_omitted_relevant_fact_is_restored(self):
        self.response['experience'][0]['bullets'] = [
            b for b in self.response['experience'][0]['bullets']
            if b['source_fact_ids'] != ['r0f0']
        ]
        plan = self.validate()
        self.assertIn(0, plan.experience[0].bullet_indices)
        self.assertIn('session handling', ' '.join(b.text for b in plan.experience[0].bullets))

    def test_long_relevant_original_is_accepted(self):
        self.response['experience'][0]['bullets'] = [{
            'text': self.profile.experience[0].bullets[0],
            'source_fact_ids': ['r0f0'],
        }]
        plan = self.validate()
        self.assertIn(self.profile.experience[0].bullets[0],
                      [b.text for b in plan.experience[0].bullets])

    def test_removing_only_project_label_is_allowed(self):
        original = self.profile.experience[0].bullets[0]
        rewrite = original.split(': ', 1)[1]
        self.response['experience'][0]['bullets'] = [{
            'text': rewrite, 'source_fact_ids': ['r0f0'],
        }]
        self.assertIn(rewrite, [b.text for b in self.validate().experience[0].bullets])

    def test_cross_role_citation_is_still_rejected(self):
        self.response['experience'][0]['bullets'] = [{
            'text': self.profile.experience[1].bullets[0],
            'source_fact_ids': ['r1f0'],
        }]
        self.assertTrue(all(
            fact_id.startswith('r0f')
            for bullet in self.validate().experience[0].bullets
            for fact_id in bullet.source_fact_ids
        ))

    def test_nine_required_skills_in_one_category_survive(self):
        jd = 'Required: React, Next.js, Vue.js, JavaScript, TypeScript, Zustand, HTML, CSS, Tailwind CSS.'
        skills = {s for group in self.validate(jd=jd).skills for s in group.items}
        self.assertTrue({'React', 'Next.js', 'Vue.js', 'JavaScript (ES6+)',
                         'TypeScript', 'Zustand', 'HTML5', 'CSS3', 'Tailwind CSS'} <= skills)

    def test_required_skills_can_exceed_total_target(self):
        items = tuple(f'Technology{i}' for i in range(26))
        profile = replace(self.profile, skills=(SkillCategory('Tools', items),))
        plan = default_tailoring_plan(profile, job_description=', '.join(items))
        self.assertEqual({s for g in plan.skills for s in g.items}, set(items))

    def test_session_handling_and_wcag_do_not_prove_other_requirements(self):
        profile = replace(
            self.profile, summary='Software Engineer.', skills=(),
            experience=(replace(self.profile.experience[0], bullets=(
                'Built session handling for a login flow and implemented WCAG accessibility.',
            )),),
        )
        jd = 'Implement state management libraries and responsive UI.'
        plan = default_tailoring_plan(profile, job_description=jd)
        report = build_tailoring_coverage_report(profile, plan, jd)
        self.assertIn('Experience evidence (phrase matches): none explicitly identified', report)
        self.assertIn('Evidence gaps: state management, responsive UI', report)

    def test_skill_listing_does_not_count_as_experience(self):
        profile = replace(
            self.profile, summary='State management with Zustand.',
            skills=(SkillCategory('Frontend', ('Zustand',)),),
            experience=(replace(self.profile.experience[0], bullets=('Built a login form.',)),),
        )
        jd = 'Experience implementing state management libraries.'
        report = build_tailoring_coverage_report(
            profile, default_tailoring_plan(profile, job_description=jd), jd,
        )
        self.assertIn('Matched JD skills (listed): Zustand', report)
        self.assertIn('Experience evidence (phrase matches): none explicitly identified', report)
        self.assertIn('Evidence gaps: state management', report)

    def test_report_exposes_loss_even_when_another_fact_matches_theme(self):
        plan = default_tailoring_plan(self.profile, job_description=self.jd)
        plan = replace(plan, experience=(replace(plan.experience[0], bullets=tuple(
            b for b in plan.experience[0].bullets if 'r0f0' not in b.source_fact_ids
        )), *plan.experience[1:]))
        report = build_tailoring_coverage_report(self.profile, plan, self.jd)
        self.assertIn('Source notes to review: 1', report)
        self.assertIn('Microfrontend Platform:', report)
        self.assertNotIn('r0f0', report)


if __name__ == '__main__':
    unittest.main()
