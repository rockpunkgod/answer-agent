"""Human attestations and timestamps are synthetic; no platform delivery occurs."""
from hashlib import sha256
import json
import unittest

from helpdesk.domain import Intent
from helpdesk.manual_delivery import ManualDeliveries, validate_manual_package
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports
from helpdesk.service import Helpdesk, Incoming
from helpdesk.workflow import Workflow
from tests import test_shared_source_delivery_integration as delivery_fixture


class ManualDeliveryRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = delivery_fixture.SharedSourceDeliveryIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.generate()
        self.db, self.origin = self.fixture.db, self.fixture.row
        self.registry = ManualDeliveries(self.db)
        self.args = {'question_version': self.origin['question_version'],
            'context_revision': self.origin['context_revision'], 'reviewer': 'Synthetic human reviewer',
            'verification_evidence': 'Synthetic teacher-message observation; no real platform receipt',
            'delivered_at': '2026-10-01T10:00:00+08:00',
            'content': 'Actual human reply in an isolated fixture, edited from the generated draft.'}

    def register(self, **changes):
        return self.registry.register(self.origin['id'], **(self.args | changes))

    def unit(self):
        return self.db.one('SELECT * FROM performance_units WHERE id=?', (self.fixture.unit_id,))

    def test_edited_actual_reply_returns_to_original_turn_and_night_counting(self):
        generated = self.origin['body']
        result = self.register()
        self.assertEqual(result['counting_status'], 'CONFIRMED')
        actual = self.db.one('SELECT * FROM outbox WHERE id=?', (result['recorded_outbox_id'],))
        self.assertEqual(actual['turn_id'], self.origin['turn_id'])
        self.assertEqual(actual['question_version'], self.origin['question_version'])
        self.assertEqual(actual['body'], self.args['content'])
        self.assertIsNone(actual['answer_id'])
        self.assertIsNone(actual['run_id'])
        self.assertEqual(self.db.one('SELECT body,state FROM outbox WHERE id=?', (self.origin['id'],))['body'], generated)
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (self.origin['id'],))[0], 'CANCELLED')
        self.assertEqual((self.unit()['category'], self.unit()['measure_unit'], self.unit()['confirmed_quantity']), ('NIGHT','篇',1))
        self.assertEqual(self.unit()['question_time'], '2026-09-30T23:10:00+08:00')
        self.assertEqual(self.unit()['completed_at'], '2026-10-01T02:00:00+00:00')
        first = PerformanceReports(self.db, PerformanceLedger(self.db)).build('2026-09-30')
        again = PerformanceReports(self.db, PerformanceLedger(self.db)).build('2026-09-30')
        self.assertEqual(first['summary'], again['summary'])
        self.assertEqual(first['summary']['night_articles'], 1)

    def test_attestation_never_fabricates_platform_receipt_or_read_status(self):
        result = self.register()
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?',
            (result['recorded_outbox_id'],))[0])
        self.assertEqual(proof['verification_method'], 'MANUAL_ATTESTATION')
        self.assertEqual(proof['reviewer'], self.args['reviewer'])
        self.assertFalse(proof['automatic_receipt'])
        self.assertIsNone(proof['platform_message_id'])
        self.assertIsNone(proof['recipient_read'])

    def test_partial_delivery_is_visible_to_followup_but_does_not_complete_or_count(self):
        first = self.register(part_number=1,total_parts=2,content='Actual first part.')
        self.assertEqual(first['state'], 'PARTIAL_DELIVERY')
        self.assertEqual(self.unit()['confirmed_quantity'], 0)
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (self.origin['id'],))[0], 'SEND_UNKNOWN')
        q = self.db.one('SELECT * FROM questions WHERE id=?', (self.fixture.fx.outcome.question_id,))
        self.assertNotEqual(q['status'], 'WAITING_FOLLOWUP')
        delivered = self.db.one('SELECT * FROM outbox WHERE id=?', (first['recorded_outbox_id'],))
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?', (delivered['id'],))[0])
        with self.assertRaisesRegex(ValueError, 'some delivery parts'):
            validate_manual_package(self.db, delivered, proof)
        with self.assertRaisesRegex(ValueError, 'no longer valid'):
            PerformanceLedger(self.db).record_delivery(self.fixture.unit_id, first['recorded_outbox_id'])
        context = Helpdesk(self.db).context(self.origin['turn_id'])
        self.assertEqual(context['previous_sent_answer'], 'Actual first part.')
        self.assertEqual(context['sent_history'][0]['total_parts'], 2)
        self.assertEqual(context['sent_history'][0]['part_number'], 1)

    def test_all_parts_confirm_once_and_followup_uses_actual_content(self):
        self.register(part_number=1,total_parts=2,content='Actual first part.')
        final = self.register(part_number=2,total_parts=2,content='Actual second part.',
                              delivered_at='2026-10-01T10:01:00+08:00')
        self.assertEqual(final['counting_status'], 'CONFIRMED')
        outcome = Helpdesk(self.db).ingest(Incoming(self.origin['binding_id'], 'Why this option?', Intent.FOLLOWUP,
            source='synthetic-followup', platform_id='synthetic-followup',
            quote_message_id=self.origin['message_id']))
        context = Helpdesk(self.db).context(outcome.turn_id)
        self.assertEqual(context['question_version'], self.origin['question_version'])
        self.assertEqual([h['body'] for h in context['sent_history']], ['Actual first part.','Actual second part.'])
        self.assertEqual(context['previous_sent_answer'], 'Actual second part.')
        self.assertEqual(self.unit()['confirmed_quantity'], 1)
        self.assertTrue(PerformanceLedger(self.db).delivery_eligibility(self.unit())['eligible'])

    def test_duplicate_registration_preserves_original_actor_and_every_count(self):
        first = self.register()
        before = {t:self.db.one('SELECT COUNT(*) FROM '+t)[0] for t in ('outbox','audit','delivery_checks','performance_events')}
        again = self.register(reviewer='Another synthetic reviewer', verification_evidence='Repeated manual click')
        self.assertTrue(again['replayed'])
        self.assertEqual(first['completion_outbox_id'], again['completion_outbox_id'])
        self.assertEqual(before, {t:self.db.one('SELECT COUNT(*) FROM '+t)[0] for t in before})
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?', (first['recorded_outbox_id'],))[0])
        self.assertEqual(proof['reviewer'], self.args['reviewer'])

    def test_conflicting_repeat_and_package_size_do_not_overwrite(self):
        first = self.register(part_number=1,total_parts=2)
        for changes in ({'content':'Different actual content.'},{'total_parts':3}):
            with self.assertRaises(ValueError):
                self.register(part_number=1,total_parts=2,**changes) if 'total_parts' not in changes else self.register(**changes)
        self.assertEqual(self.db.one('SELECT body FROM outbox WHERE id=?', (first['recorded_outbox_id'],))[0], self.args['content'])
        self.assertEqual(self.unit()['confirmed_quantity'], 0)

    def test_registration_order_does_not_change_actual_completion_time(self):
        self.register(part_number=2,total_parts=2,content='Actual later delivery.',delivered_at='2026-10-01T10:05:00+08:00')
        final = self.register(part_number=1,total_parts=2,content='Actual earlier delivery.',delivered_at='2026-10-01T10:00:00+08:00')
        self.assertEqual(final['counting_status'], 'CONFIRMED')
        self.assertEqual(self.unit()['completed_at'], '2026-10-01T02:05:00+00:00')

    def test_unknown_original_is_resolved_by_human_evidence_without_resending(self):
        self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (self.origin['id'],))
        with unittest.mock.patch.object(self.fixture.flow.desktop, 'send', side_effect=AssertionError('No send')):
            result = self.register()
            self.fixture.flow.recover()
            self.assertEqual(self.fixture.flow.dispatch(self.origin['id']), 'CANCELLED')
            self.assertEqual(self.fixture.flow.dispatch(result['recorded_outbox_id']), 'SENT_UI_CONFIRMED')
        self.assertEqual(result['counting_status'], 'CONFIRMED')

    def test_missing_unzoned_future_and_pre_question_times_do_not_register(self):
        for value in ('','2026-10-01T10:00:00','9999-01-01T10:00:00+08:00','2026-09-30T22:59:00+08:00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.register(delivered_at=value)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)

    def test_simulation_operator_test_and_foreign_version_are_rejected(self):
        with self.assertRaises(ValueError):
            self.register(question_version='another-version')
        self.db.execute('UPDATE outbox SET simulated=1 WHERE id=?',(self.origin['id'],))
        with self.assertRaisesRegex(ValueError,'测试副本'):
            self.register()
        self.db.execute('UPDATE outbox SET simulated=0 WHERE id=?',(self.origin['id'],))
        for source in ('OPERATOR_TEST','MOCK'):
            self.db.execute('UPDATE messages SET source=? WHERE id=?',(source,self.origin['message_id']))
            with self.subTest(source=source), self.assertRaisesRegex(ValueError,'练习题'):
                self.register()
            self.assertFalse(any(task['outbox_id']==self.origin['id'] or task.get('turn_id')==self.origin['turn_id']
                                 for task in self.registry.list()))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)

    def test_stale_delivery_keeps_evidence_but_does_not_restore_completion_or_count(self):
        q = self.db.one('SELECT * FROM questions WHERE id=?', (self.fixture.fx.outcome.question_id,))
        Helpdesk(self.db).correct_material(q['material_id'],self.origin['message_id'],'Changed fixture material','Changed fixture material')
        result = self.register()
        self.assertEqual(result['state'],'SENT_UI_CONFIRMED')
        self.assertEqual(result['counting_status'],'COUNTING_RECHECK_REQUIRED')
        self.assertEqual(self.unit()['confirmed_quantity'], 0)
        self.assertEqual(self.db.one('SELECT status FROM questions WHERE id=?',(q['id'],))[0], 'REVIEW')

    def test_attachment_root_and_actual_hash_are_checked_and_history_keeps_attachment(self):
        root = self.fixture.fx.base / 'manual-attachments'
        root.mkdir()
        asset = root / 'anonymous-audio.bin'
        asset.write_bytes(b'anonymous attachment fixture, not an actual recording')
        self.registry = ManualDeliveries(self.db, attachment_root=root)
        with self.assertRaises((ValueError,OSError)):
            self.register(attachments=['../business.db'])
        result = self.register(content='',attachments=[asset.name])
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?',(result['recorded_outbox_id'],))[0])
        self.assertEqual(proof['attachments'][0]['sha256'],sha256(asset.read_bytes()).hexdigest())
        self.assertEqual(proof['actual_content'],'')
        history = Helpdesk(self.db).context(self.origin['turn_id'])['sent_history']
        self.assertEqual(history[0]['attachments'][0]['name'],asset.name)

    def test_revoking_one_earlier_part_invalidates_the_whole_package(self):
        first=self.register(part_number=1,total_parts=2)
        self.register(part_number=2,total_parts=2,content='Second actual part.',delivered_at='2026-10-01T10:01:00+08:00')
        self.assertEqual(self.unit()['confirmed_quantity'],1)
        self.db.execute("UPDATE delivery_checks SET status='SEND_UNKNOWN' WHERE outbox_id=?",(first['recorded_outbox_id'],))
        self.assertFalse(PerformanceLedger(self.db).delivery_eligibility(self.unit())['eligible'])
        report=PerformanceReports(self.db,PerformanceLedger(self.db)).build('2026-09-30')
        self.assertEqual(report['summary']['night_articles'],0)

    def test_stop_blocks_operations_but_not_recording_an_actual_human_delivery(self):
        self.fixture.flow.set_stop(True)
        self.fixture.flow.set_delivery_policy({'ANSWER':'DISABLED','ACK':'DISABLED'}, 'Synthetic disabled stage')
        result=self.register()
        self.assertEqual(result['verification_method'],'MANUAL_ATTESTATION')
        self.assertEqual(self.fixture.flow._delivery_policy_config()[0]['ANSWER'],'DISABLED')

    def test_registration_never_initializes_a_desktop_or_transport_database(self):
        with unittest.mock.patch('helpdesk.workflow.MockDesktop', side_effect=AssertionError('No transport')):
            self.assertEqual(self.register()['counting_status'], 'CONFIRMED')

    def test_receipt_acknowledgement_cannot_become_completed_performance(self):
        for text in ('收到','收到。','已收到！','好的','谢谢'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError,'不算解答'):
                self.register(content=text)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)
        self.assertEqual(self.unit()['confirmed_quantity'],0)

    def test_english_answer_word_is_not_guessed_to_be_a_receipt(self):
        result=self.register(content='received',verification_evidence='Synthetic reviewer verified this is an English answer word, not an acknowledgement')
        self.assertEqual(result['verification_method'],'MANUAL_ATTESTATION')

    def test_existing_question_card_uses_actual_delivery_instead_of_waiting_for_send(self):
        result=self.register()
        task=self.fixture.fx.tasks.get_task(self.fixture.fx.task['id'])
        self.assertEqual(task['actual_delivery']['outbox_id'],result['completion_outbox_id'])
        self.assertFalse(task['actual_delivery']['stale'])


class DirectManualDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=delivery_fixture.SharedSourceDeliveryIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db=self.fixture.db
        self.origin=self.fixture.shared.answer_input(self.fixture.decision)
        self.registry=ManualDeliveries(self.db)
        self.args={'question_version':self.origin['question_version'],'context_revision':self.origin['context_revision'],
                   'reviewer':'Synthetic teacher','verification_evidence':'Synthetic direct reply observation',
                   'delivered_at':'2026-10-01T10:00:00+08:00','content':'Direct human reply, with no generated draft.'}

    def test_direct_reply_needs_no_generated_draft_and_reuses_confirmed_semantic_relation(self):
        self.assertTrue(any(t.get('turn_id')==self.origin['turn_id'] for t in self.registry.list()))
        result=self.registry.register_for_turn(self.origin['turn_id'],**self.args)
        self.assertEqual(result['counting_status'],'CONFIRMED')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0],0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],0)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0],1)
        self.assertEqual(Helpdesk(self.db).context(self.origin['turn_id'])['previous_sent_answer'],self.args['content'])
        card=self.fixture.fx.tasks.get_draft(self.fixture.fx.draft['id'])
        self.assertEqual(card['actual_delivery']['outbox_id'],result['completion_outbox_id'])
        again=self.registry.register_for_turn(self.origin['turn_id'],**self.args)
        self.assertTrue(again['replayed'])
        self.assertEqual(again['completion_outbox_id'],result['completion_outbox_id'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],1)

    def test_bad_direct_registration_rolls_back_the_placeholder_and_every_part(self):
        before=self.db.one('SELECT COUNT(*) FROM outbox')[0]
        with self.assertRaises(ValueError):
            self.registry.register_for_turn(self.origin['turn_id'],**(self.args|{'delivered_at':''}))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM outbox')[0],before)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)

    def test_human_completion_stops_pending_generation_and_late_callback(self):
        self.fixture.fx.enqueue()
        run_id=self.fixture.fx.snapshot['run_id']
        result=self.registry.register_for_turn(self.origin['turn_id'],**self.args)
        self.assertEqual(result['counting_status'],'CONFIRMED')
        self.assertEqual(self.db.one('SELECT state FROM runs WHERE id=?',(run_id,))[0],'STALE')
        late=self.fixture.fx.generate(snapshot=self.fixture.fx.snapshot)
        self.assertEqual(late['state'],'STALE')
        self.assertIsNone(late['outbox_id'])
        with self.assertRaisesRegex(ValueError,'MANUALLY_DELIVERED'):
            Workflow(self.db).start(self.origin['turn_id'])


if __name__ == '__main__':
    unittest.main()
