"""Anonymous persistence and simulated UI protocol tests; no real messages."""
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from helpdesk.delivery import MockDesktop, RetryablePreflightFailure, SimulatedCrash
from helpdesk.delivery_batches import (split_text, read_plan, progress, validate_complete,
    PLAN_EVENT, PART_METHOD, COMPLETE_METHOD)
from helpdesk.domain import Intent
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store, encode
from helpdesk.workflow import DemoGenerationAdapter, Workflow
from tests import test_delivery_tasks as task_fixtures
from tests import test_mcp_group_delivery as native_fixtures


class LongFixture(DemoGenerationAdapter):
    def generate(self, snapshot):
        result = super().generate(snapshot)
        result['text'] += '\n' + ('这是匿名流程测试原文🙂，不是教学答案。\r\n' * 230)
        return result


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.fx = task_fixtures.DeliveryTaskTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.db = self.fx.db
        self.original = self.fx.question()
        self.assertEqual(self.fx.engine.tick()['kind'], 'ACK')
        self.flow = Workflow(self.db, desktop=self.fx.desktop, generation_adapter=LongFixture())
        self.generated = self.flow.generate(self.original.turn_id)
        self.oid = self.generated['outbox_id']
        self.flow.approve(self.oid)
        self.body = self.row()['body']

    def row(self):
        return self.db.one('SELECT * FROM outbox WHERE id=?', (self.oid,))

    def restart(self, desktop=None):
        path = self.db.path
        self.db.close()
        self.db = self.fx.db = Store(path)
        self.fx.app = Helpdesk(self.db)
        self.fx.engine = self.fx.make_engine(desktop)
        self.flow = self.fx.engine.flow

    def test_lossless_unicode_line_breaks_and_size(self):
        body = ('题目🙂 A. first\r\nB. second\r\n' * 90) + '原文结束。'
        parts = split_text(body, 128)
        self.assertEqual(''.join(parts), body)
        self.assertEqual(parts, split_text(body, 128))
        self.assertTrue(all(len(p.encode('utf-16-le')) <= 256 for p in parts))
        self.assertTrue(all(p.strip() for p in parts))
        self.assertFalse(any(a.endswith('\r') and b.startswith('\n') for a, b in zip(parts, parts[1:])))

    def test_one_batch_order_restart_ack_priority_and_only_final_completion(self):
        self.restart()
        self.assertEqual(self.fx.engine.tick()['state'], 'PART_DELIVERED')
        plan = read_plan(self.db, self.row())
        self.assertGreaterEqual(len(plan['parts']), 3)
        self.assertEqual(self.row()['state'], 'PENDING')
        self.assertIsNone(self.row()['sent_at'])
        self.assertNotEqual(self.db.one('SELECT status FROM questions WHERE id=?', (self.original.question_id,))[0], 'WAITING_FOLLOWUP')
        self.restart()
        self.fx.question('another-student-question')
        self.assertEqual(self.fx.engine.tick()['kind'], 'ACK')
        states = []
        for _ in range(len(plan['parts']) - 1):
            result = self.fx.engine.tick()
            states.append(result['state'])
            self.assertEqual(result['attempt'], 1)
        self.assertEqual(states[-1], 'SENT_UI_CONFIRMED')
        self.assertTrue(all(s == 'PART_DELIVERED' for s in states[:-1]))
        self.assertEqual(self.fx.engine.tick()['state'], 'IDLE')
        receipts = [x for x in self.fx.desktop.receipts() if x['outbox_id'].startswith(self.oid)]
        self.assertEqual(''.join(x['body'] for x in receipts), self.body)
        self.assertEqual(len(receipts), len(plan['parts']))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=? AND outbox_id=?', (PLAN_EVENT, self.oid))[0], 1)
        self.assertEqual(self.flow.generate(self.original.turn_id)['outbox_id'], self.oid)
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=? ORDER BY rowid DESC', (self.oid,))[0])
        validate_complete(self.db, self.row(), proof)
        self.assertEqual(proof['verification_method'], COMPLETE_METHOD)
        self.assertIsNone(proof['platform_message_id'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_second_part_unknown_survives_restart_and_readonly_reconciliation(self):
        self.fx.engine.tick()
        self.fx.desktop.fault = 'after_send'
        with self.assertRaises(SimulatedCrash):
            self.fx.engine.tick()
        self.assertEqual(self.row()['state'], 'SENDING')
        self.restart()
        self.assertEqual(self.fx.engine.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(progress(self.db, self.row())['unknown'], 2)
        before = len(self.fx.desktop.receipts())
        self.assertEqual(self.fx.engine.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(len(self.fx.desktop.receipts()), before)
        self.assertEqual(self.flow.inspect_unknown(self.oid), 'PART_DELIVERED')
        self.assertEqual(len(self.fx.desktop.receipts()), before)
        self.fx.desktop.fault = None
        self.assertEqual(self.fx.engine.tick()['part_number'], 3)
        self.assertEqual(len([r for r in self.fx.desktop.receipts() if r['outbox_id'].endswith(':part:1')]), 1)

    def test_retry_budget_is_per_part_and_safe_preflight_does_not_repeat_previous(self):
        self.fx.engine.tick()
        with patch.object(self.fx.desktop, 'preflight', side_effect=RetryablePreflightFailure('WINDOW_UNAVAILABLE')):
            failure = self.fx.engine.tick()
        self.assertEqual((failure['part_number'], failure['attempt'], failure['retry_scheduled']), (2, 1, True))
        self.assertEqual(self.fx.engine.tick()['state'], 'IDLE')
        self.restart()
        self.fx.date += timedelta(seconds=5)
        result = self.fx.engine.tick()
        self.assertEqual((result['part_number'], result['attempt']), (2, 2))
        self.assertEqual(len([r for r in self.fx.desktop.receipts() if r['outbox_id'].endswith(':part:1')]), 1)

    def test_followup_keeps_only_the_part_actually_delivered_and_stops_old_batch(self):
        self.fx.engine.tick()
        actual = self.fx.desktop.receipts()[-1]['body']
        follow = self.fx.app.ingest(Incoming(self.fx.binding, '为什么这样判断？', Intent.FOLLOWUP,
                                            quote_message_id=self.original.message_id))
        context = self.fx.app.context(follow.turn_id)
        self.assertEqual(context['previous_sent_answer'], actual)
        self.assertEqual(context['sent_history'][0]['delivery_method'], PART_METHOD)
        self.assertEqual(context['sent_history'][0]['part_number'], 1)
        self.assertNotEqual(context['previous_sent_answer'], self.body)
        self.assertEqual(self.row()['state'], 'STALE')
        self.assertEqual(self.flow.dispatch(self.oid), 'STALE')
        self.assertEqual(len(self.fx.desktop.receipts()), 2)

    def test_correction_during_part_side_effect_preserves_fact_and_stops_remaining(self):
        send = self.fx.desktop.send
        def correct_after_send(message):
            receipt = send(message)
            self.fx.app.correct_material(self.db.one('SELECT material_id FROM questions WHERE id=?', (self.original.question_id,))[0],
                self.original.message_id, 'Corrected original material', 'Corrected original material')
            return receipt
        with patch.object(self.fx.desktop, 'send', side_effect=correct_after_send):
            self.assertEqual(self.fx.engine.tick()['state'], 'STALE')
        self.assertEqual(len(progress(self.db, self.row())['verified']), 1)
        self.assertEqual(self.fx.engine.tick()['state'], 'IDLE')
        self.assertEqual(len(self.fx.desktop.receipts()), 2)

    def test_stop_tamper_and_out_of_order_receipts_fail_closed(self):
        self.fx.engine.tick()
        self.flow.set_stop(True)
        self.assertEqual(self.fx.engine.tick()['state'], 'STOPPED')
        self.flow.set_stop(False)
        proof_row = self.db.one("SELECT * FROM delivery_checks WHERE outbox_id=? AND status='PART_UI_CONFIRMED'", (self.oid,))
        proof = json.loads(proof_row['evidence'])
        proof['part_number'] = 2
        self.db.execute('UPDATE delivery_checks SET evidence=? WHERE id=?', (encode(proof), proof_row['id']))
        with self.assertRaisesRegex(ValueError, 'ORDER_OR_PLAN'):
            progress(self.db, self.row())
        self.assertEqual(self.flow.dispatch(self.oid), 'STALE')
        self.assertEqual(len(self.fx.desktop.receipts()), 2)

    def test_snapshot_reports_parts_without_claiming_answer_completed(self):
        self.fx.engine.tick()
        item = next(x for x in self.fx.engine.snapshot()['answer_tasks'] if x['task_id'] == self.oid)
        self.assertEqual(item['state'], 'PENDING')
        self.assertEqual(item['delivery_batch']['verified_parts'], 1)
        self.assertEqual(item['delivery_batch']['current_part'], 2)
        self.assertFalse(item['delivery_batch']['complete'])

    def test_receipt_for_another_part_is_unknown_even_with_same_body(self):
        send = self.fx.desktop.send
        def incorrect_receipt(message):
            result = send(message)
            result['outbox_id'] = 'different-answer-or-part'
            return result
        with patch.object(self.fx.desktop, 'send', side_effect=incorrect_receipt):
            self.assertEqual(self.fx.engine.tick()['state'], 'SEND_UNKNOWN')
        self.assertEqual(self.fx.engine.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(progress(self.db, self.row())['verified'], {})

    def test_frozen_plan_change_blocks_next_part_without_reformatting(self):
        self.fx.engine.tick()
        audit = self.db.one('SELECT id,details FROM audit WHERE event=? AND outbox_id=?', (PLAN_EVENT, self.oid))
        plan = json.loads(audit['details'])
        plan['parts'][1]['end'] -= 1
        self.db.execute('UPDATE audit SET details=? WHERE id=?', (encode(plan), audit['id']))
        self.assertEqual(self.flow.dispatch(self.oid), 'STALE')
        self.assertEqual(len(self.fx.desktop.receipts()), 2)

    def test_equal_receipt_times_keep_numeric_part_order_in_followup(self):
        with patch('helpdesk.delivery_batches.DEFAULT_LIMIT', 128), patch(
                'helpdesk.delivery.now', return_value='2026-10-03T09:00:00+00:00'):
            for _ in range(11):
                self.fx.engine.tick()
        follow = self.fx.app.ingest(Incoming(self.fx.binding, 'Synthetic followup', Intent.FOLLOWUP,
                                            quote_message_id=self.original.message_id))
        history = self.fx.app.context(follow.turn_id)['sent_history']
        self.assertEqual([item['part_number'] for item in history], list(range(1, 12)))

    def test_equal_times_merge_old_partial_and_new_complete_in_delivery_order(self):
        with patch('helpdesk.delivery.now', return_value='2026-10-03T09:00:00+00:00'):
            self.fx.engine.tick()
            old_part = self.fx.desktop.receipts()[-1]['body']
            follow = self.fx.app.ingest(Incoming(self.fx.binding, 'Synthetic next question', Intent.FOLLOWUP,
                                                quote_message_id=self.original.message_id))
            flow = Workflow(self.db, desktop=self.fx.desktop)
            oid = flow.generate(follow.turn_id)['outbox_id']
            flow.approve(oid)
            result = self.fx.engine.tick()
            if result['kind'] == 'ACK':
                result = self.fx.engine.tick()
            self.assertEqual((result['task_id'], result['state']), (oid, 'SENT_UI_CONFIRMED'))
        last = self.fx.app.ingest(Incoming(self.fx.binding, 'Synthetic last question', Intent.FOLLOWUP,
                                          quote_message_id=self.original.message_id))
        context = self.fx.app.context(last.turn_id)
        self.assertEqual(context['sent_history'][0]['body'], old_part)
        self.assertEqual(context['sent_history'][-1]['outbox_id'], oid)
        self.assertEqual(context['previous_sent_answer'], self.db.one('SELECT body FROM outbox WHERE id=?', (oid,))[0])


class NativeBatchIntegrationTests(unittest.TestCase):
    """Production adapter with synthetic UIA/clipboard; all accounts are fixtures."""
    def setUp(self):
        self.fx = native_fixtures.SourceDeliveryIntegrationTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.db, self.oid = self.fx.db, self.fx.row['id']
        # Smaller deterministic transport limit exposes multiple parts using
        # the existing synthetic answer, without modifying any recorded answer.
        self.limit = patch('helpdesk.delivery_batches.DEFAULT_LIMIT', 256)
        self.limit.start()
        self.addCleanup(self.limit.stop)

    def test_partial_not_counted_then_complete_counts_one_night_piece(self):
        self.assertEqual(self.fx.engine.tick()['state'], 'PART_DELIVERED')
        total = len(read_plan(self.db, self.fx.row)['parts'])
        self.assertGreater(total, 3)
        unit = lambda: self.db.one('SELECT * FROM performance_units WHERE id=?', (self.fx.fx.unit_id,))
        self.assertEqual(unit()['confirmed_quantity'], 0)
        self.assertFalse(PerformanceLedger(self.db).delivery_eligibility(unit(), outbox_id=self.oid)['eligible'])
        for number in range(2, total + 1):
            result = self.fx.engine.tick()
            self.assertEqual(result['part_number'], number)
            self.assertEqual(result['state'], 'SENT_UI_CONFIRMED' if number == total else 'PART_DELIVERED')
        self.assertEqual((unit()['completion_outbox_id'], unit()['category'], unit()['confirmed_quantity']), (self.oid, 'NIGHT', 1))
        self.assertEqual(unit()['question_time'], '2026-09-30T23:10:00+08:00')
        self.assertEqual(''.join(self.fx.native.messages[2:]), self.fx.row['body'])
        self.assertEqual(self.fx.native.submissions, total + 1)
        reports = PerformanceReports(self.db, PerformanceLedger(self.db))
        self.assertEqual(reports.build('2026-09-30')['summary']['night_articles'], 1)
        before = self.db.one('SELECT COUNT(*) FROM performance_events')[0]
        self.assertEqual(reports.build('2026-09-30'), reports.build('2026-09-30'))
        self.assertEqual(self.fx.engine.tick()['state'], 'IDLE')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_events')[0], before)
        self.db.execute("DELETE FROM delivery_checks WHERE outbox_id=? AND status='PART_UI_CONFIRMED' AND json_extract(evidence,'$.part_number')=2", (self.oid,))
        self.assertEqual(reports.build('2026-09-30')['summary']['night_articles'], 0)
        with self.assertRaisesRegex(ValueError, 'DELIVERY_PART'):
            Helpdesk(self.db).context(self.fx.row['turn_id'])

    def test_unknown_second_part_uses_original_journal_and_never_repeats(self):
        self.fx.engine.tick()
        self.fx.native.timeout_after_enter = True
        self.assertEqual(self.fx.engine.tick()['state'], 'SEND_UNKNOWN')
        self.assertEqual(self.fx.engine.tick()['state'], 'NEEDS_ATTENTION')
        before = self.fx.native.submissions
        self.fx.native.timeout_after_enter = False
        self.assertEqual(self.fx.engine.flow.inspect_unknown(self.oid), 'PART_DELIVERED')
        self.assertEqual(self.fx.native.submissions, before)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units WHERE id=?', (self.fx.fx.unit_id,))[0], 0)
        self.assertEqual(self.fx.engine.tick()['part_number'], 3)

    def test_changed_group_blocks_next_part_and_preserves_confirmed_part(self):
        self.fx.engine.tick()
        self.fx.native.header = 'Other English group'
        self.assertEqual(self.fx.engine.tick()['state'], 'FAILED')
        self.assertEqual(self.fx.native.submissions, 2)
        self.assertEqual(len(progress(self.db, self.fx.row)['verified']), 1)

    def test_last_part_recovery_updates_same_performance_unit_without_resend(self):
        self.fx.engine.tick()
        total = len(read_plan(self.db, self.fx.row)['parts'])
        for _ in range(total - 2):
            self.fx.engine.tick()
        send = self.fx.desktop.send
        def crash_after_verified_ui(message):
            send(message)
            raise SimulatedCrash('Synthetic loss before SQLite confirmation')
        with patch.object(self.fx.desktop, 'send', side_effect=crash_after_verified_ui):
            with self.assertRaises(SimulatedCrash):
                self.fx.engine.tick()
        before = self.fx.native.submissions
        recovered = self.fx.engine.flow.recover()
        self.assertEqual(recovered, [{'outbox_id': self.oid, 'state': 'SENT_UI_CONFIRMED'}])
        self.assertEqual(self.fx.native.submissions, before)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units WHERE id=?', (self.fx.fx.unit_id,))[0], 1)
        self.assertEqual(self.fx.engine.flow.recover(), [])


if __name__ == '__main__':
    unittest.main()
