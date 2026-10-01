from pathlib import Path
import tempfile
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.performance import PerformanceLedger
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store


class OperatorPerformanceExclusionTests(unittest.TestCase):
    def test_reviewed_local_test_cannot_be_enrolled_or_given_formal_source_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.db')
            try:
                app = Helpdesk(store)
                binding = app.bind('local:test', 'local:fixture', '本机测试', verified=True)
                task = app.ingest(Incoming(binding, '本机录入测试题', Intent.NEW, source='OPERATOR_TEST',
                    verified_question=demo_question(), raw_material='Passage', verified_material='Passage'))
                ledger = PerformanceLedger(store)
                self.assertEqual(ledger.list_unlinked_messages(), [])
                with self.assertRaisesRegex(ValueError, 'excluded'):
                    ledger.create_unit(task.message_id, '阅读理解', scope_key='test',
                        grouping_reason='test', question_id=task.question_id)
                with self.assertRaisesRegex(ValueError, 'excluded'):
                    ledger.record_source_time(task.message_id, '2026-09-30T23:10:00+08:00',
                        source='operator_verified_original', message_locator='test', evidence={'test': True})
                self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
                self.assertIsNone(store.one('SELECT source_sent_at FROM messages WHERE id=?', (task.message_id,))[0])
            finally:
                store.close()
