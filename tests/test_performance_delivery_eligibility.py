"""Current-count eligibility with isolated receipts; no service or desktop calls."""
from hashlib import sha256
import json
from pathlib import Path
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent, new_id
from helpdesk.performance_reports import PerformanceReports
from helpdesk.service import Incoming
from tests import test_performance as fixtures
from tests import test_live_generation as generation


class DeliveryEligibilityTests(unittest.TestCase):
    setUp = fixtures.PerformanceTests.setUp
    tearDown = fixtures.PerformanceTests.tearDown
    question = fixtures.PerformanceTests.question
    unit = fixtures.PerformanceTests.unit
    real_delivery = fixtures.PerformanceTests.real_delivery

    def completed(self):
        outcome, uid = self.question('2026-09-30T23:10:00+08:00')
        oid = self.real_delivery(outcome, uid, '2026-10-01T00:20:00+08:00')
        self.ledger.confirm(uid, reviewer='fixture reviewer', evidence='isolated manual reply evidence')
        return outcome, uid, oid

    def report(self):
        return PerformanceReports(self.db, self.ledger)

    def test_human_answer_and_ordinary_followup_remain_eligible(self):
        outcome, uid, _ = self.completed()
        self.assertTrue(self.ledger.delivery_eligibility(self.unit(uid))['eligible'])
        follow = self.app.ingest(Incoming(self.person, 'fixture followup', Intent.FOLLOWUP,
                                         quote_message_id=outcome.message_id))
        self.ledger.link_activity(uid, follow.message_id, question_id=outcome.question_id,
                                  kind='FOLLOWUP', reason='same fixture question')
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 1)
        self.assertEqual(self.ledger.reconcile_stale_deliveries(), [])
        self.app.correct_material(self.unit(uid)['material_id'], follow.message_id,
                                  'fixture material correction', 'fixture material correction')
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 0)

    def test_followup_without_matching_audit_cannot_release_old_context(self):
        outcome, uid, _ = self.completed()
        follow = self.app.ingest(Incoming(self.person, 'fixture followup', Intent.FOLLOWUP,
                                         quote_message_id=outcome.message_id))
        self.db.execute("DELETE FROM audit WHERE event='MESSAGE_LINKED' AND turn_id=?", (follow.turn_id,))
        self.assertFalse(self.ledger.delivery_eligibility(self.unit(uid))['eligible'])

    def test_question_change_excludes_current_count_without_writes_or_history_overwrite(self):
        outcome, uid, _ = self.completed()
        reports = self.report()
        version = reports.generate('2026-09-30')['version']
        # Submission is a local fixture flag, never an external report submission.
        reports.mark_submitted('2026-09-30', version, actor='fixture', evidence='fixture only')
        history = reports.list_versions('2026-09-30')
        self.app.ingest(Incoming(self.person, 'fixture correction', Intent.CORRECTION,
                                 quote_message_id=outcome.message_id,
                                 verified_question=demo_question(stem='Changed fixture question')))
        before = list(self.db.connection.iterdump())
        self.db.execute('PRAGMA query_only=ON')
        try:
            result = reports.build('2026-09-30')
            self.assertEqual(reports.build('2026-09-30'), result)
            self.assertFalse(self.ledger.delivery_eligibility(self.unit(uid))['eligible'])
        finally:
            self.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(result['summary']['night_articles'], 0)
        self.assertFalse(result['details'][0]['included'])
        self.assertIn('实际交付需重新核验', result['details'][0]['exclusion_reason'])
        self.assertEqual(reports.build('2026-09-30'), result)
        self.assertEqual(list(self.db.connection.iterdump()), before)
        self.assertEqual(reports.list_versions('2026-09-30'), history)
        self.assertEqual(self.unit(uid)['status'], 'CONFIRMED')
        self.assertEqual(self.ledger.reconcile_stale_deliveries(), [uid])
        events = self.db.one('SELECT COUNT(*) FROM performance_events')[0]
        self.assertEqual(self.ledger.reconcile_stale_deliveries(), [])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_events')[0], events)

    def test_context_only_change_demotes_using_same_predicate(self):
        outcome, uid, _ = self.completed()
        self.db.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?', (outcome.question_id,))
        reports = self.report()
        self.assertEqual(reports.build('2026-09-30')['summary']['night_articles'], 0)
        self.assertEqual(self.ledger.reconcile_stale_deliveries(), [uid])
        self.assertEqual(self.unit(uid)['confirmed_quantity'], 0)

    def test_shared_material_correction_excludes_old_delivery(self):
        outcome, uid, _ = self.completed()
        self.app.correct_material(self.unit(uid)['material_id'], outcome.message_id,
                                  'fixture changed material', 'fixture changed material')
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 0)
        self.assertFalse(self.ledger.delivery_eligibility(self.unit(uid))['eligible'])

    def test_unknown_simulated_ack_invalid_binding_and_readback_fail_closed(self):
        _, uid, oid = self.completed()
        reports = self.report()
        mutations = [
            ("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (oid,)),
            ("UPDATE outbox SET simulated=1 WHERE id=?", (oid,)),
            ("UPDATE outbox SET purpose='ACK' WHERE id=?", (oid,)),
            ("UPDATE bindings SET verified=0 WHERE id=?", (self.person,)),
            ("UPDATE delivery_checks SET evidence=? WHERE outbox_id=?", ('{"simulated":true}', oid)),
            ("UPDATE delivery_checks SET evidence=? WHERE outbox_id=?", ('{"body_hash":"wrong"}', oid)),
            ("UPDATE outbox SET sent_at=? WHERE id=?", ('2026-10-01T00:30:00+08:00', oid)),
            ("DELETE FROM delivery_checks WHERE outbox_id=?", (oid,)),
            ("UPDATE delivery_checks SET status='SEND_UNKNOWN' WHERE outbox_id=?", (oid,)),
        ]
        for sql, params in mutations:
            with self.subTest(sql=sql, params=params):
                self.db.execute('SAVEPOINT invalid_fixture')
                try:
                    self.db.execute(sql, params)
                    self.assertFalse(self.ledger.delivery_eligibility(self.unit(uid))['eligible'])
                    self.assertEqual(reports.build('2026-09-30')['summary']['night_articles'], 0)
                finally:
                    self.db.execute('ROLLBACK TO invalid_fixture')
                    self.db.execute('RELEASE invalid_fixture')
        self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (oid,))
        with self.assertRaises(ValueError):
            self.ledger.confirm(uid, reviewer='fixture', evidence='fixture')

    def test_generated_answer_no_second_review_and_body_tamper_rejected(self):
        outcome, uid = self.question('2026-09-30T23:10:00+08:00')
        # Existing real-generation fixture uses a fake model and verified-course stub.
        helper = generation.LiveGenerationBoundaryTests()
        helper.db, helper.app, helper.turn = self.db, self.app, outcome.turn_id
        helper.base = Path(self.tmp.name)
        helper.skill = helper.base / 'fixture-course.md'
        helper.skill.write_text('# approved course fixture\n', encoding='utf-8')
        helper.manifest = helper.base / 'fixture-manifest.json'
        helper.manifest.write_text('{}', encoding='utf-8')
        adapter = generation.StubAdapter()
        flow, rid = helper.start(adapter)
        flow.set_answer_review_required(False)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (rid,))[0])
        oid = flow.finish(rid, adapter.generate(snapshot))['outbox_id']
        body = self.db.one('SELECT body FROM outbox WHERE id=?', (oid,))[0]
        sent = '2026-10-01T00:20:00+08:00'
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',sent_at=? WHERE id=?", (sent, oid))
        evidence = {'confirmed': True, 'simulated': False, 'body_hash': sha256(body.encode()).hexdigest()}
        self.db.execute('INSERT INTO delivery_checks VALUES(?,?,?,?,?)',
                        (new_id(), oid, 'SENT_UI_CONFIRMED', json.dumps(evidence), sent))
        self.ledger.record_delivery(uid, oid)
        self.ledger.confirm(uid, reviewer='fixture performance reviewer', evidence='fixture counting evidence')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 1)
        self.app.ingest(Incoming(self.person, 'fixture machine followup', Intent.FOLLOWUP,
                                 quote_message_id=outcome.message_id))
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 1)
        self.db.execute('UPDATE outbox SET body=? WHERE id=?', ('tampered fixture body', oid))
        self.assertEqual(self.report().build('2026-09-30')['summary']['night_articles'], 0)


if __name__ == '__main__':
    unittest.main()
