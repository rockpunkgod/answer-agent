"""Offline recovery tests: no desktop transport and no second submission."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tools.reconcile_prepared_generation import RecoveryRejected, reconcile, frozen_payload


URL = 'https://chat.deepseek.com/a/chat/s/recovery-test'


def snap(tree, attempt_id=None):
    result = {'tool': 'Snapshot', 'is_error': False,
              'content': [{'type': 'text', 'text': 'UI Tree:\n└── window "DeepSeek - Microsoft Edge"\n'
                           f'    ├── (1,1) 文档 "DeepSeek" [value:"{URL}"]\n' + tree}]}
    if attempt_id:
        result['attempt_id'] = attempt_id
    return result


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / 'workflow.db')
        app = Helpdesk(self.db)
        student = app.bind('test-group', 'test-student', '学生', verified=True)
        self.turn = app.ingest(Incoming(student, '请讲第12题', Intent.NEW,
            verified_question=demo_question(), raw_material='Passage',
            verified_material='Passage')).turn_id
        self.course = self.base / 'course.md'
        self.excerpt = 'Verified course excerpt about reading comprehension.'
        self.course.write_text(self.excerpt, encoding='utf-8')
        digest = sha256(self.course.read_bytes()).hexdigest()
        self.manifest = self.base / 'manifest.json'
        self.manifest.write_text('{}', encoding='utf-8')
        self.bundle = {'answer_generation_allowed_by_course': True,
                       'workflow_teaching_paths': [str(self.course)],
                       'files': [{'snapshot_path': str(self.course), 'snapshot_sha256': digest}],
                       'reviewed_policy_id': 'reviewed', 'question_type': '阅读理解'}
        with patch('helpdesk.workflow.verify_bundle', return_value=self.bundle):
            self.run = Workflow(self.db, generation_adapter=PreparedDeepSeekGenerator(
                None, self.base / 'none.json', self.base),
                teaching_manifest=self.manifest).start(self.turn)
        self.frozen = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',
                                            (self.run,))['input_json'])
        self.db.execute("UPDATE runs SET state='REJECTED',error='GENERATION_UNCERTAIN' WHERE id=?",
                        (self.run,))
        self.readback = self.base / 'readback.json'
        self.readback.write_text(json.dumps(snap(f'    └── text "course.md {self.excerpt}"\n')),
                                 encoding='utf-8')
        self.preparation = self.base / 'preparation.json'
        self.preparation.write_text(json.dumps({
            'status': 'OPERATOR_VERIFIED_UPLOAD_AND_INPUT', 'run_id': self.run,
            'input_fingerprint': input_fingerprint(self.frozen), 'session_url': URL,
            'uploaded_teaching_hashes': {str(self.course): digest},
            'readback_evidence': str(self.readback),
            'readback_sha256': sha256(self.readback.read_bytes()).hexdigest(),
            'course_readback_excerpts': {str(self.course): self.excerpt}}), encoding='utf-8')
        self.attempt = self.base / (self.run + '.json')
        staged_text = ('请根据已核验课程独立解题。第一行BEGIN_answer_run_' + self.run +
                       '，最后一行END_answer_run_' + self.run + '。冻结题面如下：' +
                       frozen_payload(self.frozen))
        self.attempt.write_text(json.dumps({
            'run_id': self.run, 'session_url': URL, 'input_fingerprint': input_fingerprint(self.frozen),
            'status': 'OUTCOME_REQUIRES_REVIEW', 'automatic_retry_allowed': False,
            'prompt_sha256': sha256(staged_text.encode()).hexdigest()}), encoding='utf-8')
        self.type_id = '2' * 32
        self.type_result = self.base / 'type-result.json'
        self.type_result.write_text(json.dumps({'attempt_id': self.type_id, 'tool': 'Type',
                                                'is_error': False, 'content': []}), encoding='utf-8')
        self.type_journal = self.base / ('attempt-' + self.type_id + '.json')
        self.type_journal.write_text(json.dumps({'attempt_id': self.type_id, 'tool': 'Type',
            'arguments': {'loc': [1, 2], 'text': staged_text, 'press_enter': False},
            'status': 'TOOL_RETURNED', 'result_path': str(self.type_result)}), encoding='utf-8')
        self.journal_id = '1' * 32
        option = self.frozen['student_question']['options'][0]
        self.option = option
        answer = json.dumps({'option_label': option['label'],
                             'text': '同学，我们来分析一下。根据原文判断这个选项。'}, ensure_ascii=False)
        self.snapshot = self.base / 'snapshot.json'
        self.snapshot.write_text(json.dumps(snap(
            '    ├── 按钮 "朗读"\n'
            f'    ├── text "END_answer_run_{self.run}"\n'  # Thinking text precedes final output.
            f'    ├── text "BEGIN_answer_run_{self.run}"\n'
            f'    ├── text "{answer}"\n'
            f'    └── text "END_answer_run_{self.run}"\n', self.journal_id),
            ensure_ascii=False), encoding='utf-8')
        self.journal = self.base / ('attempt-' + self.journal_id + '.json')
        self.journal.write_text(json.dumps({'attempt_id': self.journal_id, 'tool': 'Snapshot',
            'arguments': {'use_dom': True, 'use_vision': False}, 'status': 'TOOL_RETURNED',
            'result_path': str(self.snapshot)}), encoding='utf-8')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def recover(self, approved=True):
        with patch('helpdesk.workflow.verify_bundle', return_value=self.bundle):
            return reconcile(self.db, self.run, self.attempt, self.type_result, self.snapshot,
                             self.preparation, self.manifest, approved=approved)

    def test_completed_snapshot_restores_same_run_atomically(self):
        before = self.attempt.read_bytes()
        result = self.recover()
        self.assertEqual(result['state'], 'GENERATED')
        self.assertEqual(result['recovered_run'], self.run)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.db.one('SELECT run_id FROM outbox WHERE id=?',
                                     (result['outbox_id'],))[0], self.run)
        self.assertEqual(self.attempt.read_bytes(), before)
        audit = self.db.one("SELECT details FROM audit WHERE run_id=? AND event='GENERATION_RECOVERED_FROM_SNAPSHOT'",
                            (self.run,))
        self.assertEqual(json.loads(audit['details'])['snapshot_sha256'],
                         sha256(self.snapshot.read_bytes()).hexdigest())
        self.assertFalse(self.recover()['resubmitted'])

    def test_requires_explicit_approval(self):
        with self.assertRaisesRegex(RecoveryRejected, 'Explicit'):
            self.recover(False)
        self.assertEqual(self.db.one('SELECT state FROM runs WHERE id=?', (self.run,))[0], 'REJECTED')

    def test_rejects_snapshot_without_provenance(self):
        self.journal.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RecoveryRejected, 'provenance'):
            self.recover()
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)

    def test_rejects_staged_type_without_provenance(self):
        journal = json.loads(self.type_journal.read_text(encoding='utf-8'))
        journal['arguments']['press_enter'] = True
        self.type_journal.write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaisesRegex(RecoveryRejected, 'Staged Type provenance'):
            self.recover()
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)

    def test_rejects_wrong_preparation_and_stale_question(self):
        prep = json.loads(self.preparation.read_text(encoding='utf-8'))
        prep['run_id'] = 'another-run'
        self.preparation.write_text(json.dumps(prep), encoding='utf-8')
        with self.assertRaisesRegex(RecoveryRejected, 'Preparation belongs'):
            self.recover()
        prep['run_id'] = self.run
        self.preparation.write_text(json.dumps(prep), encoding='utf-8')
        self.db.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?',
                        (self.frozen['question_id'],))
        with self.assertRaisesRegex(RecoveryRejected, 'stale'):
            self.recover()

    def test_bad_final_json_never_restores_run(self):
        raw = json.loads(self.snapshot.read_text(encoding='utf-8'))
        raw['content'][0]['text'] = raw['content'][0]['text'].replace(
            '"option_label": "' + self.option['label'] + '"', '"option_label": "Z"')
        self.snapshot.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises((RecoveryRejected, ValueError)):
            self.recover()
        self.assertEqual(self.db.one('SELECT state FROM runs WHERE id=?', (self.run,))[0], 'REJECTED')

    def test_failed_workflow_validation_rolls_back_restore(self):
        raw = json.loads(self.snapshot.read_text(encoding='utf-8'))
        other = next(label for label in 'ABCD' if label != self.option['label'])
        raw['content'][0]['text'] = raw['content'][0]['text'].replace(
            '根据原文判断这个选项。', f'根据原文选{other}。')
        self.snapshot.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(RecoveryRejected, 'workflow validation'):
            self.recover()
        run = self.db.one('SELECT state,error FROM runs WHERE id=?', (self.run,))
        self.assertEqual((run['state'], run['error']), ('REJECTED', 'GENERATION_UNCERTAIN'))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)

    def test_forged_prompt_hash_rejected(self):
        attempt = json.loads(self.attempt.read_text(encoding='utf-8'))
        attempt['prompt_sha256'] = '0' * 64
        self.attempt.write_text(json.dumps(attempt), encoding='utf-8')
        with self.assertRaisesRegex(RecoveryRejected, 'Staged prompt'):
            self.recover()
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)


if __name__ == '__main__':
    unittest.main()
