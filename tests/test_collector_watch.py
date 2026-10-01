"""Durable LIVE collector -> ACK_ONLY queue; no desktop or real account calls."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import json
from datetime import datetime, timezone, timedelta
from dataclasses import replace
import unittest
from unittest.mock import patch, Mock
from helpdesk.collector_cli import run_live_iteration, main
from helpdesk.collector_storage import CollectorStore
from helpdesk.message_sources import NormalizedMessage, MessageBatch, SyncMode, normalize_sent_time
from helpdesk.service import Helpdesk
from helpdesk.storage import Store


class Source:
    source_name = "fixture"
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []
    def fetch_page(self, cursor, mode, page_size):
        self.calls.append((cursor, mode))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class CollectorWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = CollectorStore(self.root / "messages.db")
        self.business_path = self.root / "business.db"
        business = Store(self.business_path)
        try:
            Helpdesk(business).bind("room", "student", "学生", verified=True)
        finally:
            business.close()
        self.settings = {"business_database": str(self.business_path), "processing_mode": "ACK_ONLY", "max_pages": 2}
        utc, local = normalize_sent_time((datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        self.message = NormalizedMessage(source_type="wecom_archive", source_message_id="question-one", room_id="room", sender_id="student",
            normalized_text="请问这道题怎么做？", sent_at_utc=utc, sent_at_local=local)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_one_ack_no_teaching(self):
        business = Store(self.business_path)
        try:
            rows = business.all("SELECT purpose,body,state FROM outbox")
            self.assertEqual([tuple(row) for row in rows], [("ACK", "收到", "PENDING")])
            for table in ("cases", "questions", "runs", "answers", "performance_units"):
                self.assertEqual(business.one("SELECT COUNT(*) FROM " + table)[0], 0)
        finally:
            business.close()

    def test_single_iteration_syncs_and_queues_one_received_only(self):
        result = run_live_iteration(self.store, Source([MessageBatch((self.message,), "1")]), self.settings)
        self.assertEqual((result["sync"]["inserted_count"], result["events_processed"], result["pending_ack_count"]), (1, 1, 1))
        self.assertFalse(result["student_send_enabled"])
        self.assert_one_ack_no_teaching()

    def test_restart_empty_sync_resumes_cursor_without_duplicate_ack(self):
        run_live_iteration(self.store, Source([MessageBatch((self.message,), "1")]), self.settings)
        restarted = CollectorStore(self.store.path)
        source = Source([MessageBatch((), "1")])
        result = run_live_iteration(restarted, source, self.settings)
        self.assertEqual(source.calls, [("1", SyncMode.LIVE)])
        self.assertEqual((result["sync"]["inserted_count"], result["events_processed"], result["pending_ack_count"]), (0, 0, 1))
        self.assert_one_ack_no_teaching()

    def test_drain_failure_keeps_committed_cursor_and_pending_events_for_restart(self):
        with patch("helpdesk.collector_dispatch.CollectorDispatcher.drain", side_effect=RuntimeError("business unavailable")):
            with self.assertRaises(RuntimeError):
                run_live_iteration(self.store, Source([MessageBatch((self.message,), "1")]), self.settings)
        self.assertEqual(self.store.get_sync_state("fixture")["cursor"], "1")
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT processed_at FROM events").fetchone()[0])
        result = run_live_iteration(CollectorStore(self.store.path), Source([MessageBatch((), "1")]), self.settings)
        self.assertEqual(result["events_processed"], 1)
        self.assert_one_ack_no_teaching()

    def test_network_failure_preserves_cursor_and_recovery_does_not_repeat_ack(self):
        run_live_iteration(self.store, Source([MessageBatch((self.message,), "1")]), self.settings)
        with self.assertRaises(OSError):
            run_live_iteration(self.store, Source([OSError("network unavailable")]), self.settings)
        state = self.store.get_sync_state("fixture")
        self.assertEqual(state["cursor"], "1")
        self.assertIsNotNone(state["last_error"])
        self.assert_one_ack_no_teaching()
        run_live_iteration(self.store, Source([MessageBatch((), "1")]), self.settings)
        self.assertIsNone(self.store.get_sync_state("fixture")["last_error"])
        self.assert_one_ack_no_teaching()

    def test_no_explicit_ack_only_does_not_start_business_processing(self):
        settings = {"business_database": str(self.root / "never-created.db")}
        result = run_live_iteration(self.store, Source([MessageBatch((self.message,), "1")]), settings)
        self.assertFalse(result["drain_enabled"])
        self.assertFalse(Path(settings["business_database"]).exists())
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT processed_at FROM events").fetchone()[0])

    def test_backfill_watch_and_run_once_rejected_before_source_activation(self):
        for command in ("watch", "run-once"):
            with patch("helpdesk.collector_cli.load_source") as factory, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main([command, "--mode", "BACKFILL", "--config", "not-read.toml"])
                self.assertEqual(result.exception.code, 2)
                factory.assert_not_called()

    def test_watch_ctrl_c_closes_source_after_ack_commit(self):
        config = self.root / "watch.toml"
        config.write_text("[collector]\ndatabase=" + json.dumps(self.store.path.as_posix()) +
            "\nbusiness_database=" + json.dumps(self.business_path.as_posix()) +
            '\nprocessing_mode="ACK_ONLY"\npoll_seconds=1\n', encoding="utf-8")
        source = Source([MessageBatch((self.message,), "1")])
        source.close = Mock()
        with patch("helpdesk.collector_cli.load_source", return_value=source), patch("helpdesk.collector_cli.time.sleep", side_effect=KeyboardInterrupt), redirect_stdout(StringIO()) as output:
            main(["watch", "--config", str(config)])
        source.close.assert_called_once_with()
        self.assertIn('"stopped": true', output.getvalue())
        self.assert_one_ack_no_teaching()

    def test_missing_archive_configuration_run_once_exits_safely_preserving_cursor(self):
        self.store.persist_batch("wecom_archive", SyncMode.LIVE, MessageBatch((), "7"), None)
        config = self.root / "missing-archive.toml"
        config.write_text("[collector]\ndatabase=" + json.dumps(self.store.path.as_posix()) +
            '\nprocessing_mode="ACK_ONLY"\n[source]\nfactory="helpdesk.wecom_archive:create_source"\n[archive]\nenabled=false\n', encoding="utf-8")
        with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            code = main(["run-once", "--config", str(config)])
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["health"], "NEEDS_ADMIN_CONFIGURATION")
        self.assertFalse(result["watch_started"])
        self.assertTrue(result["cursor_preserved"])
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(self.store.get_sync_state("wecom_archive")["cursor"], "7")
        secret = "private secret must never appear"
        with patch("helpdesk.collector_cli.load_source", side_effect=RuntimeError(secret)), redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
            code = main(["run-once", "--config", str(config)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["health"], "SOURCE_UNAVAILABLE")
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def cli_config(self):
        path = self.root / "cli.toml"
        path.write_text("[collector]\ndatabase=" + json.dumps(self.store.path.as_posix()) +
            "\nbusiness_database=" + json.dumps(self.business_path.as_posix()) +
            '\nprocessing_mode="ACK_ONLY"\n[source]\nname="fixture"\n', encoding="utf-8")
        return path

    def historical_message(self):
        utc, local = normalize_sent_time((datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        return replace(self.message, source_message_id="historical-question", sent_at_utc=utc, sent_at_local=local)

    def test_cli_sync_live_first_pull_history_is_stored_but_never_acknowledged(self):
        config = self.cli_config()
        old = self.historical_message()
        source = Source([MessageBatch((old,), "1")])
        with patch("helpdesk.collector_cli.load_source", return_value=source), redirect_stdout(StringIO()):
            self.assertEqual(main(["sync", "--mode", "LIVE", "--config", str(config)]), 0)
        self.assertIsNotNone(self.store.get_live_activation("fixture"))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events").fetchone()[0], 0)
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["drain", "--config", str(config)]), 0)
        business = Store(self.business_path)
        try:
            self.assertEqual(business.one("SELECT COUNT(*) FROM outbox")[0], 0)
            self.assertEqual(business.one("SELECT state FROM collector_answer_tasks")[0], "ACK_HELD")
        finally:
            business.close()

    def test_cli_drain_activates_and_suppresses_preexisting_raw_historical_event(self):
        config = self.cli_config()
        old = self.historical_message()
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((old,), "1"), None)
        self.assertIsNone(self.store.get_live_activation("fixture"))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events").fetchone()[0], 1)
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["drain", "--config", str(config)]), 0)
        activation = self.store.get_live_activation("fixture")
        self.assertIsNotNone(activation)
        self.assertEqual(self.store.get_message(old.message_id)["sent_at_utc"], old.sent_at_utc)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events").fetchone()[0], 0)
        business = Store(self.business_path)
        try:
            self.assertEqual(business.one("SELECT COUNT(*) FROM outbox")[0], 0)
            self.assertEqual(business.one("SELECT state FROM collector_answer_tasks")[0], "ACK_HELD")
        finally:
            business.close()
        with redirect_stdout(StringIO()):
            main(["drain", "--config", str(config)])
        self.assertEqual(self.store.get_live_activation("fixture"), activation)


if __name__ == "__main__":
    unittest.main()
