"""Frozen-run resume boundary tests; no desktop process is opened."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.locking import resource_lock as real_resource_lock
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tools.run_prepared_deepseek import run_existing


class NoDesktop:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'workflow.db')
        app = Helpdesk(self.store)
        student = app.bind('test-group', 'test-student', '学生', verified=True)
        self.turn = app.ingest(Incoming(student, '请讲第12题', Intent.NEW,
            verified_question=demo_question(), raw_material='Passage',
            verified_material='Passage')).turn_id
        self.course = self.base / 'course.md'
        self.course.write_text('Verified course text for reading comprehension.', encoding='utf-8')
        self.manifest = self.base / 'manifest.json'
        self.manifest.write_text('{}', encoding='utf-8')
        digest = sha256(self.course.read_bytes()).hexdigest()
        self.bundle = {'answer_generation_allowed_by_course': True,
                       'workflow_teaching_paths': [str(self.course)],
                       'files': [{'snapshot_path': str(self.course), 'snapshot_sha256': digest}],
                       'reviewed_policy_id': 'reviewed', 'question_type': '阅读理解'}
        with patch('helpdesk.workflow.verify_bundle', return_value=self.bundle):
            self.run = Workflow(self.store, generation_adapter=PreparedDeepSeekGenerator(
                None, self.base / 'placeholder.json', self.base / 'attempts'),
                teaching_manifest=self.manifest).start(self.turn)
        self.snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?',
                                                  (self.run,))['input_json'])
        self.preparation = self.base / 'preparation.json'
        self.preparation.write_text(json.dumps({'run_id': self.run,
            'input_fingerprint': input_fingerprint(self.snapshot)}), encoding='utf-8')
        self.attempts = self.base / 'attempts'

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def resume(self, factory=None):
        with patch.object(PreparedDeepSeekGenerator, '_preparation', return_value=({}, None)), \
             patch('helpdesk.workflow.verify_bundle', return_value=self.bundle):
            return run_existing(self.store, self.run, self.preparation, self.manifest,
                                evidence_dir=self.attempts,
                                transport_factory=factory or (lambda: NoDesktop()))

    def test_resumes_same_run_and_finishes_without_creating_another(self):
        option = self.snapshot['student_question']['options'][0]
        result = {'adapter': PreparedDeepSeekGenerator.identity, 'simulated': False,
                  'run_id': self.run, 'session_id': self.snapshot['session_id'],
                  'web_session_evidence': 'test:evidence',
                  'uploaded_teaching_hashes': {str(self.course): sha256(self.course.read_bytes()).hexdigest()},
                  'uploads_confirmed': True, 'complete': True,
                  'correct_option_id': option['id'], 'text': '同学，我们来分析一下。这道题依据原文。'}
        with patch.object(PreparedDeepSeekGenerator, 'generate', return_value=result) as generated:
            finished = self.resume()
        self.assertEqual(finished['state'], 'GENERATED')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.store.one('SELECT run_id FROM outbox WHERE id=?',
                                        (finished['outbox_id'],))[0], self.run)
        generated.assert_called_once()

    def test_cached_window_title_does_not_activate_an_unverified_browser(self):
        preparation = json.loads(self.preparation.read_text(encoding='utf-8'))
        preparation.update(window_name='Historical - Microsoft Edge', display_index=1)
        self.preparation.write_text(json.dumps(preparation), encoding='utf-8')
        desktop = NoDesktop()
        desktop.call = Mock()
        with patch.object(PreparedDeepSeekGenerator, 'generate', side_effect=ValueError('Page unconfirmed')) as generated:
            finished = self.resume(lambda: desktop)
        desktop.call.assert_not_called()
        generated.assert_called_once()
        self.assertEqual(finished['reason'], 'GENERATION_UNCERTAIN')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM outbox WHERE run_id=?', (self.run,))[0], 0)

    def test_ack_gate_blocks_old_frozen_run_before_transport_and_adapter_submit(self):
        # The run was frozen while compatibility mode was active; enabling the
        # persisted policy must also block this otherwise valid resume entry.
        Workflow(self.store).set_require_ack_before_generation(True)
        before = {table: self.store.one('SELECT COUNT(*) FROM ' + table)[0]
                  for table in ('sessions', 'runs', 'answers', 'outbox', 'audit', 'human_tasks')}
        factory = Mock(side_effect=AssertionError('Desktop must not start without confirmed ACK'))
        with patch.object(PreparedDeepSeekGenerator, 'generate') as submit:
            with self.assertRaisesRegex(ValueError, '^ACK_REQUIRED$'):
                self.resume(factory)
        factory.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM runs WHERE id=?', (self.run,))[0], 'RUNNING')
        self.assertEqual(before, {table: self.store.one('SELECT COUNT(*) FROM ' + table)[0]
                                 for table in before})

    def test_existing_attempt_reports_without_desktop(self):
        self.attempts.mkdir()
        (self.attempts / (self.run + '.json')).write_text(
            json.dumps({'run_id': self.run, 'status': 'SUBMISSION_UNCONFIRMED'}), encoding='utf-8')
        def forbidden():
            raise AssertionError('Desktop must not start')
        result = self.resume(forbidden)
        self.assertEqual(result['existing_attempt']['status'], 'SUBMISSION_UNCONFIRMED')
        self.assertFalse(result['resubmitted'])

    def test_wrong_preparation_rejected_before_desktop(self):
        self.preparation.write_text(json.dumps({'run_id': 'another-run',
            'input_fingerprint': input_fingerprint(self.snapshot)}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'different frozen run'):
            self.resume(lambda: self.fail('Desktop started'))

    def test_finished_run_reports_without_desktop(self):
        self.store.execute("UPDATE runs SET state='REJECTED',error='GENERATION_UNCERTAIN' WHERE id=?",
                           (self.run,))
        result = self.resume(lambda: self.fail('Desktop started'))
        self.assertEqual(result['existing_run']['state'], 'REJECTED')
        self.assertFalse(result['resubmitted'])

    def test_wrong_session_rejected_before_desktop(self):
        self.store.execute("UPDATE sessions SET state='REPLACED' WHERE id=?",
                           (self.snapshot['session_id'],))
        with self.assertRaisesRegex(ValueError, 'session'):
            self.resume(lambda: self.fail('Desktop started'))

    def test_uncertain_generation_marks_same_run_rejected(self):
        with patch.object(PreparedDeepSeekGenerator, 'generate',
                          side_effect=TimeoutError('remote completion unknown')) as generated:
            result = self.resume()
        self.assertEqual(result['reason'], 'GENERATION_UNCERTAIN')
        self.assertEqual(self.store.one('SELECT state,error FROM runs WHERE id=?',
                                        (self.run,))['state'], 'REJECTED')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 1)
        generated.assert_called_once()
        self.assertFalse(self.resume(lambda: self.fail('Desktop started'))['resubmitted'])

    def test_concurrent_resume_is_busy_without_rejecting_active_run(self):
        entered = threading.Event()
        release = threading.Event()
        opened = []
        option = self.snapshot['student_question']['options'][0]
        result = {'adapter': PreparedDeepSeekGenerator.identity, 'simulated': False,
                  'run_id': self.run, 'session_id': self.snapshot['session_id'],
                  'web_session_evidence': 'test:evidence',
                  'uploaded_teaching_hashes': {str(self.course): sha256(self.course.read_bytes()).hexdigest()},
                  'uploads_confirmed': True, 'complete': True,
                  'correct_option_id': option['id'], 'text': '同学，我们来分析一下。这道题依据原文。'}

        def blocked_generate(_generator, _snapshot):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('Test release timed out')
            return result

        def make_transport():
            opened.append(1)
            return NoDesktop()

        def invoke():
            with closing(Store(self.base / 'workflow.db')) as store:
                return run_existing(store, self.run, self.preparation, self.manifest,
                                    evidence_dir=self.attempts, transport_factory=make_transport)

        # Keep the real lock, but shorten the second caller's bounded wait.
        def short_lock(path, timeout=5):
            return real_resource_lock(path, timeout=.05)

        with patch.object(PreparedDeepSeekGenerator, '_preparation', return_value=({}, None)), \
             patch.object(PreparedDeepSeekGenerator, 'generate', blocked_generate), \
             patch('helpdesk.workflow.verify_bundle', return_value=self.bundle), \
             patch('tools.run_prepared_deepseek.resource_lock', side_effect=short_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(invoke)
                self.assertTrue(entered.wait(2))
                try:
                    with self.assertRaisesRegex(TimeoutError, 'Resource busy'):
                        pool.submit(invoke).result(timeout=2)
                    self.assertEqual(self.store.one('SELECT state FROM runs WHERE id=?',
                                                    (self.run,))[0], 'RUNNING')
                    self.assertEqual(len(opened), 1)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=2)['state'], 'GENERATED')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 1)


if __name__ == '__main__':
    unittest.main()
