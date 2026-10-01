from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from helpdesk.collector_storage import CollectorStore, CursorConflict
from helpdesk.message_sources import CursorExpired, MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.message_sync import SyncEngine


def message(number=1, **changes):
    utc, local = normalize_sent_time("2026-09-30T22:58:32+08:00")
    return NormalizedMessage(**(dict(source_type="wecom_archive", source_message_id=f"m{number}", room_id="room", sender_id="student", sent_at_raw=1790780312000, sent_at_utc=utc, sent_at_local=local, raw_content="question", raw_payload={"msgid": f"m{number}"}) | changes))


class FixtureSource:
    source_name = "fixture"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch_page(self, cursor, mode, page_size):
        self.calls.append((cursor, mode, page_size))
        result = self.pages[(mode, cursor)]
        if isinstance(result, Exception):
            raise result
        return result


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CollectorStore(Path(self.temp.name) / "messages.db")

    def tearDown(self):
        self.temp.cleanup()

    def count(self, table):
        with self.store.connect() as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_same_batch_twice_one_message_event(self):
        one = message()
        first = self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((one,), "1"), None)
        replay = self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "1"), "1")
        self.assertEqual(len(first.inserted_ids), 1)
        self.assertEqual(replay.duplicate_count, 1)
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.count("events"), 1)

    def test_first_live_activation_saves_history_without_replying_to_backlog(self):
        cutoff = self.store.activate_live("fixture", activated_at="2026-09-30T23:05:00+08:00")
        fresh_utc, fresh_local = normalize_sent_time("2026-09-30T23:05:00+08:00")
        fresh = message(2, sent_at_utc=fresh_utc, sent_at_local=fresh_local)
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(), fresh), "2"), None)
        with self.store.connect() as db:
            rows = db.execute("SELECT m.source_message_id,m.sent_at_local,e.auto_reply_allowed,e.last_error FROM messages m JOIN events e USING(message_id) ORDER BY m.source_message_id").fetchall()
        self.assertEqual([row[2] for row in rows], [0, 1])
        self.assertEqual(rows[0][1], "2026-09-30T22:58:32+08:00")
        self.assertEqual(rows[0][3], "PRE_ACTIVATION_HISTORY_ACK_FORBIDDEN")
        self.assertEqual(self.store.get_sync_state("fixture")["cursor"], "2")
        self.assertEqual(self.store.get_live_activation("fixture"), cutoff)

    def test_restart_keeps_activation_and_accepts_new_messages_from_downtime(self):
        first = self.store.activate_live("fixture", activated_at="2026-09-30T22:00:00+08:00")
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((), "1"), None)
        restarted = CollectorStore(self.store.path)
        self.assertEqual(restarted.activate_live("fixture", activated_at="2026-10-01T08:00:00+08:00"), first)
        restarted.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "2"), "1")
        with restarted.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events").fetchone()[0], 1)

    def test_activation_suppresses_existing_old_events_without_rewriting_original(self):
        original = message()
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((original,), "1"), None)
        self.store.activate_live("fixture", activated_at="2026-09-30T23:00:00+08:00")
        self.assertEqual(self.store.get_message(original.message_id)["sent_at_local"], original.sent_at_local)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events").fetchone()[0], 0)
        self.assertEqual(self.store.get_sync_state("fixture")["cursor"], "1")

    def test_source_exception_secrets_are_not_persisted_in_monitoring(self):
        source = FixtureSource({(SyncMode.LIVE, None): OSError("token=private-example-do-not-report")})
        with self.assertRaises(OSError):
            SyncEngine(self.store, source).sync()
        state = self.store.get_sync_state("fixture")
        self.assertEqual(state["last_error"], "OSError")
        self.assertNotIn("private-example", str(self.store.monitoring("fixture")))

    def test_replay_changes_ingest_metadata_without_creating_conflict(self):
        first = message(ingested_at="2026-09-30T23:05:00+08:00")
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((first,), "1"), None)
        replay = replace(first, message_id="new-observation-id", ingested_at="2026-10-01T08:00:00+08:00", updated_at="2026-10-01T08:01:00+08:00")
        result = self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((replay,), "1"), "1")
        self.assertEqual((result.duplicate_count, result.conflict_count), (1, 0))
        self.assertEqual(self.count("events"), 1)
        self.assertEqual(self.store.get_message(first.message_id)["ingested_at"], first.ingested_at)

    def test_1001_messages_continue_paging(self):
        source = FixtureSource({(SyncMode.LIVE, None): MessageBatch(tuple(message(i) for i in range(1000)), "1000", True), (SyncMode.LIVE, "1000"): MessageBatch((message(1000),), "1001")})
        result = SyncEngine(self.store, source).sync()
        self.assertEqual((result.pages, result.inserted_count, result.cursor), (2, 1001, "1001"))
        self.assertEqual(self.count("events"), 1001)

    def test_db_failure_does_not_advance_cursor(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        source = FixtureSource({(SyncMode.LIVE, None): MessageBatch((message(),), "1")})
        with self.assertRaises(sqlite3.IntegrityError):
            SyncEngine(self.store, source).sync()
        self.assertIsNone(self.store.get_sync_state("fixture")["cursor"])
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("events"), 0)

    def test_crash_between_write_and_cursor_rolls_back_then_restart(self):
        batch = MessageBatch((message(),), "1")
        def crash(db):
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            raise RuntimeError("simulated process crash before cursor")
        with self.assertRaises(RuntimeError):
            self.store.persist_batch("fixture", SyncMode.LIVE, batch, None, before_cursor=crash)
        self.assertEqual(self.count("messages"), 0)
        restarted = CollectorStore(self.store.path)
        restarted.persist_batch("fixture", SyncMode.LIVE, batch, None)
        restarted.persist_batch("fixture", SyncMode.LIVE, batch, "1")
        self.assertEqual(self.count("events"), 1)

    def test_actual_process_exit_before_commit_recovers(self):
        script = """
import os, sys
from helpdesk.collector_storage import CollectorStore
from helpdesk.message_sources import NormalizedMessage, MessageBatch, SyncMode
store = CollectorStore(sys.argv[1])
m = NormalizedMessage(source_type='wecom_archive', source_message_id='crash-id', room_id='r', sender_id='s')
store.persist_batch('fixture', SyncMode.LIVE, MessageBatch((m,), '1'), None, before_cursor=lambda db: os._exit(71))
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.store.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 71, result.stderr)
        recovered = CollectorStore(self.store.path)
        self.assertIsNone(recovered.get_sync_state("fixture")["cursor"])
        self.assertEqual(self.count("messages"), 0)
        recovered.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "1"), None)
        self.assertEqual(self.count("events"), 1)

    def test_monitoring_empty_success_is_healthy(self):
        SyncEngine(self.store, FixtureSource({(SyncMode.LIVE, None): MessageBatch((), None)})).sync()
        stats = self.store.monitoring("fixture")
        self.assertEqual(stats["sync_health"], "HEALTHY")
        self.assertEqual(stats["messages_received_today"], 0)
        self.assertIsNone(stats["sync_lag_seconds"])

    def test_monitoring_without_tzdata_keeps_daily_boundaries_and_bounded_lookups(self):
        rows = [
            ("fixture", "LIVE", "2026-09-30T15:59:59+00:00", 7, 3, 0),
            ("fixture", "LIVE", "2026-09-30T16:00:00+00:00", 2, 1, 0),
            ("fixture", "LIVE", "2026-10-01T23:59:59+08:00", 4, 2, 0),
            ("fixture", "LIVE", "2026-10-02T00:00:00+08:00", 9, 4, 0),
            ("other", "LIVE", "2026-10-01T12:00:00+08:00", 10, 8, 0),
            ("fixture", "BACKFILL", "2026-10-01T12:00:00+08:00", 10, 8, 0),
        ] * 300
        with self.store.connect() as db:
            db.executemany("INSERT INTO sync_batches(source_name,mode,committed_at,received_count,duplicate_count,conflict_count) VALUES(?,?,?,?,?,?)", rows)
        with patch("helpdesk.message_sources.ZoneInfo",
                   side_effect=ZoneInfoNotFoundError("fixture tzdata unavailable")) as lookup:
            stats = self.store.monitoring("fixture", now=datetime.fromisoformat("2026-10-01T12:00:00+00:00"))
        self.assertEqual(stats["messages_received_today"], 1800)
        self.assertEqual(stats["duplicate_messages_today"], 900)
        lookup.assert_called_once_with("Asia/Shanghai")

    def test_gui_same_fingerprint_is_evidence_not_primary_key(self):
        a = message(source_type="windows_gui", source_message_id=None, fingerprint="same-image", sender_id="a")
        b = replace(a, message_id="b-message", sender_id="b")
        result = self.store.persist_batch("gui", SyncMode.LIVE, MessageBatch((a, b), "seen"), None)
        self.assertEqual(len(result.inserted_ids), 2)
        self.assertEqual(self.count("message_conflicts"), 1)
        self.assertEqual(self.count("students"), 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT SUM(auto_reply_allowed) FROM events").fetchone()[0], 0)

    def test_same_student_repeated_gui_text_retained(self):
        a = message(source_type="windows_gui", source_message_id=None, fingerprint="same")
        b = replace(a, message_id="another-observation")
        result = self.store.persist_batch("gui", SyncMode.LIVE, MessageBatch((a, b), "seen"), None)
        self.assertEqual(len(result.inserted_ids), 2)

    def test_two_students_identical_image_official_ids_independent(self):
        a = message(1, message_type="image", media_id="same-image", sender_id="a")
        b = message(2, message_type="image", media_id="same-image", sender_id="b")
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((a, b), "2"), None)
        self.assertEqual(self.count("messages"), 2)
        self.assertEqual(self.count("message_media"), 2)

    def test_backfill_cursor_and_reply_permission_isolated(self):
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "live"), None)
        self.store.persist_batch("fixture", SyncMode.BACKFILL, MessageBatch((message(2),), "history"), None)
        self.assertEqual(self.store.get_sync_state("fixture")["cursor"], "live")
        self.assertEqual(self.store.get_sync_state("fixture", SyncMode.BACKFILL)["cursor"], "history")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT auto_reply_allowed FROM events WHERE mode='BACKFILL'").fetchone()[0], 0)

    def test_gui_ambiguous_time_never_fabricates_sent_time(self):
        m = NormalizedMessage(source_type="windows_gui", room_id="r", sender_id="s", sent_at_raw="昨天 23:03")
        self.assertIsNone(m.sent_at_utc)
        self.assertIsNone(m.sent_at_local)
        self.assertEqual((m.time_confidence, m.parse_status), ("low", "pending_time_verification"))
        with self.assertRaises(ValueError):
            normalize_sent_time("23:03")

    def test_restart_resumes_last_committed_cursor(self):
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "1"), None)
        source = FixtureSource({(SyncMode.LIVE, "1"): MessageBatch((message(2),), "2")})
        SyncEngine(CollectorStore(self.store.path), source).sync()
        self.assertEqual(source.calls[0][0], "1")
        self.assertEqual(self.count("messages"), 2)

    def test_empty_success_not_failure(self):
        source = FixtureSource({(SyncMode.LIVE, None): MessageBatch((), None)})
        result = SyncEngine(self.store, source).sync()
        state = self.store.get_sync_state("fixture")
        self.assertEqual(result.inserted_count, 0)
        self.assertIsNotNone(state["last_success_at"])
        self.assertIsNone(state["last_error"])

    def test_expired_cursor_review_no_silent_restart(self):
        source = FixtureSource({(SyncMode.LIVE, None): CursorExpired("fixture expired")})
        with self.assertRaises(CursorExpired):
            SyncEngine(self.store, source).sync()
        with self.assertRaises(CursorExpired):
            SyncEngine(self.store, source).sync()
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(self.store.get_sync_state("fixture")["status"], "NEEDS_REVIEW")

    def test_concurrent_failure_cannot_clear_expired_cursor_review(self):
        self.store.record_error("fixture", SyncMode.LIVE, "expired", review=True)
        self.store.record_error("fixture", SyncMode.LIVE, "stale concurrent writer failed")
        state = self.store.get_sync_state("fixture")
        self.assertEqual((state["status"], state["last_error"]), ("NEEDS_REVIEW", "expired"))

    def test_cli_monitoring_tracks_daily_duplicates_per_source(self):
        from helpdesk.collector_cli import status
        m = message()
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((m,), "1"), None)
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((m,), "1"), "1")
        self.store.persist_batch("other", SyncMode.LIVE, MessageBatch((message(2),), "2"), None)
        stats = status(self.store, source_name="fixture")
        self.assertEqual(stats["messages_received_today"], 1)
        self.assertEqual(stats["duplicate_messages_today"], 1)
        self.assertEqual(stats["events_pending"], 1)

    def test_network_error_after_page_keeps_committed_page(self):
        source = FixtureSource({(SyncMode.LIVE, None): MessageBatch((message(),), "1", True), (SyncMode.LIVE, "1"): OSError("fixture network failure")})
        with self.assertRaises(OSError):
            SyncEngine(self.store, source).sync()
        self.assertEqual(self.store.get_sync_state("fixture")["cursor"], "1")
        self.assertEqual(self.count("messages"), 1)

    def test_stable_id_changed_payload_retains_original_and_conflict(self):
        original = message()
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((original,), "1"), None)
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(raw_content="changed"),), "2"), "1")
        self.assertEqual(self.store.get_message(original.message_id)["raw_content"], "question")
        self.assertEqual(self.count("message_conflicts"), 1)
        self.assertEqual(self.count("events"), 1)

    def test_stale_writer_cursor_rejected(self):
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(),), "1"), None)
        with self.assertRaises(CursorConflict):
            self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((message(2),), "2"), None)
        self.assertEqual(self.count("messages"), 1)

    def test_time_fields_independent_and_full(self):
        m = message(ingested_at="2026-09-30T23:05:00+08:00", processed_at="2026-10-01T01:00:00+08:00", answered_at="2026-10-01T02:00:00+08:00")
        self.store.persist_batch("fixture", SyncMode.LIVE, MessageBatch((m,), "1"), None)
        row = self.store.get_message(m.message_id)
        self.assertEqual(row["sent_at_local"], "2026-09-30T22:58:32+08:00")
        self.assertEqual(row["ingested_at"], "2026-09-30T23:05:00+08:00")
        self.assertNotEqual(row["processed_at"], row["answered_at"])

    def test_field_validation(self):
        for changes in ({"source_message_id": None}, {"room_id": ""}, {"message_type": "invalid"}, {"time_confidence": "certain"}, {"sent_at_utc": "2026-09-30T22:58:32"}, {"sent_at_local": "2026-10-01T22:58:32+08:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                message(**changes)
        self.assertEqual(normalize_sent_time(0, unit="milliseconds")[0], "1970-01-01T00:00:00+00:00")

    def test_no_cursor_progress_rejected(self):
        source = FixtureSource({(SyncMode.LIVE, None): MessageBatch((message(),), None, True)})
        with self.assertRaises(ValueError):
            SyncEngine(self.store, source).sync()
        self.assertEqual(self.count("messages"), 0)


if __name__ == "__main__":
    unittest.main()
