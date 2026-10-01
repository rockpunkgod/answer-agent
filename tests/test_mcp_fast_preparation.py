"""Fast upload/source review/generation contract; fake desktop only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation_review import PreparationReviewError
from tests.test_mcp_preparation import URL, SequentialDesktop
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from tests.test_mcp_generation import Transport, URL as GENERATION_URL
from tests import test_mcp_preparation_text as text_tests


class FastPreparationTests(unittest.TestCase):
    setUp = text_tests.TextPreparationTests.setUp
    tearDown = text_tests.TextPreparationTests.tearDown
    candidate = text_tests.TextPreparationTests.candidate
    review = text_tests.TextPreparationTests.review
    approve = text_tests.TextPreparationTests.approve

    def fast_candidate(self):
        self.controls['preparation_mode'] = 'FAST_UPLOAD_THEN_GENERATE'
        self.controls['material_order'] = 'COURSE_THEN_QUESTION'
        self.snapshot['session_id'] = 'independent-fast-session'
        return self.candidate()

    def test_fast_upload_then_source_review_can_generate_with_one_submit(self):
        fake, candidate = self.fast_candidate()
        self.assertEqual(fake.uploaded, [self.course['name'], candidate['files'][-1]['name']])
        self.assertFalse(any(t == 'Shortcut' and a.get('shortcut') == 'enter' for t, a in fake.calls))
        self.assertEqual(candidate['material_stages'], [])
        self.assertFalse(candidate['model_readback_performed'])
        self.assertFalse(candidate['operator_verified'])
        self.assertNotIn('course_readback_excerpts', candidate)
        approved = self.approve(self.review())
        self.assertEqual(approved['status'], 'ATTACHMENTS_READY_SOURCE_REVIEWED')
        self.assertFalse(approved['model_readback_performed'])
        self.assertNotIn('readback_evidence', approved)
        self.assertNotIn('course_readback_excerpts', approved)
        class MatchingTransport(Transport):
            def call(self, tool, arguments):
                value = super().call(tool, arguments)
                for item in value.get('content', []):
                    if item.get('type') == 'text':
                        item['text'] = item['text'].replace(GENERATION_URL, URL)
                return value
        transport = MatchingTransport()
        result = PreparedDeepSeekGenerator(transport, self.output, self.base / 'generation',
                                           poll_interval=0, timeout=2).generate(self.snapshot)
        self.assertEqual(result['correct_option_id'], 'option-A')
        self.assertEqual(transport.calls.count('Shortcut'), 1)
        self.assertIn('先实际读取本会话固定版本的ANSWER教学Skill附件', transport.prompt)
        self.assertIn('同一次生成', transport.prompt)
        self.assertFalse(result['simulated'])
        self.assertFalse(result['model_readback_performed'])
        self.assertEqual(result['preparation_contract'], 'ATTACHMENTS_READY_SOURCE_REVIEWED')

    def test_old_strict_two_stage_source_review_remains_usable(self):
        self.controls['material_order'] = 'COURSE_THEN_QUESTION'
        fake = SequentialDesktop([self.course])
        record = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.candidate_path,
                                        self.controls, poll_interval=0, timeout=2).run()
        self.assertEqual(len(fake.submission_uploads), 2)
        self.assertTrue(record['course_stage_verified'])
        approved = self.approve(self.review())
        self.assertEqual(approved['status'], 'OPERATOR_VERIFIED_UPLOAD_AND_INPUT')
        PreparedDeepSeekGenerator(None, self.output, self.base)._preparation(self.snapshot)

    def test_unreviewed_fast_candidate_blocks_before_transport(self):
        self.fast_candidate()
        transport = Mock()
        with self.assertRaisesRegex(ValueError, 'INDEPENDENT_SOURCE_REVIEW_REQUIRED'):
            PreparedDeepSeekGenerator(transport, self.candidate_path, self.base / 'generation').generate(self.snapshot)
        transport.call.assert_not_called()
        self.assertFalse((self.base / 'generation').exists())

    def test_fast_still_requires_frozen_source_review(self):
        self.fast_candidate()
        for patch in ({'source_verified_excerpts': {}}, {'reviewed_question_text': {}},
                      {'source_review_evidence': ''}, {'reviewer': ''},
                      {'verified_question_stem': 'An unrelated question stem'}):
            with self.subTest(patch=patch), self.assertRaises(PreparationReviewError):
                self.approve(self.review() | patch)
            self.assertFalse(self.output.exists())

    def test_ready_snapshot_upload_unknown_and_changed_source_fail_closed(self):
        _, candidate = self.fast_candidate()
        original = self.candidate_path.read_bytes()
        for mutation in ('missing_attachment', 'uncertain_upload'):
            altered = deepcopy(candidate)
            if mutation == 'missing_attachment':
                altered['readiness_snapshot']['content'][0]['text'] = altered['readiness_snapshot']['content'][0]['text'].replace(self.course['name'], 'missing.md')
            else:
                next(e for e in altered['events'] if e.get('intent') == 'submit file picker once')['status'] = 'OUTCOME_UNCONFIRMED'
            self.candidate_path.write_text(json.dumps(altered), encoding='utf-8')
            with self.subTest(mutation=mutation), self.assertRaises(PreparationReviewError):
                self.approve(self.review())
        self.candidate_path.write_bytes(original)
        self.approve(self.review())
        for path in (Path(self.course['path']), self.candidate_path, self.review_path, self.readback):
            content = path.read_bytes(); path.write_bytes(content + b'changed')
            transport = Mock()
            with self.subTest(path=path), self.assertRaises(ValueError):
                PreparedDeepSeekGenerator(transport, self.output, self.base / 'generation').generate(self.snapshot)
            transport.call.assert_not_called()
            path.write_bytes(content)


if __name__ == '__main__': unittest.main()
