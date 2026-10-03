import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.mcp_page_contract import PageUnconfirmed


URL = 'https://chat.deepseek.com/a/chat/s/prepared-test'


def snapshot(tail):
    return {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text':
        'UI Tree:\ndesktop\n└── window "DeepSeek - Microsoft Edge"\n'
        f'    ├── (1,2) 文档 "DeepSeek" [value:"{URL}"]\n' + tail}]}


class Transport:
    def __init__(self, fail_submit=False):
        self.calls = []
        self.prompt = ''
        self.fail_submit = fail_submit

    def call(self, tool, arguments):
        self.calls.append(tool)
        if tool == 'Type':
            self.prompt = arguments['text']
            return {}
        if tool == 'Shortcut':
            if self.fail_submit:
                raise TimeoutError('uncertain submit')
            return {}
        if not self.prompt:
            return snapshot('    └── (100,200) 编辑 "给 DeepSeek 发送消息"\n')
        if 'Shortcut' not in self.calls:
            return snapshot(f'    └── (100,200) 编辑 "给 DeepSeek 发送消息" [focused] [value:"{self.prompt}"]\n')
        return snapshot('    ├── 按钮 "朗读"\n'
                        '    ├── text "BEGIN_run_1234567890123456"\n'
                        '    ├── text "course.md：A verified preparation excerpt."\n'
                        '    ├── text "END_run_1234567890123456"\n'
                        '    ├── text "BEGIN_answer_run_1234567890123456"\n'
                        '    ├── text "{"option_label":"A","text":"同学，我们来分析一下。选A。"}"\n'
                        '    └── text "END_answer_run_1234567890123456"\n')


class PreparedGenerationTests(unittest.TestCase):
    def setUp(self):
        # This fixture isolates the webpage contract. Real source rejection is
        # exercised without this mock in test_teaching_bundle/test_answer_teaching.
        source = patch('helpdesk.mcp_generation.verify_frozen_teaching', return_value={})
        source.start();self.addCleanup(source.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        course = self.base / 'course.md'
        excerpt = 'Only verified material can establish an answer.'
        course.write_text(excerpt, encoding='utf-8')
        proof = self.base / 'proof.json'
        proof.write_text(json.dumps(snapshot(f'    └── text "course.md {excerpt}"')), encoding='utf-8')
        self.context = {k: 'test' for k in ('case_id', 'question_id', 'question_version', 'student_material', 'student_words', 'intent')}
        self.context.update(context_revision=0, attachments=[], run_id='1234567890123456', session_id='db-session',
                            student_question={'options': [{'id': 'option-a', 'label': 'A'}]},
                            teaching_skills=[{'path': str(course), 'sha256': sha256(course.read_bytes()).hexdigest()}])
        self.prep = dict(status='OPERATOR_VERIFIED_UPLOAD_AND_INPUT', input_fingerprint=input_fingerprint(self.context),
                         session_url=URL, readback_evidence=str(proof), readback_sha256=sha256(proof.read_bytes()).hexdigest(),
                         uploaded_teaching_hashes={str(course): sha256(course.read_bytes()).hexdigest()},
                         course_readback_excerpts={str(course): excerpt})
        self.path = self.base / 'preparation.json'
        self.path.write_text(json.dumps(self.prep), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_captures_answer_once_and_records_real_mode_and_evidence(self):
        transport = Transport()
        generator = PreparedDeepSeekGenerator(transport, self.path, self.base/'runs')
        result = generator.generate(self.context)
        self.assertFalse(any(c in transport.prompt for c in '{}\r\n\t'))
        self.assertIn('BEGIN_answer_run_1234567890123456', transport.prompt)
        self.assertFalse(result['simulated'])
        self.assertEqual(result['correct_option_id'], 'option-a')
        self.assertEqual(result['session_id'], 'db-session')
        record = json.loads(Path(result['web_session_evidence']).read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'FINAL_OUTPUT_CAPTURED')
        with self.assertRaises(FileExistsError):
            generator.generate(self.context)
        self.assertEqual(transport.calls.count('Shortcut'), 1)

    def test_changed_context_or_teaching_rejected_before_desktop(self):
        transport = Transport()
        generator = PreparedDeepSeekGenerator(transport, self.path, self.base/'runs')
        changed = copy.deepcopy(self.context)
        changed['student_words'] = 'changed request'
        with self.assertRaisesRegex(ValueError, 'context changed'):
            generator.generate(changed)
        Path(self.context['teaching_skills'][0]['path']).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'file changed'):
            generator.generate(self.context)
        self.assertEqual(transport.calls, [])

    def test_uncertain_submission_is_not_repeated_after_failure(self):
        transport = Transport(fail_submit=True)
        generator = PreparedDeepSeekGenerator(transport, self.path, self.base/'runs')
        with self.assertRaises(TimeoutError):
            generator.generate(self.context)
        with self.assertRaises(FileExistsError):
            generator.generate(self.context)
        record = json.loads((self.base/'runs/1234567890123456.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'OUTCOME_REQUIRES_REVIEW')
        self.assertEqual(transport.calls.count('Shortcut'), 1)

    def test_truncated_final_tree_stops_without_accepting_visible_partial_or_resubmitting(self):
        class Truncated(Transport):
            def call(self, tool, arguments):
                result = super().call(tool, arguments)
                if tool == 'Snapshot' and 'Shortcut' in self.calls:
                    result['content'][0]['text'] += '\n... [truncated: reached the 4000-element capture limit — some elements were not visited.]'
                return result
        transport = Truncated()
        generator = PreparedDeepSeekGenerator(transport, self.path, self.base/'runs', timeout=1, poll_interval=0)
        with self.assertRaisesRegex(PageUnconfirmed, 'PAGE_TREE_TRUNCATED'):
            generator.generate(self.context)
        record = json.loads((self.base/'runs/1234567890123456.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'OUTCOME_REQUIRES_REVIEW')
        self.assertNotIn('result', record)
        self.assertEqual(transport.calls.count('Snapshot'), 3)
        with self.assertRaises(FileExistsError):
            generator.generate(self.context)
        self.assertEqual(transport.calls.count('Shortcut'), 1)

    def test_review_feedback_keeps_original_question_context(self):
        self.prep['review_feedback'] = 'Check the broader purpose before excluding option B.'
        self.path.write_text(json.dumps(self.prep), encoding='utf-8')
        transport = Transport()
        result = PreparedDeepSeekGenerator(transport, self.path, self.base/'runs').generate(self.context)
        self.assertIn(self.prep['review_feedback'], transport.prompt)
        self.assertIn('学生疑问：test', transport.prompt)
        self.assertEqual(result['run_id'], self.context['run_id'])
        self.assertEqual(self.prep['input_fingerprint'], input_fingerprint(self.context))

    def test_actual_delivery_changes_the_prepared_input_fingerprint(self):
        before = input_fingerprint(self.context)
        self.context.update(intent='FOLLOWUP', previous_sent_answer='老师实际回复，保留原文。',
            previous_delivery_simulated=0, sent_history=[dict(outbox_id='actual-part-1',
                question_version=self.context['question_version'], sent_at='2026-10-01T10:00:00+08:00',
                body='老师实际回复，保留原文。', simulated=0, delivery_method='MANUAL_ATTESTATION',
                part_number=1, total_parts=2)])
        frozen = copy.deepcopy(self.context)
        self.assertNotEqual(input_fingerprint(frozen), before)
        for field, value in (('body', '老师后来补充的实际回复。'),
                             ('sent_at', '2026-10-01T10:01:00+08:00'),
                             ('question_version', 'older-version'), ('total_parts', 3)):
            with self.subTest(field=field):
                changed = copy.deepcopy(frozen)
                changed['sent_history'][0][field] = value
                if field == 'body':
                    changed['previous_sent_answer'] = value
                self.assertNotEqual(input_fingerprint(frozen), input_fingerprint(changed))

    def test_changed_actual_delivery_rejects_before_desktop_or_attempt(self):
        history = [dict(outbox_id='actual-1', question_version='test',
            sent_at='2026-10-01T10:00:00+08:00', body='老师实际解答第一部分。', simulated=0)]
        self.context.update(intent='FOLLOWUP', previous_sent_answer=history[-1]['body'],
                            sent_history=history, previous_delivery_simulated=0)
        self.prep['input_fingerprint'] = input_fingerprint(self.context)
        self.path.write_text(json.dumps(self.prep), encoding='utf-8')
        self.context['sent_history'][0]['body'] = '后来登记了不同的实际回复。'
        self.context['previous_sent_answer'] = self.context['sent_history'][0]['body']
        transport = Transport()
        with self.assertRaisesRegex(ValueError, 'context changed'):
            PreparedDeepSeekGenerator(transport, self.path, self.base/'runs').generate(self.context)
        self.assertEqual(transport.calls, [])
        self.assertFalse((self.base/'runs/1234567890123456.json').exists())

    def test_long_paste_requires_restoration_and_full_readback_before_submit(self):
        class LongPaste(Transport):
            expanded = False
            def call(self, tool, arguments):
                if tool == 'Click':
                    self.calls.append(tool)
                    self.expanded = True
                    return {}
                if tool == 'Snapshot' and self.prompt and not self.expanded:
                    self.calls.append(tool)
                    return snapshot('    ├── (10,20) 按钮 "粘贴原文至输入框"\n'
                                    '    └── (100,200) 编辑 "给 DeepSeek 发送消息" [focused]\n')
                return super().call(tool, arguments)
        transport = LongPaste()
        result = PreparedDeepSeekGenerator(transport,self.path,self.base/'runs').generate(self.context)
        self.assertEqual(result['correct_option_id'], 'option-a')
        self.assertLess(transport.calls.index('Click'), transport.calls.index('Shortcut'))
        self.assertEqual(transport.calls.count('Type'), 1)
        self.assertEqual(transport.calls.count('Shortcut'), 1)


if __name__ == '__main__':
    unittest.main()
