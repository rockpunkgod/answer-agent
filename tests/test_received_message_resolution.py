"""Isolated collector fixtures only; no real student or desktop is accessed."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_storage import CollectorStore
from helpdesk.domain import Intent, Option, Question
from helpdesk.message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.performance import PerformanceLedger
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store, encode, now


class ReceivedMessageResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collector = CollectorStore(self.root / 'collector.db')
        self.business = Store(self.root / 'business.db')
        self.binding = Helpdesk(self.business).bind('fixture-room', 'fixture-student', 'fixture only', verified=True)
        self.ack = CollectorDispatcher(self.collector, self.business)
        self.dispatch = CollectorDispatcher(self.collector, self.business, processing_mode='CASE_RESOLUTION')

    def tearDown(self):
        self.business.close()
        self.tmp.cleanup()

    def receive(self, source_id='receipt', *, sent='2026-09-30T22:58:00+08:00', mode=SyncMode.LIVE, **values):
        utc, local = normalize_sent_time(sent)
        message = NormalizedMessage(source_type='wecom_archive', source_message_id=source_id,
            room_id='fixture-room', sender_id='fixture-student', raw_content='请问这道题为什么不选B？',
            normalized_text='请问这道题为什么不选B？', sent_at_raw=sent, sent_at_utc=utc, sent_at_local=local,
            ingested_at='2026-09-30T23:05:00+08:00', **values)
        state = self.collector.get_sync_state('fixture-source', mode)
        self.collector.persist_batch('fixture-source', mode, MessageBatch((message,), source_id), state['cursor'])
        self.ack.drain()
        task = self.business.one('SELECT * FROM collector_answer_tasks WHERE collector_message_id=?', (message.message_id,))
        return message, dict(task)

    @staticmethod
    def question(stem='fixture question'):
        return Question('12', stem, stem, tuple(Option.confirmed(label, value, i, 'fixture transcription')
            for i, (label, value) in enumerate(zip('ABCD', ('first', 'second', 'third', 'fourth')))), 'fixture transcription')

    def decision(self, task, *, intent=Intent.NEW, **values):
        if intent == Intent.NEW:
            values.setdefault('verified_question', self.question())
            values.setdefault('raw_material', 'fixture passage')
            values.setdefault('verified_material', 'fixture passage')
        return self.dispatch.received_incoming(task['id'], intent=intent, **values)

    def resolve(self, task, decision=None, *, dispatcher=None):
        return (dispatcher or self.dispatch).resolve(task['id'], decision or self.decision(task),
            reviewer='fixture source reviewer', rationale='fixture has complete source and clear question', confidence=1)

    def counts(self):
        return {name: self.business.one('SELECT COUNT(*) FROM ' + name)[0] for name in
                ('messages', 'outbox', 'cases', 'materials', 'question_versions', 'questions', 'turns', 'audit',
                 'answers', 'runs', 'performance_units')}

    def test_in_place_link_preserves_original_transport_and_unknown_ack(self):
        message, task = self.receive()
        before = dict(self.business.one('SELECT * FROM messages'))
        with self.business.transaction():
            self.business.execute("UPDATE outbox SET state='SEND_UNKNOWN',simulated=0")
        ack = dict(self.business.one('SELECT * FROM outbox'))
        raw = dict(self.collector.get_message(message.message_id))
        result = self.resolve(task)
        after = dict(self.business.one('SELECT * FROM messages'))
        self.assertEqual(result.message_id, before['id'])
        self.assertEqual(result.status, 'LINKED')
        self.assertIsNotNone(result.turn_id)
        for key in ('id', 'binding_id', 'source', 'platform_id', 'observation_id', 'observed_at', 'raw_text',
                    'attachments', 'fingerprint', 'created_at', 'source_sent_at', 'source_time_evidence'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(ack, dict(self.business.one('SELECT * FROM outbox')))
        current = dict(self.collector.get_message(message.message_id))
        self.assertEqual({k: v for k, v in raw.items() if k != 'processed_at'},
                         {k: v for k, v in current.items() if k != 'processed_at'})
        self.assertEqual(self.counts()['performance_units'], 0)
        self.assertEqual(self.counts()['runs'], 0)
        self.assertEqual(self.counts()['answers'], 0)
        self.assertEqual(after['source_sent_at'], '2026-09-30T22:58:00+08:00')

    def test_ack_only_mode_does_not_enable_teaching(self):
        _, task = self.receive()
        before = self.counts()
        with self.assertRaisesRegex(ValueError, 'ACK_ONLY'):
            self.resolve(task, dispatcher=self.ack)
        self.assertEqual(before, self.counts())

    def test_original_sent_ack_survives_idempotent_replay_and_event_restart(self):
        _, task = self.receive()
        with self.business.transaction():
            self.business.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=0")
        first = self.resolve(task)
        before = self.counts()
        ack = dict(self.business.one('SELECT * FROM outbox'))
        # Newly parsed options have different internal UUIDs, with identical content.
        replay = self.resolve(task, self.decision(task))
        with self.collector.connect() as db:
            db.execute('UPDATE events SET processed_at=NULL')
        self.dispatch.drain()
        self.assertEqual(first, replay)
        self.assertEqual(before, self.counts())
        self.assertEqual(ack, dict(self.business.one('SELECT * FROM outbox')))
        self.assertEqual(self.business.one('SELECT state FROM collector_answer_tasks')[0], 'RESOLVED')

    def test_changed_decision_requires_a_new_correction_message(self):
        _, task = self.receive()
        self.resolve(task)
        before = self.counts()
        with self.assertRaisesRegex(ValueError, 'different decision'):
            self.resolve(task, self.decision(task, verified_question=self.question('changed condition')))
        self.assertEqual(before, self.counts())

    def test_every_transport_field_is_immutable(self):
        _, task = self.receive()
        original = self.decision(task)
        before = self.counts()
        replacements = dict(binding_id='another-student', text='replacement', source='mock', platform_id='another-id',
            observation_id='synthetic', observed_at='2026-10-01T00:00:00+08:00', attachments=({'path': 'invented'},),
            source_sent_at='2026-09-30T23:05:00+08:00', source_time_evidence={'source': 'acquisition_time'})
        for field, value in replacements.items():
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'transport fields'):
                self.resolve(task, replace(original, **{field: value}))
            self.assertEqual(before, self.counts())
        with self.assertRaisesRegex(ValueError, 'business decision fields'):
            self.dispatch.received_incoming(task['id'], intent=Intent.NEW, source_sent_at=original.source_sent_at)

    def test_cross_student_references_are_rejected_without_linking(self):
        other = Helpdesk(self.business).bind('fixture-room', 'other-student', 'other fixture', verified=True)
        foreign = Helpdesk(self.business).ingest(Incoming(other, 'foreign fixture', intent=Intent.NEW,
            verified_question=self.question(), raw_material='passage', verified_material='passage'))
        material = self.business.one('SELECT material_id FROM questions WHERE id=?', (foreign.question_id,))[0]
        _, task = self.receive()
        before = self.counts()
        for key, value in dict(quote_message_id=foreign.message_id, case_id=foreign.case_id,
                              question_id=foreign.question_id, material_id=material).items():
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'original student'):
                self.resolve(task, self.decision(task, **{key: value}))
            self.assertEqual(before, self.counts())

    def test_backfill_is_held_and_cannot_be_promoted(self):
        _, task = self.receive(mode=SyncMode.BACKFILL)
        self.assertEqual(task['state'], 'ACK_HELD')
        self.assertEqual(self.counts()['messages'], 0)
        self.assertEqual(self.counts()['outbox'], 0)
        with self.assertRaisesRegex(ValueError, 'Original ACK_ONLY'):
            self.decision(task)
        with self.assertRaises(ValueError):
            self.dispatch.resolve(task['id'], Incoming(self.binding, 'fixture', intent=Intent.NEW),
                reviewer='fixture', rationale='fixture', confidence=1)
        self.assertEqual(self.counts()['cases'], 0)

    def test_event_policy_is_rechecked_even_after_success(self):
        message, task = self.receive()
        first = self.resolve(task)
        before = self.counts()
        with self.collector.connect() as db:
            db.execute('UPDATE events SET auto_reply_allowed=0 WHERE message_id=?', (message.message_id,))
        with self.assertRaisesRegex(ValueError, 'Event policy'):
            self.resolve(task)
        self.assertEqual(first.status, 'LINKED')
        self.assertEqual(before, self.counts())

    def test_collector_conflict_after_ack_does_not_create_case(self):
        message, task = self.receive()
        before = self.counts()
        changed = replace(message, message_id='conflicting-copy', raw_content='changed original body')
        self.collector.persist_batch('fixture-source', SyncMode.LIVE, MessageBatch((changed,), 'second'), 'receipt')
        with self.assertRaisesRegex(ValueError, 'source conflict'):
            self.resolve(task)
        self.assertEqual(before, self.counts())

    def test_source_policy_sender_and_time_changes_are_rejected(self):
        message, task = self.receive()
        before = self.counts()
        checks = (('sender_id', 'another-student'), ('room_id', 'another-room'), ('time_confidence', 'low'),
                  ('source_confidence', 'low'), ('parse_status', 'pending_time_verification'),
                  ('sent_at_local', '2026-09-30T23:05:00+08:00'),
                  ('raw_payload', encode({'sender_role': 'teacher'})), ('raw_payload', encode({'is_self': True})))
        original = dict(self.collector.get_message(message.message_id))
        for key, value in checks:
            with self.collector.connect() as db:
                db.execute('UPDATE messages SET ' + key + '=? WHERE message_id=?', (value, message.message_id))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.resolve(task)
            self.assertEqual(before, self.counts())
            with self.collector.connect() as db:
                db.execute('UPDATE messages SET ' + key + '=? WHERE message_id=?', (original[key], message.message_id))
        for kwargs in ({'self_sender_ids': ('fixture-student',)}, {'teacher_sender_ids': ('fixture-student',)}):
            dispatcher = CollectorDispatcher(self.collector, self.business, processing_mode='CASE_RESOLUTION', **kwargs)
            with self.assertRaisesRegex(ValueError, 'verified student source'):
                self.resolve(task, dispatcher=dispatcher)
            self.assertEqual(before, self.counts())

    def test_business_source_conflict_and_invalid_provenance_are_rejected(self):
        _, task = self.receive()
        decision = self.decision(task)
        evidence = decision.source_time_evidence
        with self.business.transaction():
            self.business.execute('INSERT INTO source_conflicts VALUES(?,?,?,?,?)',
                ('fixture-conflict', task['business_message_id'], 'conflicting', '[]', now()))
        before = self.counts()
        with self.assertRaisesRegex(ValueError, 'unresolved source conflict'):
            self.resolve(task, decision)
        self.assertEqual(before, self.counts())
        with self.business.transaction():
            self.business.execute('DELETE FROM source_conflicts')
            self.business.execute('UPDATE messages SET source_time_evidence=?',
                                  (encode({**evidence, 'evidence': {'collector_message_id': 'invented'}}),))
        with self.assertRaisesRegex(ValueError, 'time evidence'):
            self.resolve(task)
        self.assertEqual(self.counts()['cases'], 0)

    def test_complete_media_proof_is_preserved_and_changed_file_is_held(self):
        photo = self.root / 'fixture-original.png'
        photo.write_bytes(b'fixture local original media')
        digest = sha256(photo.read_bytes()).hexdigest()
        _, task = self.receive(message_type='image', media_id='fixture-media', local_media_path=str(photo), media_hash=digest)
        row = self.business.one('SELECT * FROM messages')
        image = json.loads(row['attachments'])[0]
        self.assertEqual((image['path'], image['sha256']), (str(photo.resolve()), digest))
        self.assertEqual(image['media_id'], 'fixture-media')
        self.assertEqual(image['collector_message_id'], task['collector_message_id'])
        self.assertIn('collector original media', image['provenance'])
        photo.write_bytes(b'changed bytes')
        with self.assertRaisesRegex(ValueError, 'attachments'):
            self.resolve(task)
        self.assertEqual(self.counts()['cases'], 0)
        self.assertEqual(self.business.one('SELECT attachments FROM messages')[0], row['attachments'])

    def test_gui_photo_without_platform_media_id_keeps_its_hash(self):
        photo = self.root / 'gui-photo.png'
        photo.write_bytes(b'fixture GUI image')
        utc, local = normalize_sent_time('2026-09-30T23:10:00+08:00')
        image = NormalizedMessage(source_type='windows_gui', room_id='fixture-room', sender_id='fixture-student',
            message_type='image', local_media_path=str(photo), media_hash=sha256(photo.read_bytes()).hexdigest(),
            sent_at_raw='2026-09-30T23:10:00+08:00', sent_at_utc=utc, sent_at_local=local,
            raw_payload={'identity_verified': True})
        self.collector.persist_batch('fixture-gui', SyncMode.LIVE, MessageBatch((image,), 'position'), None)
        self.ack.drain()
        task = dict(self.business.one('SELECT * FROM collector_answer_tasks'))
        attachments = json.loads(self.business.one('SELECT attachments FROM messages')[0])
        self.assertEqual(attachments[0]['sha256'], image.media_hash)
        with self.collector.connect() as db:
            db.execute('UPDATE messages SET raw_payload=?', (encode({'identity_verified': 'true'}),))
        with self.assertRaises(ValueError):
            self.resolve(task)
        self.assertEqual(self.counts()['cases'], 0)

    def test_interruption_after_business_commit_recovers_exact_original_turn(self):
        message, task = self.receive()
        execute = self.business.execute
        def fail_task_commit(sql, params=()):
            if sql.startswith('UPDATE collector_answer_tasks SET'):
                raise RuntimeError('fixture interruption after business resolution commit')
            return execute(sql, params)
        with patch.object(self.business, 'execute', side_effect=fail_task_commit):
            with self.assertRaises(RuntimeError):
                self.resolve(task)
        self.assertEqual(self.business.one('SELECT state FROM collector_answer_tasks')[0], 'ACK_QUEUED')
        self.assertIsNone(self.collector.get_message(message.message_id)['processed_at'])
        record = json.loads(self.business.one("SELECT details FROM audit WHERE event='RECEIVED_MESSAGE_RESOLVED'")[0])
        before = self.counts()
        path = self.business.path
        self.business.close()
        self.business = Store(path)
        self.dispatch = CollectorDispatcher(self.collector, self.business, processing_mode='CASE_RESOLUTION')
        outcome = self.resolve(task)
        self.assertEqual(asdict(outcome), record['result'])
        self.assertEqual(before, self.counts())
        self.assertEqual(self.business.one('SELECT state FROM collector_answer_tasks')[0], 'RESOLVED')

    def test_parallel_resolvers_link_and_audit_only_once(self):
        _, task = self.receive()
        decision = self.decision(task)
        def worker(_):
            business = Store(self.business.path)
            try:
                dispatcher = CollectorDispatcher(self.collector, business, processing_mode='CASE_RESOLUTION')
                return self.resolve(task, decision, dispatcher=dispatcher)
            finally:
                business.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, (1, 2)))
        self.assertEqual(outcomes[0], outcomes[1])
        for table in ('messages', 'cases', 'questions', 'question_versions', 'turns', 'outbox'):
            self.assertEqual(self.counts()[table], 1, table)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM audit WHERE event='RECEIVED_MESSAGE_RESOLVED'")[0], 1)

    def test_unknown_association_is_paused_once_without_repeating_ack(self):
        _, task = self.receive()
        result = self.resolve(task, self.decision(task, intent=Intent.FOLLOWUP))
        self.assertEqual(result.status, 'NEEDS_REVIEW')
        before = self.counts()
        self.assertEqual(self.resolve(task, self.decision(task, intent=Intent.FOLLOWUP)), result)
        self.assertEqual(before, self.counts())
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox WHERE purpose='ACK'")[0], 1)
        self.assertEqual(self.counts()['cases'], 0)

    def test_late_followup_retains_first_message_time_and_one_unit(self):
        first, task = self.receive('first', sent='2026-09-30T22:50:00+08:00')
        original = self.resolve(task)
        ledger = PerformanceLedger(self.business)
        unit = ledger.create_unit(original.message_id, '语法填空', scope_key='fixture-material',
            grouping_reason='fixture verified one material', question_id=original.question_id)
        for i in range(3):
            _, follow = self.receive('follow-' + str(i), sent='2026-09-30T23:10:00+08:00')
            result = self.resolve(follow, self.decision(follow, intent=Intent.FOLLOWUP,
                question_id=original.question_id, quote_message_id=original.message_id))
            ledger.link_activity(unit, result.message_id, question_id=result.question_id,
                kind='FOLLOWUP', reason='fixture same material ordinary followup')
            self.assertEqual(result.question_id, original.question_id)
        self.assertEqual(self.counts()['cases'], 1)
        self.assertEqual(self.counts()['question_versions'], 1)
        self.assertEqual(self.counts()['performance_units'], 1)
        row = self.business.one('SELECT * FROM performance_units WHERE id=?', (unit,))
        self.assertEqual((row['question_time'], row['category']), (first.sent_at_local, 'REGULAR'))

    def test_legacy_resolved_task_without_decision_key_is_readable(self):
        _, task = self.receive()
        first = self.resolve(task)
        saved = json.loads(self.business.one('SELECT decision_json FROM collector_answer_tasks')[0])
        saved.pop('decision_key')
        with self.business.transaction():
            self.business.execute('UPDATE collector_answer_tasks SET decision_json=?', (encode(saved),))
        before = self.counts()
        self.assertEqual(self.resolve(task), first)
        self.assertEqual(before, self.counts())

    def test_case_resolution_legacy_task_without_decision_key_is_readable(self):
        utc, local = normalize_sent_time('2026-09-30T23:10:00+08:00')
        message = NormalizedMessage(source_type='wecom_archive', source_message_id='legacy-case',
            room_id='fixture-room', sender_id='fixture-student', raw_content='fixture question',
            sent_at_raw='2026-09-30T23:10:00+08:00', sent_at_utc=utc, sent_at_local=local)
        self.collector.persist_batch('fixture-case', SyncMode.LIVE, MessageBatch((message,), 'one'), None)
        self.dispatch.drain()
        task = dict(self.business.one('SELECT * FROM collector_answer_tasks'))
        decision = Incoming(self.binding, 'ignored replacement', intent=Intent.NEW,
            verified_question=self.question(), raw_material='fixture passage', verified_material='fixture passage')
        first = self.resolve(task, decision)
        saved = json.loads(self.business.one('SELECT decision_json FROM collector_answer_tasks')[0])
        saved.pop('decision_key')
        with self.business.transaction():
            self.business.execute('UPDATE collector_answer_tasks SET decision_json=?', (encode(saved),))
        before = self.counts()
        self.assertEqual(self.resolve(task, replace(decision, verified_question=self.question())), first)
        self.assertEqual(before, self.counts())


if __name__ == '__main__':
    unittest.main()
