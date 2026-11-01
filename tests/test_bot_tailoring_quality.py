"""Exercise the generation boundary with separate atomic notes, not pre-grouped input."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

import bot
from test_fullstack_tailoring import atomic_profile, JD
from resume_core import TailoringQualityError


def response_for(profile, grouped=False):
    roles = []
    for index, role in enumerate(profile.experience):
        groups = ([[0, 1], [2, 3, 4], [5]] if index == 0 else [[0, 1]]) if grouped else [[i] for i in range(len(role.bullets))]
        roles.append({'role_index': index, 'bullets': [{
            'text': ' '.join(role.bullets[i] for i in group),
            'source_fact_ids': [f'r{index}f{i}' for i in group],
        } for group in groups]})
    if grouped:
        roles[1]['bullets'][0]['text'] = (
            'Built a browser extension using Node.js and Express synchronization '
            'to maintain consistent real-time state across browser contexts.'
        )
    return json.dumps({
        'summary': profile.summary, 'summary_source_fact_ids': ['s0'],
        'experience': roles,
        'skills': [{'category': group.name, 'items': list(group.items)} for group in profile.skills],
    })


class TailoringQualityTests(unittest.TestCase):
    def test_atomic_bullets_trigger_a_cited_rewrite_before_returning_plan(self):
        profile = atomic_profile()
        generate = Mock(side_effect=[
            SimpleNamespace(text=response_for(profile)),
            SimpleNamespace(text=response_for(profile, grouped=True)),
        ])
        plan = bot.tailor_with_llm(SimpleNamespace(models=SimpleNamespace(generate_content=generate)), profile, JD)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(len(plan.experience[1].bullets), 1)
        self.assertEqual(len(plan.experience[0].bullets), 3)
        self.assertIn('Spring Boot', plan.experience[0].bullets[0].text)
        self.assertIn('consistent real-time state', plan.experience[1].bullets[0].text)
        self.assertIn('browser extension using Node.js', plan.experience[1].bullets[0].text)
        repair_prompt = generate.call_args.kwargs['contents']
        self.assertIn('Built a browser extension', repair_prompt)
        self.assertIn('source_fact_ids', repair_prompt)

    def test_repeated_fragments_are_not_silently_delivered(self):
        profile = atomic_profile()
        generate = Mock(return_value=SimpleNamespace(text=response_for(profile)))
        with self.assertRaisesRegex(TailoringQualityError, 'complete.*achievement'):
            bot.tailor_with_llm(SimpleNamespace(models=SimpleNamespace(generate_content=generate)), profile, JD)
        self.assertEqual(generate.call_count, 2)

    def test_invalid_json_fallback_with_atomic_notes_is_also_repaired(self):
        profile = atomic_profile()
        generate = Mock(side_effect=[
            SimpleNamespace(text='not-json'),
            SimpleNamespace(text=response_for(profile, grouped=True)),
        ])
        plan = bot.tailor_with_llm(SimpleNamespace(models=SimpleNamespace(generate_content=generate)), profile, JD)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(len(plan.experience[1].bullets), 1)

    def test_complete_achievements_do_not_trigger_an_extra_model_call(self):
        profile = atomic_profile()
        generate = Mock(return_value=SimpleNamespace(text=response_for(profile, grouped=True)))
        bot.tailor_with_llm(SimpleNamespace(models=SimpleNamespace(generate_content=generate)), profile, JD)
        generate.assert_called_once()

    def test_repair_cannot_bypass_provenance_checks(self):
        profile = atomic_profile()
        repaired = json.loads(response_for(profile, grouped=True))
        repaired['experience'][1]['bullets'][0]['text'] += ' Improved revenue by 99%.'
        generate = Mock(side_effect=[
            SimpleNamespace(text=response_for(profile)),
            SimpleNamespace(text=json.dumps(repaired)),
        ])
        with self.assertRaises(TailoringQualityError):
            bot.tailor_with_llm(SimpleNamespace(models=SimpleNamespace(generate_content=generate)), profile, JD)
        self.assertEqual(generate.call_count, 2)


class QualityFailureDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_repair_explains_problem_without_sending_pdf(self):
        status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        message = SimpleNamespace(text=JD, reply_text=AsyncMock(return_value=status), reply_document=AsyncMock())
        context = SimpleNamespace(application=SimpleNamespace(bot_data={'genai_client': object()}))
        with patch.object(bot, '_reject_unless_authorised', AsyncMock(return_value=False)), \
             patch.object(bot, 'load_profile', return_value=atomic_profile()), \
             patch.object(bot, 'tailor_with_llm', side_effect=TailoringQualityError('complete achievements')), \
             patch.object(bot, 'compile_latex') as compile_pdf:
            await bot.handle_jd(SimpleNamespace(message=message), context)
        message.reply_document.assert_not_awaited()
        compile_pdf.assert_not_called()
        self.assertIn('no PDF was sent', status.edit_text.await_args.args[0])
