import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from helpdesk.diagnostics import doctor


class DiagnosticsTests(unittest.TestCase):
    def test_default_does_not_claim_live_connections(self):
        status = doctor()
        self.assertEqual(status['demo'], 'SIMULATION_ONLY')
        self.assertFalse(status['scheduler']['connection_verified'])
        self.assertFalse(status['deepseek']['live_page_verified'])

    def test_prepared_status_is_read_only_and_expired_pin_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            database = base / 'test.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE runs(id,state,input_json)')
                conn.execute('CREATE TABLE outbox(id,purpose,state,simulated)')
                conn.execute('INSERT INTO runs VALUES(?,?,?)', ('run', 'GENERATED', json.dumps({
                    'simulated': False, 'generation_adapter': 'WINDOWS_MCP_PREPARED_DEEPSEEK'})))
                conn.execute('INSERT INTO outbox VALUES(?,?,?,?)', ('test', 'TEST_ANSWER', 'SENT_UI_CONFIRMED', 0))
                conn.commit()
            (base / 'preparation.json').write_text(json.dumps({'run_id': 'run', 'operator_verified': True}))
            (base / 'manifest.json').write_text('{}')
            (base / 'pin.json').write_text(json.dumps({'outbox_id': 'test', 'expires_at': 1}))
            config = base / 'real.json'
            config.write_text(json.dumps({'database': 'test.db', 'run_id': 'run',
                'preparation': 'preparation.json', 'manifest': 'manifest.json',
                'test_outbox_id': 'test', 'pin': 'pin.json'}))
            before = database.read_bytes()
            status = doctor(real_config_path=config)
            self.assertEqual(before, database.read_bytes())
            prepared = status['prepared_workbench']
            self.assertTrue(prepared['frozen_prepared_adapter'])
            self.assertTrue(prepared['operator_preparation_reviewed'])
            self.assertEqual(prepared['test_delivery']['state'], 'SENT_UI_CONFIRMED')
            self.assertFalse(prepared['test_pin_time_valid'])
            self.assertFalse(prepared['desktop_checked_now'])
            self.assertFalse(status['scheduler']['connection_verified'])

    def test_missing_database_is_not_created_by_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / 'real.json'
            config.write_text(json.dumps({'database': 'missing.db', 'run_id': 'run',
                'preparation': 'prep.json', 'manifest': 'manifest.json'}))
            with self.assertRaises(FileNotFoundError):
                doctor(real_config_path=config)
            self.assertFalse((base / 'missing.db').exists())
