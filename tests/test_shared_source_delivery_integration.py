"""Anonymous shared-source to manual-delivery integration, no external actors."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

from helpdesk.manual_group_draft import ManualGroupDraft
from helpdesk.reviewed_question_queue import advance
from helpdesk.semantic_decisions import SharedSemanticDecisions
from helpdesk.source_question_tasks import validate_reviewed_source_task
from helpdesk.storage import encode
from helpdesk.workflow import Workflow
from tests import test_source_question_tasks as source_fixture


class FixtureManualAdapter(ManualGroupDraft):
    """Business boundary fixture; real MCP authority is covered separately."""
    def __init__(self, root):
        self.root = root
        self.stages = 0
        self.reads = 0
        self.confirmed = True

    def stage_outbox(self, store, outbox_id, *, semantic_decision_id=None):
        self.stages += 1
        return {'status': 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND',
                'journal': str(self.root / 'anonymous-fixture-journal.json'), 'answer_sent': False}

    def readback_outbox(self, store, outbox_id):
        self.reads += 1
        row = store.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
        binding = store.one('SELECT * FROM bindings WHERE id=?', (row['binding_id'],))
        return {'confirmed': self.confirmed, 'simulated': False, 'outbox_id': outbox_id,
                'binding_id': binding['id'], 'group_key': binding['group_key'], 'student_key': binding['student_key'],
                'body_hash': sha256(row['body'].encode()).hexdigest(), 'sender_role': 'TEACHER',
                'confirmed_at': datetime.now(timezone.utc).isoformat(),
                'message_locator': 'isolated-fixture:actual-bubble',
                'evidence_paths': [str(self.root / 'anonymous-fixture-proof.json')]}


class SharedSourceDeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fx = source_fixture.SourceQuestionTasksTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.message, self.fx.receipt, self.fx.outcome = self.fx.receive(
            'shared-source', sent='2026-09-30T23:10:00+08:00')
        self.db = self.fx.db
        self.shared = SharedSemanticDecisions(self.db, self.fx.raw)
        self.decision = self.shared.confirm(self.fx.receipt['id'], question_type='阅读理解',
            actor='isolated resolver', evidence='isolated verified material and question')
        self.fx.draft = self.fx.tasks.create_from_semantic(self.fx.raw, self.decision)
        self.unit_id = self.shared.counting_unit(self.decision)

    def generate(self):
        self.fx.enqueue()
        for kwargs in ({'session_creator': lambda **_: source_fixture.URL},
                       {'preparer': self.fx.prepare}, {'generator': self.fx.generate}):
            advance(self.db, self.fx.task['id'], executor='LUNA', **kwargs)
        self.row = self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        self.flow = Workflow(self.db)
        self.adapter = FixtureManualAdapter(self.fx.base)

    def test_shared_type_and_scope_reach_one_clarity_gate_and_frozen_input(self):
        again = self.fx.tasks.create_from_semantic(self.fx.raw, self.decision)
        self.assertEqual(again['id'], self.fx.draft['id'])
        self.fx.enqueue()
        marker = self.fx.snapshot['source_clarity_review']
        self.assertEqual(marker['semantic_decision_id'], self.decision)
        self.assertEqual(marker['counting_unit_id'], self.unit_id)
        self.assertEqual(self.fx.source_review_count(), 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 1)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)

    def test_source_type_cannot_diverge_from_counting_decision(self):
        draft = self.fx.draft
        payload = dict(draft['payload'], question_type='语法填空')
        encoded = encode(payload)
        self.db.execute('UPDATE operator_draft_revisions SET payload=?,payload_sha256=? WHERE draft_id=?',
                        (encoded, sha256(encoded.encode()).hexdigest(), draft['id']))
        with self.assertRaisesRegex(ValueError, 'shared semantic decision'):
            self.fx.review()
        self.assertEqual(self.fx.source_review_count(), 0)

    def test_native_draft_is_not_delivery_then_verified_human_send_counts_once(self):
        self.generate()
        result = self.flow.stage_manual_answer(self.row['id'], adapter=self.adapter)
        self.assertFalse(result['answer_sent'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0], 0)
        result = self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter)
        self.assertEqual(result, {'state': 'SENT_UI_CONFIRMED', 'counting_status': 'CONFIRMED'})
        unit = self.db.one('SELECT * FROM performance_units WHERE id=?', (self.unit_id,))
        self.assertEqual((unit['category'], unit['measure_unit'], unit['confirmed_quantity']), ('NIGHT', '篇', 1))
        before = {table: self.db.one('SELECT COUNT(*) FROM '+table)[0]
                  for table in ('delivery_checks', 'performance_events', 'outbox')}
        self.assertEqual(self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter), result)
        self.assertEqual(self.adapter.reads, 1)
        self.assertEqual(before, {table: self.db.one('SELECT COUNT(*) FROM '+table)[0] for table in before})

    def test_unknown_human_send_does_not_count_or_replay_a_draft(self):
        self.generate()
        self.flow.stage_manual_answer(self.row['id'], adapter=self.adapter)
        self.adapter.confirmed = False
        result = self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter)
        self.assertEqual(result['state'], 'SEND_UNKNOWN')
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0], 0)
        result = self.flow.stage_manual_answer(self.row['id'], adapter=self.adapter)
        self.assertEqual(result['status'], 'SEND_UNKNOWN')
        self.assertEqual(self.adapter.stages, 1)

    def test_stop_blocks_new_draft_but_preserves_verified_actual_delivery(self):
        self.generate()
        self.flow.stage_manual_answer(self.row['id'], adapter=self.adapter)
        self.flow.set_stop(True)
        self.assertEqual(self.flow.stage_manual_answer(self.row['id'], adapter=self.adapter)['status'], 'STOPPED')
        self.assertEqual(self.adapter.stages, 1)
        self.assertEqual(self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter)['state'], 'SENT_UI_CONFIRMED')

    def test_unstarted_manual_task_cannot_accept_delivery_claim(self):
        self.generate()
        with self.assertRaisesRegex(ValueError, 'authorization checkpoint'):
            self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter)
        self.assertEqual(self.adapter.reads, 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)

    def test_counting_recovers_after_receipt_commit_and_source_changes_stop_old_gate(self):
        self.generate()
        with self.db.transaction():
            self.flow._record_check(self.row, self.adapter.readback_outbox(self.db, self.row['id']), simulated=False)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0], 0)
        self.assertEqual(self.flow.verify_manual_delivery(self.row['id'], adapter=self.adapter)['counting_status'], 'CONFIRMED')
        with self.fx.raw.connect() as raw:
            raw.execute('UPDATE events SET auto_reply_allowed=0 WHERE message_id=?', (self.fx.message.message_id,))
        with self.assertRaises(ValueError):
            validate_reviewed_source_task(self.db, self.fx.task['id'])


if __name__ == '__main__':
    unittest.main()
