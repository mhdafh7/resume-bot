"""Keep related cited facts in coherent achievements instead of standalone fragments."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from resume_core import parse_resume_markdown, validate_tailoring_response


class FactGroupingTests(unittest.TestCase):
    def test_restore_combined_citations_without_splitting_achievement(self):
        profile = parse_resume_markdown(
            (Path(__file__).parent / 'fixtures/frontend_original.md').read_text()
        )
        role = replace(profile.experience[1], bullets=(
            'Built a browser extension.',
            'Used Node.js and Express synchronization to maintain consistent real-time state across browser contexts.',
        ))
        profile = replace(profile, experience=(role,))
        response = json.dumps({
            'summary': profile.summary,
            'summary_source_fact_ids': ['s0'],
            'experience': [{'role_index': 0, 'bullets': [{
                'text': 'Built a browser extension using Node.js and Express synchronization to maintain consistent real-time state across browser contexts.',
                'source_fact_ids': ['r0f0', 'r0f1'],
            }]}],
            'skills': [{'category': 'Backend & Data', 'items': ['Node.js', 'Express.js']}],
        })
        plan = validate_tailoring_response(response, profile, 'Design, develop, test, and maintain backend applications, services, and RESTful APIs using Java, ReactJS and Spring Boot.')
        bullets = plan.experience[0].bullets
        self.assertEqual(len(bullets), 1)
        self.assertEqual(set(bullets[0].source_fact_ids), {'r0f0', 'r0f1'})
        self.assertIn('browser extension', bullets[0].text)
        self.assertIn('consistent real-time state', bullets[0].text)
        self.assertNotIn('Built a browser extension.', [bullet.text for bullet in bullets])
