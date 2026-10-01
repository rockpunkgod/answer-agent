"""Failure ordering and migration checks beyond happy-path acceptance."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

from helpdesk.__main__ import demo_question
from helpdesk.delivery import MockDesktop, SimulatedCrash
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


def dispatch_in_process(db_path, desktop_path, outbox_id):
    db = Store(db_path)
    try:
        return Workflow(db, desktop=MockDesktop(desktop_path)).dispatch(outbox_id)
    finally:
        db.close()


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "db.sqlite"
        self.db = Store(self.path)
        self.app = Helpdesk(self.db)
        self.desktop = MockDesktop(str(self.path) + ".transport")
        self.flow = Workflow(self.db, desktop=self.desktop)
        self.student = self.app.bind("DEMO", "student", "Demo", verified=True)
        self.first = self.app.ingest(Incoming(self.student, "第12题", Intent.NEW, verified_question=demo_question(), verified_material="Passage"))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_duplicate_generation_request_does_not_queue_two_answers(self):
        a = self.flow.generate(self.first.turn_id)
        b = self.flow.generate(self.first.turn_id)
        self.assertEqual(a["outbox_id"], b["outbox_id"])
        self.flow.approve(a["outbox_id"])
        self.flow.dispatch(a["outbox_id"])
        c = self.flow.generate(self.first.turn_id)
        self.assertEqual(c["state"], "SENT_UI_CONFIRMED")
        self.assertEqual(len(self.desktop.receipts()), 1)

    def test_crash_then_correction_preserves_delivery_and_flags_old_version(self):
        generated = self.flow.generate(self.first.turn_id)
        self.flow.approve(generated["outbox_id"])
        self.desktop.fault = "after_send"
        with self.assertRaises(SimulatedCrash):
            self.flow.dispatch(generated["outbox_id"])
        self.app.ingest(Incoming(self.student, "发错了", Intent.CORRECTION, quote_message_id=self.first.message_id,
                                 verified_question=demo_question(stem="Why did he NOT return home?")))
        self.desktop.fault = None
        self.flow.recover()
        row = self.db.one("SELECT state FROM outbox WHERE id=?", (generated["outbox_id"],))
        self.assertEqual(row[0], "SENT_UI_CONFIRMED")
        self.assertTrue(self.db.one("SELECT id FROM human_tasks WHERE reason LIKE 'DELIVERED_OLD_VERSION_RECHECK:%'"))
        self.assertEqual(len(self.desktop.receipts()), 1)

    def test_negative_correction_does_not_blindly_reuse_fixture_answer(self):
        changed = self.app.ingest(Incoming(self.student, "更正NOT", Intent.CORRECTION, quote_message_id=self.first.message_id,
                                 verified_question=demo_question(stem="Why did he NOT return home?")))
        result = self.flow.generate(changed.turn_id)
        self.assertEqual(result["state"], "REJECTED")
        self.assertIsNone(result["outbox_id"])

    def test_v1_migration_preserves_original_data(self):
        path = Path(self.temp.name) / "old.db"
        migration = Path(__file__).resolve().parents[1] / "helpdesk/migrations/001_initial.sql"
        connection = sqlite3.connect(path)
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO bindings VALUES('old','group','student','Name',1)")
        connection.commit()
        connection.close()
        migrated = Store(path)
        try:
            self.assertEqual(migrated.one("SELECT MAX(version) FROM schema_migrations")[0], 6)
            self.assertEqual(migrated.one("SELECT display_name FROM bindings WHERE id='old'")[0], "Name")
            self.assertEqual(migrated.one("PRAGMA integrity_check")[0], "ok")
            self.assertEqual(migrated.all("PRAGMA foreign_key_check"), [])
        finally:
            migrated.close()

    def test_two_processes_share_one_desktop_lock(self):
        a = self.flow.generate(self.first.turn_id)["outbox_id"]
        self.flow.approve(a)
        other = self.app.bind("DEMO", "other", "Other", verified=True)
        second = self.app.ingest(Incoming(other, "第12题", Intent.NEW, verified_question=demo_question(), verified_material="Passage"))
        b = self.flow.generate(second.turn_id)["outbox_id"]
        self.flow.approve(b)
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            calls = [pool.submit(dispatch_in_process, str(self.path), self.desktop.path, oid) for oid in (a, b)]
            self.assertEqual([call.result(timeout=20) for call in calls], ["SENT_UI_CONFIRMED", "SENT_UI_CONFIRMED"])
        self.assertEqual({r["outbox_id"] for r in self.desktop.receipts()}, {a, b})
