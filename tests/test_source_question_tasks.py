"""Source-backed single clarity gate; every source, ACK and desktop is a fixture."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from helpdesk.automatic_preparation import complete_automatic_preparation, _source_review
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_storage import CollectorStore
from helpdesk.domain import Intent, Option, Question
from helpdesk.message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.operator_tasks import OperatorTasks
from helpdesk.performance import PerformanceLedger
from helpdesk.reviewed_question_queue import request_enqueue, resume_pending, advance, get
from helpdesk.service import Helpdesk
from helpdesk.source_question_tasks import SourceQuestionTasks, validate_reviewed_source_task
from helpdesk.storage import Store, encode
from helpdesk.workflow import Workflow
from tests.test_mcp_preparation import FakeDesktop, URL


class SourceQuestionTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / 'business.db')
        self.addCleanup(lambda: self.db.close())
        self.raw = CollectorStore(self.base / 'source.db')
        self.binding = Helpdesk(self.db).bind('fixture-room', 'fixture-student', 'fixture student', verified=True)
        self.tasks = SourceQuestionTasks(self.db)
        self.ack = CollectorDispatcher(self.raw, self.db)
        self.resolver = CollectorDispatcher(self.raw, self.db, processing_mode='CASE_RESOLUTION')
        self.course = self.base / 'fixture-course.md'
        self.course.write_text('# Fixture course\nRead the complete source and compare all options.\n', encoding='utf-8')
        self.manifest_path = self.base / 'manifest.json'
        self.manifest_path.write_text('{}', encoding='utf-8')
        self.manifest = dict(question_type='阅读理解', answer_generation_allowed_by_course=True,
            workflow_teaching_paths=[str(self.course)], files=[dict(snapshot_path=str(self.course),
            snapshot_sha256=sha256(self.course.read_bytes()).hexdigest())], reviewed_policy_id='fixture-policy')
        self.patches = [patch(name, return_value=self.manifest) for name in
            ('helpdesk.operator_tasks.verify_bundle', 'helpdesk.workflow.verify_bundle', 'helpdesk.automatic_preparation.verify_bundle')]
        for stub in self.patches:
            stub.start()
            self.addCleanup(stub.stop)
        flow = Workflow(self.db)
        flow.set_require_ack_before_generation(True)
        flow.set_require_source_clarity_review(True)
        flow.set_answer_review_required(False)
        flow.set_delivery_policy({'ACK': 'AUTO', 'ANSWER': 'MANUAL', 'CORRECTION': 'MANUAL'}, 'fixture single clarity gate')
        self.photo = None
        self.message, self.receipt, self.outcome = self.receive()
        self.draft = self.create(self.receipt)

    def receive(self, source_id='original', *, intent=Intent.NEW, sent='2026-09-30T22:58:00+08:00',
                question_id=None, photo=None, mode=SyncMode.LIVE):
        utc, local = normalize_sent_time(sent)
        values = dict(message_type='image', media_id='fixture-photo-' + source_id,
                      local_media_path=str(photo), media_hash=sha256(photo.read_bytes()).hexdigest()) if photo else {}
        message = NormalizedMessage(source_type='wecom_archive', source_message_id=source_id,
            room_id='fixture-room', sender_id='fixture-student', room_name='Fixture English group',
            raw_content='  请问第12题为什么不选B？  ', normalized_text='  请问第12题为什么不选B？  ',
            sent_at_raw=sent, sent_at_utc=utc, sent_at_local=local, ingested_at='2026-09-30T23:05:00+08:00', **values)
        state = self.raw.get_sync_state('fixture-source', mode)
        self.raw.persist_batch('fixture-source', mode, MessageBatch((message,), source_id), state['cursor'])
        self.ack.drain()
        receipt = dict(self.db.one('SELECT * FROM collector_answer_tasks WHERE collector_message_id=?', (message.message_id,)))
        if mode != SyncMode.LIVE:
            return message, receipt, None
        options = {'A': 'To visit a friend.', 'B': 'To take a holiday.', 'C': 'To look after his mother.', 'D': 'To find a job.'}
        question = Question('12', 'Why did John go home?', 'Why did John go home?',
            tuple(Option.confirmed(label, text, i, 'offline transcription fixture') for i, (label, text) in enumerate(options.items())),
            'offline transcription fixture')
        fields = dict(question_id=question_id) if question_id else dict(verified_question=question,
            raw_material='John went home to look after his mother.', verified_material='John went home to look after his mother.')
        decision = self.resolver.received_incoming(receipt['id'], intent=intent, **fields)
        outcome = self.resolver.resolve(receipt['id'], decision, reviewer='offline resolver fixture',
                                        rationale='offline fixture source and association', confidence=1)
        return message, receipt, outcome

    def create(self, receipt):
        return self.tasks.create_from_received(self.raw, receipt['id'], question_type='阅读理解',
                                               resolver_evidence='Offline resolver fixture; no actual DeepSeek judgment claimed')

    def review(self, draft=None):
        draft = draft or self.draft
        return self.tasks.review(draft['id'], expected_revision=draft['revision'], reviewer='fixture clarity reviewer',
                                 source_evidence='fixture complete original question is legible')

    def confirm_fixture_ack(self, message_id=None):
        with self.db.transaction():
            self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=0 WHERE purpose='ACK' AND message_id=?",
                            (message_id or self.outcome.message_id,))

    def admit(self, task=None):
        return request_enqueue(self.db, (task or self.task)['id'], self.manifest_path)

    def enqueue(self):
        self.task = self.review()
        self.confirm_fixture_ack()
        self.queue = self.admit()
        self.task = self.tasks.get_task(self.task['id'])
        self.snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (self.task['run_id'],))[0])
        return self.queue

    def business_counts(self):
        return {table: self.db.one('SELECT COUNT(*) FROM ' + table)[0] for table in
                ('bindings', 'messages', 'cases', 'questions', 'question_versions', 'turns', 'outbox', 'runs',
                 'reviews', 'answers', 'performance_units')}

    def source_review_count(self):
        return self.db.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")[0]

    def prepare(self, **args):
        fake = FakeDesktop([dict(name=self.course.name, content=self.course.read_text(encoding='utf-8'))])
        DeepSeekSessionPreparer(fake, args['snapshot'], args['session_url'], args['candidate_path'],
            dict(preparation_mode='FAST_UPLOAD_THEN_GENERATE', material_order='COURSE_THEN_QUESTION',
                 upload_button='Upload files', picker_window='Open', file_input='File name', open_button='Open'),
            poll_interval=0, timeout=2).run()

    def generate(self, **args):
        snapshot = args['snapshot']
        option = next(item for item in snapshot['student_question']['options'] if item['verified_text'] == 'To look after his mother.')
        self.fixture_answer = '第12题选C。\n原始讲解完整保留。\n' + '这是隔离流程测试内容。' * 80
        return Workflow(self.db).finish(snapshot['run_id'], dict(adapter=snapshot['generation_adapter'],
            simulated=False, run_id=snapshot['run_id'], session_id=snapshot['session_id'],
            web_session_evidence='Offline response fixture, not actual DeepSeek',
            uploaded_teaching_hashes={item['path']: item['sha256'] for item in snapshot['teaching_skills']},
            uploads_confirmed=True, complete=True, correct_option_id=option['id'], text=self.fixture_answer))

    def test_source_intake_and_review_keep_original_binding_case_message_version_ack(self):
        before = self.business_counts()
        message = dict(self.db.one('SELECT * FROM messages'))
        ack = dict(self.db.one('SELECT * FROM outbox'))
        self.assertEqual(self.create(self.receipt)['id'], self.draft['id'])
        task = self.review()
        self.assertEqual(self.business_counts(), before)
        self.assertEqual((task['binding_id'], task['message_id'], task['case_id'], task['question_id'], task['turn_id']),
                         (self.binding, self.outcome.message_id, self.outcome.case_id, self.outcome.question_id, self.outcome.turn_id))
        self.assertEqual(task['label'], 'SOURCE_MESSAGE')
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM bindings WHERE group_key LIKE 'local-operator-test:%'")[0], 0)
        self.assertEqual(dict(self.db.one('SELECT * FROM messages')), message)
        self.assertEqual(dict(self.db.one('SELECT * FROM outbox')), ack)
        self.assertEqual(self.source_review_count(), 1)

    def test_repeated_clarity_review_is_identical_and_different_evidence_rejected(self):
        task = self.review()
        audits = [dict(row) for row in self.db.all('SELECT * FROM audit')]
        self.assertEqual(self.review(), task)
        self.assertEqual(audits, [dict(row) for row in self.db.all('SELECT * FROM audit')])
        with self.assertRaisesRegex(ValueError, 'different evidence'):
            self.tasks.review(self.draft['id'], expected_revision=1, reviewer='new reviewer', source_evidence='new review')
        self.assertEqual(self.source_review_count(), 1)

    def test_only_original_source_review_class_can_review_or_freeze_source_draft(self):
        with self.assertRaisesRegex(ValueError, 'original receipt'):
            OperatorTasks(self.db).review(self.draft['id'], expected_revision=1, reviewer='fixture', source_evidence='fixture')
        task = self.review()
        self.confirm_fixture_ack()
        with self.assertRaisesRegex(ValueError, 'original source review'):
            OperatorTasks(self.db).freeze(task['id'], teaching_manifest=self.manifest_path,
                preparation_path=self.base / 'bad.json', evidence_dir=self.base / 'bad-evidence')
        self.assertEqual(self.business_counts()['runs'], 0)
        with self.assertRaises(ValueError):
            OperatorTasks(self.db).revise(self.draft['id'], self.draft['payload'], expected_revision=1)

    def test_review_waits_for_original_real_ack_and_background_admits_without_second_review(self):
        self.task = self.review()
        state = self.admit()
        self.assertEqual(state['phase'], 'WAITING_ACK')
        self.assertEqual(self.business_counts()['runs'], 0)
        with self.db.transaction():
            self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=1 WHERE purpose='ACK'")
        self.assertEqual(resume_pending(self.db)[0]['phase'], 'WAITING_ACK')
        self.assertEqual(self.business_counts()['runs'], 0)
        self.confirm_fixture_ack()
        state = resume_pending(self.db)[0]
        self.assertEqual(state['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertFalse(state['source_review_required_again'])
        self.assertEqual(self.source_review_count(), 1)
        self.assertEqual(self.business_counts()['runs'], 1)
        self.assertEqual(self.admit()['run_id'], state['run_id'])

    def test_source_marker_preserves_actual_first_time_and_does_not_label_operator_test(self):
        self.enqueue()
        marker = self.snapshot['source_clarity_review']
        self.assertNotIn('operator_test', self.snapshot)
        self.assertTrue(marker['original_sender_known'])
        self.assertEqual(marker['original_sent_at'], self.message.sent_at_local)
        self.assertEqual(marker['original_message_id'], self.outcome.message_id)
        self.assertEqual(self.snapshot['student_words'], self.message.raw_content)
        self.assertEqual(self.business_counts()['performance_units'], 0)
        self.assertEqual(self.source_review_count(), 1)

    def test_direct_generation_cannot_bypass_the_one_source_clarity_gate(self):
        from helpdesk.mcp_generation import PreparedDeepSeekGenerator
        self.confirm_fixture_ack()
        flow = Workflow(self.db, generation_adapter=PreparedDeepSeekGenerator(None,
            self.base / 'unreviewed.json', self.base / 'unreviewed-evidence'), teaching_manifest=self.manifest_path)
        with self.assertRaisesRegex(ValueError, 'SOURCE_CLARITY_REQUIRED'):
            flow.start(self.outcome.turn_id)
        self.assertEqual(self.business_counts()['runs'], 0)
        self.assertEqual(self.source_review_count(), 0)

    def test_no_luna_actor_has_no_desktop_calls_or_attempts(self):
        self.enqueue()
        callback = Mock(side_effect=AssertionError('No real desktop'))
        state = advance(self.db, self.task['id'], session_creator=callback, preparer=callback, generator=callback)
        self.assertEqual(state['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(state['attempts'], [])
        callback.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'ONLY_EXPLICIT_LUNA'):
            advance(self.db, self.task['id'], executor='SOL', session_creator=callback)

    def test_source_queue_runs_three_fake_stages_and_keeps_full_manual_answer(self):
        self.enqueue()
        self.assertEqual(advance(self.db, self.task['id'], executor='LUNA', session_creator=lambda **_: URL)['phase'], 'READY_FOR_PREPARATION')
        self.assertEqual(advance(self.db, self.task['id'], executor='LUNA', preparer=self.prepare)['phase'], 'ATTACHMENTS_READY')
        projected = _source_review(self.db, self.task['id'])[2]
        self.assertEqual(projected['review_origin'], 'INITIAL_SOURCE_CLARITY_REVIEW')
        self.assertEqual(projected['original_sent_at'], self.message.sent_at_local)
        self.assertTrue(projected['original_sender_known'])
        self.assertFalse(projected['new_human_review'])
        self.assertEqual(advance(self.db, self.task['id'], executor='LUNA', generator=self.generate)['phase'], 'GENERATED')
        answer = self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        self.assertEqual((answer['body'], answer['binding_id'], answer['state'], answer['review_status']),
                         (self.fixture_answer, self.binding, 'PENDING', 'NOT_REQUIRED'))
        self.assertEqual(self.business_counts()['reviews'], 0)
        self.assertEqual(self.business_counts()['performance_units'], 0)
        self.assertEqual(self.source_review_count(), 1)
        callback = Mock(side_effect=AssertionError('No repeat generation'))
        self.assertEqual(advance(self.db, self.task['id'], executor='LUNA', generator=callback)['phase'], 'GENERATED')
        callback.assert_not_called()

    def test_generated_source_still_requires_original_evidence_before_delivery(self):
        self.enqueue()
        advance(self.db, self.task['id'], executor='LUNA', session_creator=lambda **_: URL)
        advance(self.db, self.task['id'], executor='LUNA', preparer=self.prepare)
        advance(self.db, self.task['id'], executor='LUNA', generator=self.generate)
        with self.raw.connect() as db:
            db.execute('UPDATE events SET auto_reply_allowed=0')
        before = self.business_counts()
        self.assertEqual(get(self.db, self.task['id'])['phase'], 'NEEDS_ATTENTION')
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(self.source_review_count(), 1)

    def test_original_photo_metadata_and_projection_support_fast_upload(self):
        photo = self.base / 'fixture-original.png'
        Image.new('RGB', (20, 20), 'white').save(photo)
        message, receipt, outcome = self.receive('photo', photo=photo)
        draft = self.create(receipt)
        task = self.review(draft)
        self.confirm_fixture_ack(outcome.message_id)
        queue = self.admit(task)
        self.assertEqual(queue['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(advance(self.db, task['id'], executor='LUNA', session_creator=lambda **_: URL)['phase'], 'READY_FOR_PREPARATION')
        self.assertEqual(advance(self.db, task['id'], executor='LUNA', preparer=self.prepare)['phase'], 'ATTACHMENTS_READY')
        source = _source_review(self.db, task['id'])[2]
        self.assertEqual(source['reviewed_image_hashes'], {str(photo.resolve()): sha256(photo.read_bytes()).hexdigest()})
        original = json.loads(self.db.one('SELECT attachments FROM messages WHERE id=?', (outcome.message_id,))[0])[0]
        self.assertEqual(original['collector_message_id'], message.message_id)
        self.assertEqual(original['media_id'], 'fixture-photo-photo')
        self.assertEqual(set(draft['payload']['attachments'][0]), {'path', 'sha256', 'provenance'})

    def test_event_policy_revocation_blocks_review_without_changing_case(self):
        before = self.business_counts()
        with self.raw.connect() as db:
            db.execute('UPDATE events SET auto_reply_allowed=0')
        with self.assertRaisesRegex(ValueError, 'Event policy'):
            self.review()
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(self.source_review_count(), 0)

    def test_waiting_review_is_not_repeated_when_source_becomes_invalid(self):
        self.task = self.review()
        self.assertEqual(self.admit()['phase'], 'WAITING_ACK')
        with self.raw.connect() as db:
            db.execute('UPDATE messages SET time_confidence=?', ('low',))
        self.confirm_fixture_ack()
        self.assertEqual(resume_pending(self.db)[0]['phase'], 'NEEDS_ATTENTION')
        self.assertEqual(self.source_review_count(), 1)
        self.assertEqual(self.business_counts()['runs'], 0)

    def test_source_change_before_execution_prevents_all_callbacks(self):
        self.enqueue()
        with self.raw.connect() as db:
            db.execute('UPDATE messages SET raw_payload=?', (encode({'sender_role': 'teacher'}),))
        callback = Mock(side_effect=AssertionError('No source-invalid execution'))
        state = advance(self.db, self.task['id'], executor='LUNA', session_creator=callback)
        self.assertEqual(state['phase'], 'NEEDS_ATTENTION')
        callback.assert_not_called()

    def test_review_rejects_modified_question_fields_or_original_text(self):
        payload = dict(self.draft['payload'])
        payload['request_text'] = 'changed original request'
        with self.db.transaction():
            raw = encode(payload)
            self.db.execute('UPDATE operator_draft_revisions SET payload=?,payload_sha256=?', (raw, sha256(raw.encode()).hexdigest()))
        with self.assertRaisesRegex(ValueError, 'source fields changed'):
            self.review()
        self.assertEqual(self.source_review_count(), 0)
        self.assertEqual(self.business_counts()['messages'], 1)

    def test_unverified_missing_time_and_backfill_never_make_source_drafts(self):
        _, backfill, _ = self.receive('history', mode=SyncMode.BACKFILL)
        before = self.db.one('SELECT COUNT(*) FROM operator_drafts')[0]
        with self.assertRaises(ValueError):
            self.create(backfill)
        with self.raw.connect() as db:
            db.execute('UPDATE messages SET sent_at_local=NULL WHERE message_id=?', (self.message.message_id,))
        with self.assertRaises(ValueError):
            self.create(self.receipt)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM operator_drafts')[0], before)

    def test_original_source_photo_changed_blocks_clarity_review(self):
        photo = self.base / 'source-photo.png'
        Image.new('RGB', (20, 20), 'white').save(photo)
        _, receipt, _ = self.receive('picture', photo=photo)
        draft = self.create(receipt)
        photo.write_bytes(b'changed file fixture')
        with self.assertRaisesRegex(ValueError, 'attachments'):
            self.review(draft)
        self.assertEqual(self.source_review_count(), 0)

    def test_changed_review_audit_is_rejected_before_freezing(self):
        task = self.review()
        with self.db.transaction():
            self.db.execute("UPDATE audit SET details='{}' WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")
        self.confirm_fixture_ack()
        with self.assertRaisesRegex(ValueError, 'clarity review audit'):
            self.admit(task)
        self.assertEqual(self.business_counts()['runs'], 0)

    def test_plain_followup_keeps_one_case_version_and_original_performance_time(self):
        original_task = self.review()
        ledger = PerformanceLedger(self.db)
        unit = ledger.create_unit(self.outcome.message_id, '语法填空', scope_key='fixture-one-material',
            grouping_reason='fixture confirmed one material', question_id=self.outcome.question_id)
        _, receipt, outcome = self.receive('followup', intent=Intent.FOLLOWUP,
            sent='2026-09-30T23:10:00+08:00', question_id=self.outcome.question_id)
        follow_task = self.review(self.create(receipt))
        self.assertEqual((follow_task['case_id'], follow_task['question_id']), (original_task['case_id'], original_task['question_id']))
        self.assertEqual(self.business_counts()['cases'], 1)
        self.assertEqual(self.business_counts()['question_versions'], 1)
        self.assertEqual(self.business_counts()['performance_units'], 1)
        row = self.db.one('SELECT question_time,category FROM performance_units WHERE id=?', (unit,))
        self.assertEqual(tuple(row), (self.message.sent_at_local, 'REGULAR'))
        with self.assertRaisesRegex(ValueError, 'context is stale|Stale turn'):
            validate_reviewed_source_task(self.db, original_task['id'])

    def test_frozen_source_reopens_without_another_review_or_task(self):
        self.enqueue()
        before = self.business_counts()
        audit_count = self.source_review_count()
        self.db.close()
        self.db = Store(self.base / 'business.db')
        self.tasks = SourceQuestionTasks(self.db)
        self.assertEqual(self.admit()['run_id'], self.queue['run_id'])
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(self.source_review_count(), audit_count)


if __name__ == '__main__':
    unittest.main()
