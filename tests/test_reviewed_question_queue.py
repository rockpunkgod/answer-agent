"""Persistent scheduling and explicit injected actors; no actual desktop calls."""
import unittest
from unittest.mock import Mock, patch

from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.reviewed_question_queue import (advance, enqueue, get, list_queue,
                                              request_enqueue, resume_pending, list_admissions)
from helpdesk.session_isolation import claim_deepseek_chat
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tests import test_automatic_preparation as source_tests
from tests.test_mcp_preparation import FakeDesktop, URL


class ReviewedQuestionQueueTests(unittest.TestCase):
    def setUp(self):
        source_tests.AutomaticPreparationTests.setUp(self)
        # Discard fake upload evidence and ownership so every queue begins
        # before browser creation. This only changes the disposable fixture.
        self.candidate.unlink()
        self.db.execute('DELETE FROM deepseek_chats')
        self.calls = []
        self.enqueued = self.enqueue()

    tearDown = source_tests.AutomaticPreparationTests.tearDown
    frozen = source_tests.AutomaticPreparationTests.frozen
    make_candidate = source_tests.AutomaticPreparationTests.make_candidate

    def enqueue(self):
        return enqueue(self.db, self.task['id'], self.manifest_path, candidate_path=self.candidate)

    def session(self, **arguments):
        self.calls.append('session')
        self.assert_committed_attempt(arguments, 'SESSION_CREATION')
        return URL

    def prepare(self, **arguments):
        self.calls.append('prepare')
        self.assert_committed_attempt(arguments, 'PREPARATION')
        fake = FakeDesktop([dict(name=self.course.name, content=self.course.read_text(encoding='utf-8'))])
        controls = dict(preparation_mode='FAST_UPLOAD_THEN_GENERATE', material_order='COURSE_THEN_QUESTION',
                        upload_button='Upload files', picker_window='Open', file_input='File name', open_button='Open')
        DeepSeekSessionPreparer(fake, arguments['snapshot'], arguments['session_url'],
            arguments['candidate_path'], controls, poll_interval=0, timeout=2).run()

    def generate(self, **arguments):
        self.calls.append('generate')
        self.assert_committed_attempt(arguments, 'GENERATION')
        snapshot = arguments['snapshot']
        selected = next(o for o in snapshot['student_question']['options']
                        if o['verified_text'] == 'To look after his mother.')
        # A trusted callback invokes the existing finish machinery. Returning a
        # dict without these records is explicitly insufficient in another test.
        flow = Workflow(self.db)
        return flow.finish(snapshot['run_id'], dict(adapter=snapshot['generation_adapter'],
            simulated=False, run_id=snapshot['run_id'], session_id=snapshot['session_id'],
            web_session_evidence='Offline final capture fixture',
            uploaded_teaching_hashes={s['path']: s['sha256'] for s in snapshot['teaching_skills']},
            uploads_confirmed=True, complete=True, correct_option_id=selected['id'],
            text=f"第12题选{selected['label']}，根据原文判断。"))

    def assert_committed_attempt(self, arguments, stage):
        other = Store(self.db.path)
        try:
            row = other.one('SELECT * FROM reviewed_question_attempts WHERE id=?', (arguments['attempt_id'],))
            self.assertEqual((row['state'], row['stage'], row['executor']), ('STARTED', stage, 'LUNA'))
            self.assertEqual(other.one('SELECT phase FROM reviewed_question_queue WHERE task_id=?',
                (self.task['id'],))[0], stage + '_STARTED')
        finally:
            other.close()

    def step(self, **overrides):
        arguments = dict(executor='LUNA', session_creator=self.session, preparer=self.prepare, generator=self.generate)
        arguments.update(overrides)
        return advance(self.db, self.task['id'], **arguments)

    def test_enqueue_is_persistent_idempotent_and_without_execution(self):
        self.assertEqual(self.enqueued['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(self.enqueued['attempts'], [])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.enqueue(), self.enqueued)
        self.db.close()
        self.db = Store(self.base / 'business.db')
        self.assertEqual(self.enqueue(), self.enqueued)
        self.assertEqual(list_queue(self.db), [self.enqueued])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)
        with self.assertRaisesRegex(ValueError, 'CONFIGURATION_CHANGED'):
            enqueue(self.db, self.task['id'], self.manifest_path, candidate_path=self.base / 'changed.json')

    def test_new_review_automatically_freezes_once_without_desktop(self):
        draft = self.tasks.create_draft(self.payload)
        task = self.tasks.review(draft['id'], expected_revision=1,
            reviewer='Initial source reviewer', source_evidence='Original worksheet verified')
        self.assertIsNone(task['run_id'])
        with patch('helpdesk.workflow.MockDesktop', side_effect=AssertionError('no desktop')):
            queued = enqueue(self.db, task['id'], self.manifest_path)
            self.assertEqual(enqueue(self.db, task['id'], self.manifest_path), queued)
        frozen_task = self.db.one('SELECT * FROM operator_tasks WHERE id=?', (task['id'],))
        self.assertEqual(queued['run_id'], frozen_task['run_id'])
        self.assertEqual(queued['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertTrue(queued['candidate_path'].endswith('preparation-candidate.json'))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 2)

    def test_no_luna_actor_means_zero_callbacks_and_no_attempts(self):
        callback = Mock(side_effect=AssertionError('no execution permitted'))
        state = advance(self.db, self.task['id'], session_creator=callback, preparer=callback, generator=callback)
        self.assertEqual(state['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(state['last_error'], 'LUNA_EXECUTOR_REQUIRED:SESSION_CREATION')
        callback.assert_not_called()
        self.assertEqual(state['attempts'], [])
        with self.assertRaisesRegex(ValueError, 'ONLY_EXPLICIT_LUNA_EXECUTOR_ALLOWED'):
            self.step(executor='SOL')
        callback.assert_not_called()

    def test_three_stages_use_original_source_gate_and_authoritative_answer(self):
        flow = Workflow(self.db)
        flow.set_answer_review_required(False)
        flow.set_manual_send(True)
        before_audits = self.db.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0]
        self.assertEqual(self.step()['phase'], 'READY_FOR_PREPARATION')
        self.assertEqual(self.step()['phase'], 'ATTACHMENTS_READY')
        self.assertEqual(self.step()['phase'], 'GENERATED')
        self.assertEqual(self.calls, ['session', 'prepare', 'generate'])
        self.assertEqual([a['state'] for a in get(self.db, self.task['id'])['attempts']], ['SUCCEEDED'] * 3)
        self.assertEqual(self.step()['phase'], 'GENERATED')
        self.assertEqual(self.enqueue()['phase'], 'GENERATED')
        self.assertEqual(self.calls, ['session', 'prepare', 'generate'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0], before_audits)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        row = self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        self.assertEqual((row['state'], row['review_status']), ('PENDING', 'NOT_REQUIRED'))
        self.assertEqual(self.db.one('SELECT status FROM questions WHERE id=?', (self.task['question_id'],))[0], 'AWAITING_MANUAL_SEND')

    def test_raised_session_attempt_is_not_replayed_after_restart(self):
        callback = Mock(side_effect=RuntimeError('Desktop outcome unknown'))
        self.assertEqual(self.step(session_creator=callback)['phase'], 'EXECUTION_UNCERTAIN')
        self.db.close()
        self.db = Store(self.base / 'business.db')
        self.assertEqual(self.step(session_creator=callback)['phase'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 1)

    def test_process_interruption_marks_uncertain_without_retry(self):
        callback = Mock(side_effect=SystemExit('simulated process death'))
        with self.assertRaises(SystemExit):
            self.step(session_creator=callback)
        self.assertEqual(get(self.db, self.task['id'])['attempts'][0]['state'], 'STARTED')
        self.db.close()
        self.db = Store(self.base / 'business.db')
        recovered = self.step(session_creator=callback)
        self.assertEqual(recovered['phase'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(recovered['attempts'][0]['state'], 'UNCERTAIN')
        self.assertEqual(callback.call_count, 1)

    def test_persisted_chat_recovers_session_after_interruption_without_recreation(self):
        def create_then_crash(**arguments):
            claim_deepseek_chat(arguments['snapshot'], URL)
            raise SystemExit('crash after owned chat saved')
        with self.assertRaises(SystemExit):
            self.step(session_creator=create_then_crash)
        forbidden = Mock(side_effect=AssertionError('do not recreate'))
        state = self.step(executor=None, session_creator=forbidden)
        self.assertEqual(state['phase'], 'READY_FOR_PREPARATION')
        self.assertEqual(state['attempts'][0]['state'], 'RECOVERED_FROM_CHAT')
        forbidden.assert_not_called()

    def test_saved_fast_candidate_recovers_without_upload_or_second_human_gate(self):
        self.step()
        def upload_then_crash(**arguments):
            self.prepare(**arguments)
            raise SystemExit('crash after candidate saved')
        with self.assertRaises(SystemExit):
            self.step(preparer=upload_then_crash)
        forbidden = Mock(side_effect=AssertionError('do not repeat upload'))
        state = self.step(executor=None, preparer=forbidden)
        self.assertEqual(state['phase'], 'ATTACHMENTS_READY')
        self.assertEqual(state['attempts'][1]['state'], 'RECOVERED_FROM_PREPARATION')
        forbidden.assert_not_called()
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)

    def test_generated_run_recovers_without_second_submit(self):
        self.step()
        self.step()
        def generate_then_crash(**arguments):
            self.generate(**arguments)
            raise SystemExit('crash after final answer persisted')
        with self.assertRaises(SystemExit):
            self.step(generator=generate_then_crash)
        forbidden = Mock(side_effect=AssertionError('do not generate again'))
        state = self.step(executor=None, generator=forbidden)
        self.assertEqual(state['phase'], 'GENERATED')
        self.assertEqual(state['attempts'][-1]['state'], 'RECOVERED_FROM_RUN')
        forbidden.assert_not_called()

    def test_callback_return_is_not_evidence_of_completion(self):
        self.step()
        self.step()
        pretend = Mock(return_value={'state': 'GENERATED', 'text': 'invented callback result'})
        state = self.step(generator=pretend)
        self.assertEqual(state['phase'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.step(generator=pretend)
        self.assertEqual(pretend.call_count, 1)

    def test_changed_source_and_stop_switch_prevent_executor_call(self):
        callback = Mock(side_effect=AssertionError('must not run'))
        self.db.execute("UPDATE settings SET value='true' WHERE key='stop_requested'")
        self.assertEqual(self.step(session_creator=callback)['last_error'], 'STOPPED')
        self.assertEqual(get(self.db, self.task['id'])['attempts'], [])
        self.db.execute("UPDATE settings SET value='false' WHERE key='stop_requested'")
        self.db.execute('UPDATE operator_tasks SET source_evidence=? WHERE id=?', ('changed source', self.task['id']))
        self.assertEqual(self.step(session_creator=callback)['phase'], 'NEEDS_ATTENTION')
        callback.assert_not_called()

    def test_ack_gate_blocks_freezing_without_manufactured_receipt(self):
        draft = self.tasks.create_draft(self.payload)
        task = self.tasks.review(draft['id'], expected_revision=1,
            reviewer='Source reviewer', source_evidence='Worksheet inspected')
        Workflow(self.db).set_require_ack_before_generation(True)
        before = self.db.one('SELECT COUNT(*) FROM runs')[0]
        with self.assertRaisesRegex(ValueError, 'ACK_REQUIRED'):
            enqueue(self.db, task['id'], self.manifest_path)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before)
        self.assertIsNone(self.db.one('SELECT run_id FROM operator_tasks WHERE id=?', (task['id'],))[0])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE state='SENT_UI_CONFIRMED'")[0], 0)
        self.assertIsNone(self.db.one('SELECT task_id FROM reviewed_question_queue WHERE task_id=?', (task['id'],)))

    def test_status_interface_contains_no_source_or_callback_secrets(self):
        callback = Mock(side_effect=RuntimeError('secret-token-and-original-question'))
        self.step(session_creator=callback)
        for state in (get(self.db, self.task['id']), list_queue(self.db)):
            self.assertNotIn('secret-token-and-original-question', repr(state))
            self.assertNotIn(self.payload['passage'], repr(state))
            self.assertNotIn(self.task['source_evidence'], repr(state))

    def test_uncommitted_review_transaction_cannot_enqueue_or_execute(self):
        with self.db.transaction():
            with self.assertRaisesRegex(ValueError, 'COMMITTED_REVIEW_TRANSACTION_REQUIRED'):
                self.enqueue()
            with self.assertRaisesRegex(ValueError, 'COMMITTED_QUEUE_TRANSACTION_REQUIRED'):
                self.step()

    def reviewed_unfrozen(self):
        draft = self.tasks.create_draft(self.payload)
        return self.tasks.review(draft['id'], expected_revision=1,
            reviewer='First worksheet reviewer', source_evidence='Original worksheet fully inspected')

    def persisted(self):
        tables = ('operator_tasks', 'runs', 'outbox', 'audit', 'reviews', 'answers',
                  'reviewed_question_queue', 'reviewed_question_attempts', 'reviewed_question_admissions')
        return {table: [dict(r) for r in self.db.all(f'SELECT * FROM {table} ORDER BY rowid')] for table in tables}

    def confirm_fixture_ack(self, task):
        # Disposable, pre-existing trusted delivery-confirmation fixture. The
        # admission library does not create, send, or change this receipt.
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=0 WHERE message_id=? AND purpose='ACK'",
                        (task['message_id'],))

    def test_admission_waiting_ack_is_durable_quiet_and_admits_without_second_review(self):
        task = self.reviewed_unfrozen()
        Workflow(self.db).set_require_ack_before_generation(True)
        before_runs = self.db.one('SELECT COUNT(*) FROM runs')[0]
        state = request_enqueue(self.db, task['id'], self.manifest_path)
        self.assertEqual((state['phase'], state['enqueued'], state['run_id']), ('WAITING_ACK', False, None))
        before = self.persisted()
        self.assertEqual(request_enqueue(self.db, task['id'], self.manifest_path), state)
        resume_pending(self.db)
        resume_pending(self.db)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before_runs)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE state='SENT_UI_CONFIRMED'")[0], 0)
        self.db.close()
        self.db = Store(self.base / 'business.db')
        self.assertEqual(list_admissions(self.db), [state])
        # An old simulated receipt is insufficient for real prepared generation.
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=1 WHERE message_id=? AND purpose='ACK'",
                        (task['message_id'],))
        self.assertEqual(resume_pending(self.db)[0]['phase'], 'WAITING_ACK')
        self.assertIsNone(self.db.one('SELECT run_id FROM operator_tasks WHERE id=?', (task['id'],))[0])
        # Another student's ACK cannot satisfy this task's original binding.
        self.confirm_fixture_ack(self.task)
        self.assertEqual(resume_pending(self.db)[0]['phase'], 'WAITING_ACK')
        self.confirm_fixture_ack(task)
        with patch('helpdesk.workflow.MockDesktop', side_effect=AssertionError('no desktop')):
            ready = resume_pending(self.db)[0]
        self.assertEqual((ready['phase'], ready['admission_phase'], ready['enqueued']),
                         ('WAITING_DESKTOP_EXECUTOR', 'ENQUEUED', True))
        self.assertIsNotNone(ready['run_id'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before_runs + 1)
        before = self.persisted()
        resume_pending(self.db)
        request_enqueue(self.db, task['id'], self.manifest_path)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)

    def test_admission_stop_is_quiet_and_resume_does_not_execute_desktop(self):
        task = self.reviewed_unfrozen()
        Workflow(self.db).set_stop(True)
        state = request_enqueue(self.db, task['id'], self.manifest_path)
        self.assertEqual(state['phase'], 'STOPPED')
        self.assertIsNone(state['run_id'])
        before = self.persisted()
        resume_pending(self.db)
        self.assertEqual(self.persisted(), before)
        Workflow(self.db).set_stop(False)
        state = resume_pending(self.db)[0]
        self.assertTrue(state['enqueued'])
        self.assertEqual(state['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(state['attempts'], [])

    def test_admission_source_or_course_change_stops_without_infinite_retry(self):
        task = self.reviewed_unfrozen()
        Workflow(self.db).set_require_ack_before_generation(True)
        request_enqueue(self.db, task['id'], self.manifest_path)
        self.db.execute('UPDATE operator_tasks SET source_evidence=? WHERE id=?', ('Changed source', task['id']))
        before_runs = self.db.one('SELECT COUNT(*) FROM runs')[0]
        self.assertEqual(resume_pending(self.db)[0]['phase'], 'NEEDS_ATTENTION')
        before = self.persisted()
        with patch('helpdesk.reviewed_question_queue._validate_admission_materials', side_effect=AssertionError('no repeated invalid attempt')):
            resume_pending(self.db)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before_runs)

    def test_admission_configuration_is_fixed_and_material_changes_fail_closed(self):
        task = self.reviewed_unfrozen()
        Workflow(self.db).set_require_ack_before_generation(True)
        request_enqueue(self.db, task['id'], self.manifest_path)
        with self.assertRaisesRegex(ValueError, 'ADMISSION_CONFIGURATION_CHANGED'):
            request_enqueue(self.db, task['id'], self.manifest_path, candidate_path=self.base / 'other-candidate.json')
        self.course.write_bytes(self.course.read_bytes() + b'changed course')
        self.confirm_fixture_ack(task)
        state = resume_pending(self.db)[0]
        self.assertEqual(state['phase'], 'NEEDS_ATTENTION')
        self.assertIsNone(state['run_id'])

    def test_admission_interruption_after_freeze_reuses_same_run(self):
        task = self.reviewed_unfrozen()
        with patch('helpdesk.reviewed_question_queue._source_review', side_effect=SystemExit('crash after freeze before queue insert')):
            with self.assertRaises(SystemExit):
                request_enqueue(self.db, task['id'], self.manifest_path)
        frozen_run = self.db.one('SELECT run_id FROM operator_tasks WHERE id=?', (task['id'],))[0]
        self.assertIsNotNone(frozen_run)
        before_runs = self.db.one('SELECT COUNT(*) FROM runs')[0]
        self.db.close()
        self.db = Store(self.base / 'business.db')
        state = resume_pending(self.db)[0]
        self.assertEqual(state['run_id'], frozen_run)
        self.assertTrue(state['enqueued'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before_runs)

    def test_admission_interruption_after_queue_insert_recovers_without_refreeze(self):
        task = self.reviewed_unfrozen()
        original = enqueue
        def enqueue_then_crash(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit('crash before admission completion')
        with patch('helpdesk.reviewed_question_queue.enqueue', side_effect=enqueue_then_crash):
            with self.assertRaises(SystemExit):
                request_enqueue(self.db, task['id'], self.manifest_path)
        before_runs = self.db.one('SELECT COUNT(*) FROM runs')[0]
        with patch('helpdesk.reviewed_question_queue.enqueue', side_effect=AssertionError('must not freeze again')):
            state = resume_pending(self.db)[0]
        self.assertTrue(state['enqueued'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], before_runs)

    def test_readonly_queue_and_admission_projection_uses_authoritative_terminals(self):
        request_enqueue(self.db, self.task['id'], self.manifest_path, candidate_path=self.candidate)
        self.step()
        self.step()
        # External trusted completion: leave the stored queue phase unchanged.
        selected = next(o for o in self.snapshot['student_question']['options'] if o['label'] == 'C')
        Workflow(self.db).finish(self.task['run_id'], dict(adapter=self.snapshot['generation_adapter'],
            simulated=False, run_id=self.task['run_id'], session_id=self.snapshot['session_id'],
            web_session_evidence='External offline final capture',
            uploaded_teaching_hashes={s['path']: s['sha256'] for s in self.snapshot['teaching_skills']},
            uploads_confirmed=True, complete=True, correct_option_id=selected['id'], text='第12题选C。'))
        before = self.persisted()
        with patch('helpdesk.reviewed_question_queue.advance', side_effect=AssertionError('read must not advance')):
            visible = get(self.db, self.task['id'])
            self.assertEqual(visible['phase'], 'GENERATED')
            self.assertEqual(visible['stored_phase'], 'ATTACHMENTS_READY')
            self.assertEqual(list_admissions(self.db)[0]['phase'], 'GENERATED')
            self.assertEqual(list_queue(self.db)[0]['phase'], 'GENERATED')
        self.assertEqual(self.persisted(), before)
        self.db.execute("UPDATE outbox SET body=body || 'changed' WHERE purpose='ANSWER'")
        before = self.persisted()
        self.assertEqual(get(self.db, self.task['id'])['phase'], 'NEEDS_ATTENTION')
        self.assertEqual(get(self.db, self.task['id'])['last_error'], 'GENERATED_OUTBOX_INVALID')
        self.assertEqual(self.persisted(), before)
        self.db.execute("UPDATE runs SET state='REJECTED' WHERE id=?", (self.task['run_id'],))
        before = self.persisted()
        visible = get(self.db, self.task['id'])
        self.assertEqual((visible['phase'], visible['authoritative_run_state']), ('NEEDS_ATTENTION', 'REJECTED'))
        self.assertEqual(list_admissions(self.db)[0]['phase'], 'NEEDS_ATTENTION')
        self.assertEqual(self.persisted(), before)


if __name__ == '__main__':
    unittest.main()
