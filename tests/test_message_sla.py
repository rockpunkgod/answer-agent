"""Synthetic clocks and delivery evidence in temporary SQLite; no real sending."""
from hashlib import sha256
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent, new_id
from helpdesk.message_sla import message_sla
from helpdesk.performance_rules import timestamp
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store, encode, now
from helpdesk.workflow import Workflow
from tests import test_manual_delivery_registration as manual_fixture
from tests import test_delivery_batches as batch_fixture


class MessageSLATests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Store(Path(temporary.name) / 'synthetic.db')
        self.addCleanup(self.db.close)
        self.app = Helpdesk(self.db)
        self.binding = self.app.bind('synthetic-group', 'synthetic-student', 'Anonymous fixture', verified=True)
        self.incoming = Incoming(self.binding, 'Synthetic question', Intent.NEW, source='collector:synthetic',
            platform_id='synthetic-1', observed_at='2026-10-01T23:05:00+08:00',
            source_sent_at='2026-10-01T22:58:00+08:00',
            source_time_evidence={'source': 'wecom_original', 'message_locator': 'synthetic-1',
                                  'evidence': {'fixture': 'Synthetic original message, no platform access'}},
            verified_question=demo_question(), raw_material='Synthetic passage', verified_material='Synthetic passage')
        self.original = self.app.ingest(self.incoming)
        self.ack = self.db.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (self.original.message_id,))
        # This is the later ingestion time, deliberately different from original and observation.
        self.db.execute('UPDATE messages SET created_at=? WHERE id=?',
                        ('2026-10-01T23:05:10+08:00', self.original.message_id))
        self.flow = Workflow(self.db, desktop=SimpleNamespace(simulated=True))

    def metric(self, as_of='2026-10-01T23:30:00+08:00'):
        return next(row for row in message_sla(self.db, as_of=as_of) if row['message_id'] == self.original.message_id)

    def receipt(self, sent_at, *, row=None, simulated=False, **changes):
        row = row or self.ack
        self.db.execute('UPDATE outbox SET simulated=? WHERE id=?', (int(simulated), row['id']))
        proof = {'confirmed': True, 'simulated': simulated, 'confirmed_at': sent_at,
                 'body_hash': sha256(row['body'].encode()).hexdigest(), 'outbox_id': row['id'],
                 'binding_id': self.binding, 'group_key': 'synthetic-group', 'student_key': 'synthetic-student'} | changes
        self.flow._record_check(row, proof, simulated=simulated)
        return proof

    def answer(self):
        oid = new_id()
        question = self.db.one('SELECT * FROM questions WHERE id=?', (self.original.question_id,))
        self.db.execute('''INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,
            question_version,context_revision,idempotency_key,state,created_at,simulated)
            VALUES(?,?,?,?,?,'ANSWER',?,?,?,?,?,?,0)''', (oid, self.original.message_id, self.original.case_id,
                self.original.turn_id, self.binding, 'Synthetic delivered answer', question['current_version'],
                question['context_revision'], 'synthetic-answer:' + oid, 'PENDING', now()))
        return self.db.one('SELECT * FROM outbox WHERE id=?', (oid,))

    def test_original_time_includes_collection_delay_and_dashboard_uses_same_result(self):
        self.receipt('2026-10-01T23:06:00+08:00')
        item = self.metric()
        self.assertEqual((item['collection_seconds'], item['ack_seconds']), (420, 480))
        self.assertEqual(item['time_basis'], 'STUDENT_ORIGINAL_SEND_TIME')
        self.assertFalse(item['ack_overdue'])
        self.assertEqual(self.flow.dashboard()['health']['sla'][0]['ack_seconds'], 480)
        self.assertEqual(self.app.ingest(self.incoming).status, 'DUPLICATE')
        changes = self.db.connection.total_changes
        self.assertEqual(self.metric(), item)
        self.assertEqual(self.db.connection.total_changes, changes)

    def test_late_confirmed_ack_remains_late_after_delivery(self):
        self.receipt('2026-10-01T23:14:00+08:00')
        item = self.metric()
        self.assertEqual(item['ack_seconds'], 960)
        self.assertTrue(item['ack_overdue'])
        self.assertEqual(item['ack_status'], 'VERIFIED')

    def test_fifteen_minutes_is_inclusive_and_offset_is_respected(self):
        for sent, expected in [('2026-10-01T15:13:00Z', False), ('2026-10-01T15:13:00.001Z', True)]:
            with self.subTest(sent=sent):
                self.receipt(sent)
                self.assertEqual(self.metric()['ack_overdue'], expected)

    def test_unsent_ack_becomes_overdue_from_original_time(self):
        item = self.metric('2026-10-01T23:14:00+08:00')
        self.assertEqual(item['ack_status'], 'PENDING')
        self.assertIsNone(item['ack_seconds'])
        self.assertTrue(item['ack_overdue'])

    def test_missing_invalid_or_conflicting_time_never_falls_back_to_ingestion(self):
        self.receipt('2026-10-01T23:06:00+08:00')
        good = encode(self.incoming.source_time_evidence)
        for sent, proof in [(None, good), ('2026-10-01T22:58:00', good),
                (self.incoming.source_sent_at, None), (self.incoming.source_sent_at, 'broken json'),
                (self.incoming.source_sent_at, encode({'source': 'acquisition', 'message_locator': 'x', 'evidence': 'x'})),
                (self.incoming.source_sent_at, encode({'source': 'wecom_original', 'evidence': 'x'})),
                ('2026-10-02T22:58:00+08:00', good)]:
            with self.subTest(sent=sent, proof=proof):
                self.db.execute('UPDATE messages SET source_sent_at=?,source_time_evidence=? WHERE id=?',
                                (sent, proof, self.original.message_id))
                item = self.metric()
                self.assertEqual(item['time_status'], 'TIME_REVIEW_REQUIRED')
                self.assertIsNone(item['ack_seconds'])
                self.assertIsNone(item['ack_overdue'])

    def test_duplicate_original_time_conflict_stays_pending(self):
        from dataclasses import replace
        self.receipt('2026-10-01T23:06:00+08:00')
        self.app.ingest(replace(self.incoming, source_sent_at='2026-10-01T22:57:00+08:00'))
        self.assertEqual(self.metric()['time_status'], 'TIME_REVIEW_REQUIRED')
        self.assertEqual(self.db.one('SELECT source_sent_at FROM messages WHERE id=?',
                                    (self.original.message_id,))[0], self.incoming.source_sent_at)

    def test_observation_cannot_precede_the_original_message(self):
        self.db.execute('UPDATE messages SET observed_at=? WHERE id=?',
                        ('2026-10-01T22:57:00+08:00', self.original.message_id))
        self.assertEqual(self.metric()['time_status'], 'TIME_REVIEW_REQUIRED')
        self.assertIsNone(self.metric()['collection_seconds'])

    def test_simulated_and_unverified_receipts_cannot_establish_real_sla(self):
        self.receipt('2026-10-01T23:06:00+08:00', simulated=True)
        self.assertIsNone(self.metric()['ack_seconds'])
        self.receipt('2026-10-01T23:06:00+08:00', group_key='wrong-group')
        self.assertIsNone(self.metric()['ack_seconds'])
        self.receipt('2026-10-01T23:06:00+08:00', outbox_id=None)
        self.assertIsNone(self.metric()['ack_seconds'])
        self.receipt('2026-10-01T23:06:00+08:00')
        self.db.execute("INSERT INTO delivery_checks VALUES(?,?,'SEND_UNKNOWN',?,?)",
                        (new_id(), self.ack['id'], encode({'confirmed': False}), now()))
        self.assertIsNone(self.metric()['ack_seconds'])
        self.assertEqual(self.metric()['ack_status'], 'DELIVERY_REVIEW_REQUIRED')

    def test_legacy_missing_receipt_time_and_impossible_receipt_times_are_pending(self):
        for sent in (None, 'bad', '2026-10-01T23:06:00', '2026-10-01T22:57:00+08:00', '2026-10-02T12:00:00Z'):
            with self.subTest(sent=sent):
                self.receipt(sent)
                self.assertIsNone(self.metric()['ack_seconds'])
                self.assertIsNone(self.metric()['ack_overdue'])

    def test_draft_unknown_and_answer_policy_are_not_completion(self):
        row = self.answer()
        self.assertIsNone(self.metric()['answer_seconds'])
        self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (row['id'],))
        self.assertEqual(self.metric()['answer_status'], 'SEND_UNKNOWN')
        self.receipt('2026-10-01T23:20:00+08:00', row=row)
        item = self.metric()
        self.assertEqual(item['answer_seconds'], 22 * 60)
        self.assertEqual(item['answer_status'], 'VERIFIED')
        self.assertIsNone(item['answer_overdue'])
        self.assertIsNone(item['answer_limit_seconds'])

    def test_simulated_source_does_not_become_real_from_a_receipt(self):
        self.receipt('2026-10-01T23:06:00+08:00')
        for source in ('mock', 'OPERATOR_TEST'):
            self.db.execute('UPDATE messages SET source=? WHERE id=?', (source, self.original.message_id))
            self.assertEqual(self.metric()['time_status'], 'SIMULATED_SOURCE')
            self.assertIsNone(self.metric()['ack_seconds'])


class ManualPackageSLATests(unittest.TestCase):
    def test_partial_manual_delivery_is_not_answer_completion(self):
        fixture = manual_fixture.ManualDeliveryRegistrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        # The reused fixture moves its synthetic question to 23:10; give it a
        # consistent synthetic collection time too. No real record is edited.
        fixture.db.execute('UPDATE messages SET observed_at=? WHERE id=?',
                           ('2026-09-30T23:11:00+08:00', fixture.origin['message_id']))
        def metric():
            return next(row for row in message_sla(fixture.db, as_of='2026-10-03T00:00:00+08:00')
                        if row['message_id'] == fixture.origin['message_id'])
        first = fixture.register(part_number=1, total_parts=2)
        self.assertIsNone(metric()['answer_seconds'])
        fixture.register(part_number=2, total_parts=2, content='Second synthetic part.',
                         delivered_at='2026-10-01T10:01:00+08:00')
        self.assertEqual(metric()['answer_seconds'], 651 * 60)
        fixture.db.execute('DELETE FROM delivery_checks WHERE outbox_id=?', (first['recorded_outbox_id'],))
        self.assertIsNone(metric()['answer_seconds'])

    def test_automatic_batch_requires_every_part_receipt(self):
        fixture = batch_fixture.NativeBatchIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        for _ in range(64):
            result = fixture.fx.engine.tick()
            if result['state'] == 'SENT_UI_CONFIRMED':
                break
            self.assertEqual(result['state'], 'PART_DELIVERED')
        self.assertEqual(result['state'], 'SENT_UI_CONFIRMED')
        row = fixture.db.one('SELECT * FROM outbox WHERE id=?', (fixture.oid,))
        # Only the isolated fixture's inconsistent observation clock is repaired.
        fixture.db.execute('UPDATE messages SET observed_at=? WHERE id=?',
                           ('2026-09-30T23:11:00+08:00', row['message_id']))
        def metric():
            return next(item for item in message_sla(fixture.db) if item['message_id'] == row['message_id'])
        expected = (timestamp(row['sent_at']) - timestamp('2026-09-30T23:10:00+08:00')).total_seconds()
        self.assertEqual(metric()['answer_seconds'], expected)
        fixture.db.execute("DELETE FROM delivery_checks WHERE outbox_id=? AND status='PART_UI_CONFIRMED' AND json_extract(evidence,'$.part_number')=2", (fixture.oid,))
        self.assertIsNone(metric()['answer_seconds'])
