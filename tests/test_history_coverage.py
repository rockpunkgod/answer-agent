import tempfile
from pathlib import Path
import unittest
from helpdesk.storage import Store
from helpdesk.history_coverage import record_observed_group, record_coverage, coverage_summary


class HistoryCoverageTests(unittest.TestCase):
    def test_exited_groups_remain_in_scope_and_partial_is_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Store(Path(temp) / 'coverage.db')
            try:
                active = record_observed_group(db, 'active', membership='ACTIVE', evidence='snapshot-a')
                exited = record_observed_group(db, 'exited', membership='EXITED', evidence='snapshot-b')
                record_coverage(db, exited, '2026-09-17', '2026-09-30', status='PARTIAL', actor='observer', evidence='one page only')
                summary = coverage_summary(db, '2026-09-17', '2026-09-30')
                self.assertEqual({g['id'] for g in summary['groups']}, {active, exited})
                self.assertEqual(summary['scope'], 'ALL_HISTORICAL_GROUPS')
                self.assertFalse(summary['complete'])
                self.assertFalse(summary['inventory_complete'])
                self.assertEqual(db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
                self.assertTrue(any(g['membership'] == 'EXITED' for g in summary['groups']))
            finally:
                db.close()

    def test_complete_one_group_does_not_claim_complete_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Store(Path(temp) / 'coverage.db')
            try:
                gid = record_observed_group(db, 'group', membership='EXITED', evidence='verified identity', verified=True)
                record_coverage(db, gid, '2026-09-17', '2026-09-30', status='COMPLETE', actor='reviewer', evidence='full exported history')
                summary = coverage_summary(db, '2026-09-17', '2026-09-30')
                self.assertTrue(summary['groups'][0]['coverage_complete'])
                self.assertFalse(summary['complete'])
            finally:
                db.close()
