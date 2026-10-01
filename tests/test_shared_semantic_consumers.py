"""Shared decisions use anonymous collector/business fixtures, never services."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_storage import CollectorStore
from helpdesk.domain import Intent, Option, Question
from helpdesk.message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.semantic_decisions import COUNTED, CONFIRMED, SharedSemanticDecisions
from helpdesk.service import Helpdesk
from helpdesk.storage import Store, encode


class SharedSemanticConsumersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.collector = CollectorStore(self.root / 'collector.db')
        self.db = Store(self.root / 'business.db')
        self.desk = Helpdesk(self.db)
        self.binding = self.desk.bind('room-fixture', 'student-fixture', 'anonymous', verified=True)
        self.ack = CollectorDispatcher(self.collector, self.db)
        self.resolver = CollectorDispatcher(self.collector, self.db, processing_mode='CASE_RESOLUTION')
        self.shared = SharedSemanticDecisions(self.db, self.collector)
        self.sequence = 0

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def question(self, number='12', stem='anonymous question'):
        return Question(number, stem, stem, tuple(Option.confirmed(label, text, i, 'fixture')
            for i, (label, text) in enumerate(zip('ABCD', ('first', 'second', 'third', 'fourth')))), 'fixture')

    def received(self, *, sent='2026-10-01T22:58:00+08:00', sender='student-fixture', mode=SyncMode.LIVE):
        self.sequence += 1
        utc, local = normalize_sent_time(sent)
        message = NormalizedMessage(source_type='wecom_archive', source_message_id=f'fixture-{self.sequence}',
            room_id='room-fixture', sender_id=sender, raw_content='请问这道题为什么不选B？',
            normalized_text='请问这道题为什么不选B？', sent_at_raw=sent, sent_at_utc=utc, sent_at_local=local,
            ingested_at='2026-10-02T08:05:00+08:00')
        state = self.collector.get_sync_state('fixture', mode)
        self.collector.persist_batch('fixture', mode, MessageBatch((message,), str(self.sequence)), state['cursor'])
        self.ack.drain()
        task = self.db.one('SELECT * FROM collector_answer_tasks WHERE collector_message_id=?', (message.message_id,))
        return message, dict(task)

    def linked(self, intent=Intent.NEW, *, sent='2026-10-01T22:58:00+08:00', sender='student-fixture', **fields):
        message, task = self.received(sent=sent, sender=sender)
        if intent == Intent.NEW:
            fields.setdefault('verified_question', self.question())
            fields.setdefault('raw_material', 'anonymous passage')
            fields.setdefault('verified_material', 'anonymous passage')
        decision = self.resolver.received_incoming(task['id'], intent=intent, **fields)
        outcome = self.resolver.resolve(task['id'], decision, reviewer='fixture resolver',
            rationale='anonymous verified original source and association', confidence=1)
        return message, task, outcome

    def confirm(self, task, kind='阅读理解', **fields):
        return self.shared.confirm(task['id'], question_type=kind, actor='fixture resolver',
                                   evidence='verified type and scope from anonymous fixture', **fields)

    def rows(self, table):
        return self.db.one('SELECT COUNT(*) FROM ' + table)[0]

    def test_same_decision_used_by_both_consumers_only_creates_candidate(self):
        _, task, outcome = self.linked()
        before = {table: self.rows(table) for table in ('messages', 'outbox', 'cases', 'questions', 'turns')}
        decision = self.confirm(task)
        audit_count = self.rows('audit')
        answer = self.shared.answer_input(decision)
        self.assertEqual(self.rows('audit'), audit_count)
        unit = self.shared.counting_unit(decision)
        self.assertEqual(answer['decision_id'], decision)
        self.assertEqual(answer['turn_id'], outcome.turn_id)
        self.assertEqual(answer['collector_task_id'], task['id'])
        record = self.db.one('SELECT * FROM performance_units WHERE id=?', (unit,))
        self.assertEqual(record['status'], 'PENDING')
        self.assertEqual(record['confirmed_quantity'], 0)
        self.assertIsNone(record['completed_at'])
        self.assertEqual(before, {table: self.rows(table) for table in before})
        self.assertEqual(self.rows('reviews'), 0)
        for _ in range(2):
            self.assertEqual(self.confirm(task), decision)
            self.assertEqual(self.shared.counting_unit(decision), unit)
        self.assertEqual(self.rows('performance_units'), 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (CONFIRMED,))[0], 1)
        counted = json.loads(self.db.one('SELECT details FROM audit WHERE event=?', (COUNTED,))[0])
        self.assertEqual(counted['decision_id'], decision)

    def test_empty_evidence_and_unresolved_history_are_rejected(self):
        _, task, _ = self.linked()
        for actor, evidence in (('', 'proof'), ('actor', ''), ('actor', '   ')):
            with self.assertRaises(ValueError):
                self.shared.confirm(task['id'], question_type='阅读理解', actor=actor, evidence=evidence)
        _, historical = self.received(mode=SyncMode.BACKFILL)
        outbox = self.rows('outbox')
        with self.assertRaises(ValueError):
            self.confirm(historical)
        self.assertEqual(self.rows('outbox'), outbox)

    def test_source_tamper_and_revoked_event_invalidate_both_consumers(self):
        message, task, _ = self.linked()
        decision = self.confirm(task)
        with self.collector.connect() as db:
            db.execute('UPDATE messages SET raw_content=? WHERE message_id=?', ('changed source', message.message_id))
        for consumer in (self.shared.answer_input, self.shared.counting_unit):
            with self.assertRaises(ValueError):
                consumer(decision)
        self.assertEqual(self.rows('performance_units'), 0)
        with self.collector.connect() as db:
            db.execute('UPDATE messages SET raw_content=? WHERE message_id=?', ('请问这道题为什么不选B？', message.message_id))
            db.execute('UPDATE events SET auto_reply_allowed=0 WHERE message_id=?', (message.message_id,))
        with self.assertRaises(ValueError):
            self.shared.answer_input(decision)

    def test_cross_student_and_cross_material_units_are_rejected(self):
        _, first, _ = self.linked()
        unit = self.shared.counting_unit(self.confirm(first))
        self.desk.bind('room-fixture', 'other-fixture', 'anonymous other', verified=True)
        _, other, _ = self.linked(sender='other-fixture')
        with self.assertRaises(ValueError):
            self.confirm(other, existing_unit_id=unit)
        _, different, _ = self.linked()
        with self.assertRaises(ValueError):
            self.confirm(different, existing_unit_id=unit)

    def test_old_version_and_changed_audit_rejected(self):
        _, task, outcome = self.linked()
        decision = self.confirm(task)
        self.linked(Intent.CORRECTION, question_id=outcome.question_id,
                    verified_question=self.question(stem='substantive NOT change'))
        with self.assertRaises(ValueError):
            self.shared.answer_input(decision)
        _, task, _ = self.linked()
        decision = self.confirm(task)
        record = self.shared._rows(CONFIRMED, 'decision_id', decision)[0]
        record['evidence'] = 'changed evidence'
        self.db.execute('UPDATE audit SET details=? WHERE event=? AND turn_id=?',
                        (encode(record), CONFIRMED, record['turn_id']))
        with self.assertRaises(ValueError):
            self.shared.counting_unit(decision)

    def test_followup_preserves_first_time_and_requires_existing_question_scope(self):
        _, task, outcome = self.linked(sent='2026-10-01T22:58:00+08:00')
        unit = self.shared.counting_unit(self.confirm(task, '语法填空'))
        before = dict(self.db.one('SELECT * FROM performance_units WHERE id=?', (unit,)))
        _, follow, _ = self.linked(Intent.FOLLOWUP, sent='2026-10-02T00:30:00+08:00', question_id=outcome.question_id)
        decision = self.confirm(follow, '语法填空')
        self.assertEqual(self.shared.counting_unit(decision), unit)
        after = dict(self.db.one('SELECT * FROM performance_units WHERE id=?', (unit,)))
        self.assertEqual(before['question_time'], after['question_time'])
        self.assertEqual(before['first_message_id'], after['first_message_id'])
        self.assertEqual(before['measure_unit'], after['measure_unit'])
        self.assertEqual(self.rows('performance_units'), 1)
        _, orphan, outcome = self.linked()
        _, follow, _ = self.linked(Intent.FOLLOWUP, question_id=outcome.question_id)
        with self.assertRaisesRegex(ValueError, 'EXISTING_SCOPE'):
            self.confirm(follow)

    def test_night_grammar_subquestions_merge_daytime_questions_are_distinct(self):
        _, task, outcome = self.linked(sent='2026-10-01T23:10:00+08:00')
        first = self.shared.counting_unit(self.confirm(task, '语法填空'))
        _, sub, _ = self.linked(Intent.SUBQUESTION, sent='2026-10-02T00:10:00+08:00',
            case_id=outcome.case_id, question_id=outcome.question_id, verified_question=self.question('13'))
        second = self.shared.counting_unit(self.confirm(sub, '语法填空'))
        self.assertEqual(first, second)
        self.assertEqual(self.db.one('SELECT measure_unit FROM performance_units WHERE id=?', (first,))[0], '篇')
        _, task, outcome = self.linked(sent='2026-10-02T12:10:00+08:00')
        first = self.shared.counting_unit(self.confirm(task, '语法填空'))
        _, sub, _ = self.linked(Intent.SUBQUESTION, sent='2026-10-02T12:11:00+08:00',
            case_id=outcome.case_id, question_id=outcome.question_id, verified_question=self.question('13'))
        second = self.shared.counting_unit(self.confirm(sub, '语法填空'))
        self.assertNotEqual(first, second)
        self.assertEqual(self.db.one('SELECT SUM(confirmed_quantity) FROM performance_units')[0], 0)

    def test_correction_supplement_and_dispute_do_not_start_new_units(self):
        for intent in (Intent.CORRECTION, Intent.SUPPLEMENT, Intent.DISPUTE):
            _, task, outcome = self.linked()
            unit = self.shared.counting_unit(self.confirm(task))
            count = self.rows('performance_units')
            fields = {'question_id': outcome.question_id}
            if intent != Intent.DISPUTE:
                fields['verified_question'] = self.question(stem='changed verified stem')
            _, changed, _ = self.linked(intent, **fields)
            decision = self.confirm(changed)
            self.assertEqual(self.shared.counting_unit(decision), unit)
            self.assertEqual(self.rows('performance_units'), count)

    def test_failed_projection_rolls_back_unit_link_and_audit_then_recovers(self):
        _, task, outcome = self.linked()
        decision = self.confirm(task)
        original = self.shared._append
        def crash(event, details):
            if event == COUNTED:
                raise RuntimeError('anonymous injected interruption')
            return original(event, details)
        with patch.object(self.shared, '_append', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.shared.counting_unit(decision)
        self.assertEqual(self.rows('performance_units'), 0)
        self.assertEqual(self.rows('performance_links'), 0)
        unit = self.shared.counting_unit(decision)
        _, follow, _ = self.linked(Intent.FOLLOWUP, question_id=outcome.question_id)
        follow_decision = self.confirm(follow)
        links = self.rows('performance_links')
        with patch.object(self.shared, '_append', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.shared.counting_unit(follow_decision)
        self.assertEqual(self.rows('performance_links'), links)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=? AND turn_id=?',
                                    (COUNTED, self.shared.answer_input(follow_decision)['turn_id']))[0], 0)
        self.assertEqual(self.shared.counting_unit(follow_decision), unit)
        self.assertEqual(self.shared.counting_unit(follow_decision), unit)
        self.assertEqual(self.rows('performance_links'), links + 1)


if __name__ == '__main__':
    unittest.main()
