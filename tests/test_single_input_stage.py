"""Trusted current-stage configuration requires one input review, then manual send."""
from pathlib import Path
import json
import tempfile
import unittest

from helpdesk.demo_server import DemoHTTPServer
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


class SingleInputStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'business.db'
        self.config = self.root / 'collector.toml'

    def config_text(self, value):
        return ('[collector]\nbusiness_database=' + json.dumps(self.db.as_posix())
                + '\n[stage]\nname="One input review"\nrequire_ack_before_generation=true'
                + '\nanswer_review_required=' + value
                + '\n[stage.delivery]\nACK="AUTO"\nANSWER="MANUAL"\nCORRECTION="MANUAL"\n')

    def test_current_stage_persists_no_second_review_but_manual_send(self):
        self.config.write_text(self.config_text('false'), encoding='utf-8')
        server = DemoHTTPServer(('127.0.0.1', 0), self.db,
                                processing_mode='ACK_ONLY', collector_config=self.config)
        try:
            store = Store(self.db)
            try:
                health = Workflow(store).dashboard()['health']
                self.assertIs(health['answer_review_required'], False)
                with self.assertRaisesRegex(ValueError, 'ACK_REQUIRED'):
                    Workflow(store)._require_confirmed_ack('missing-ack-turn')
                self.assertEqual(health['delivery_policy']['ANSWER'], 'MANUAL')
                self.assertEqual(store.one('SELECT COUNT(*) FROM reviews')[0], 0)
                self.assertEqual(store.one('SELECT COUNT(*) FROM outbox')[0], 0)
            finally:
                store.close()
        finally:
            server.server_close()

    def test_string_flag_rejected_before_audit_or_policy_writes(self):
        self.config.write_text(self.config_text('"false"'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'answer_review_required must be a boolean'):
            DemoHTTPServer(('127.0.0.1', 0), self.db,
                           processing_mode='ACK_ONLY', collector_config=self.config)
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
