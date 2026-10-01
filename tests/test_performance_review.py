import tempfile
from pathlib import Path
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_review import PerformanceReview


class PerformanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'review.db')
        self.app = Helpdesk(self.db)
        self.bid = self.app.bind('demo-group', 'student', 'demo', verified=True)
        self.proof = {'source': 'wecom_original', 'message_locator': 'fixture:m1', 'evidence': 'test-only'}
        self.first = self.app.ingest(Incoming(self.bid, 'question', Intent.NEW,
                                verified_question=demo_question(), verified_material='Passage',
                                source_sent_at='2026-09-30T23:10:00+08:00', source_time_evidence=self.proof))
        self.ledger = PerformanceLedger(self.db)
        self.unit = self.ledger.create_unit(self.first.message_id, '阅读理解', scope_key='scope1',
                                           grouping_reason='same material', question_id=self.first.question_id)
        self.review = PerformanceReview(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_ingest_preserves_original_time_without_observation_fallback(self):
        unit = self.ledger.list_units()[0]
        self.assertEqual(unit['category'], 'NIGHT')
        self.assertEqual(unit['question_time'], '2026-09-30T23:10:00+08:00')
        other = self.app.ingest(Incoming(self.bid, 'other', Intent.NEW))
        self.assertIsNone(self.db.one('SELECT source_sent_at FROM messages WHERE id=?', (other.message_id,))[0])
        with self.assertRaises(ValueError):
            self.app.ingest(Incoming(self.bid, 'bad time', source_sent_at='2026-09-30T23:10:00+08:00'))

    def test_anomaly_counts_events_and_units_separately_without_auto_penalty(self):
        a = self.review.flag(self.unit, '投诉', actor='detector', reason='待核验', evidence='event-a')
        self.review.flag(self.unit, '疑似敷衍', actor='detector', reason='待核验', evidence='event-b')
        initial = self.review.anomaly_summary()
        self.assertEqual(initial['counts']['SUSPECTED'], {'events': 2, 'units': 1})
        self.assertEqual(initial['counts']['CONFIRMED']['events'], 0)
        self.assertIsNone(initial['rate'])
        self.review.resolve(a, 'CONFIRMED', reviewer='主管', reason='核对原始内容', evidence='human-review')
        self.review.resolve(a, 'WITHDRAWN', reviewer='主管', reason='补充证据不成立', evidence='appeal-evidence')
        final = self.review.anomaly_summary()
        self.assertEqual(final['counts']['WITHDRAWN']['events'], 1)
        self.assertFalse(final['automatic_penalty'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM performance_events WHERE event='ANOMALY_REVIEWED'")[0], 2)

    def test_duplicate_observation_can_backfill_original_time_without_another_ack(self):
        old = self.app.ingest(Incoming(self.bid, 'later timestamp', Intent.NEW, platform_id='late-proof'))
        duplicate = self.app.ingest(Incoming(self.bid, 'later timestamp', Intent.NEW, platform_id='late-proof',
                                    source_sent_at='2026-09-30T22:58:00+08:00', source_time_evidence=self.proof))
        self.assertEqual(duplicate.status, 'DUPLICATE')
        self.assertEqual(self.db.one('SELECT source_sent_at FROM messages WHERE id=?', (old.message_id,))[0], '2026-09-30T22:58:00+08:00')
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE message_id=? AND purpose='ACK'", (old.message_id,))[0], 1)
        conflict = self.app.ingest(Incoming(self.bid, 'later timestamp', Intent.NEW, platform_id='late-proof',
                                    source_sent_at='2026-09-30T23:05:00+08:00', source_time_evidence=self.proof))
        self.assertEqual(conflict.status, 'NEEDS_REVIEW')
        self.assertEqual(self.db.one('SELECT source_sent_at FROM messages WHERE id=?', (old.message_id,))[0], '2026-09-30T22:58:00+08:00')

    def test_timing_persists_raw_wait_separately_and_does_not_reclassify_night(self):
        result = self.review.timing(self.unit, actor='operator', as_of='2026-10-01T10:00:00+08:00')
        self.assertEqual(result['raw_answer_minutes'], 650)
        self.assertIsNone(result['adjusted_answer_minutes'])
        self.assertEqual(self.ledger.list_units()[0]['category'], 'NIGHT')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_sla_reviews')[0], 1)


if __name__ == '__main__':
    unittest.main()
