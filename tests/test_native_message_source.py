"""Use the actual native archive verifier; all acquisition files are fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from helpdesk.chat_text_archive import archive_clipboard
from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_cli import run_live_iteration
from helpdesk.message_sources import CursorExpired, SyncMode
from helpdesk.message_sync import SyncEngine
from helpdesk.native_message_source import NativeMessageSource, create_source
from helpdesk.storage import Store


class NativeMessageSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.archive = self.root / "native"
        self.archive.mkdir()
        self.journal = self.root / "journal"
        self.journal.mkdir()
        self.store = CollectorStore(self.root / "messages.db")

    def tearDown(self):
        self.tmp.cleanup()

    def acquisition(self, identity, *, text="为什么不选B？\r\n[图片]\r\n", folder_name=None, observed_group="English答疑群（已退出）"):
        result = self.journal / (identity + ".json")
        result.write_text(json.dumps({"attempt_id": identity, "tool": "Clipboard", "is_error": False,
            "content": [{"type": "text", "text": "Clipboard content:\n" + text}]}, ensure_ascii=False), encoding="utf-8")
        (self.journal / ("attempt-" + identity + ".json")).write_text(json.dumps({"attempt_id": identity,
            "tool": "Clipboard", "arguments": {"mode": "get"}, "status": "TOOL_RETURNED",
            "result_path": str(result), "started_at": "2026-09-30T03:00:00+00:00"}), encoding="utf-8")
        folder = archive_clipboard(result, self.archive, observed_group=observed_group)
        if folder_name:
            folder = folder.rename(self.archive / folder_name)
        return folder

    def test_actual_archive_contract_preserves_bytes_and_unknown_identity_time(self):
        folder = self.acquisition("a" * 32)
        before = {p: p.read_bytes() for p in folder.iterdir()}
        source = NativeMessageSource(self.archive)
        message = source.fetch_page(None, SyncMode.LIVE, 1000).messages[0]
        self.assertEqual(message.raw_content.encode("utf-8"), (folder / "原始文字记录.txt").read_bytes())
        self.assertEqual(message.source_type, "windows_gui")
        self.assertIsNone(message.source_message_id)
        self.assertIsNone(message.source_seq)
        self.assertTrue(message.room_id.startswith("unverified-room:"))
        self.assertEqual(message.sender_id, "unverified-sender:" + "a" * 32)
        self.assertEqual((message.sent_at_raw, message.sent_at_utc, message.sent_at_local), (None, None, None))
        self.assertEqual((message.source_confidence, message.time_confidence, message.parse_status), ("low", "low", "pending_time_verification"))
        self.assertFalse(message.raw_payload["identity_verified"])
        self.assertFalse(message.raw_payload["desktop_listener_verified"])
        self.assertEqual(message.raw_payload["provenance"]["acquired_at"], "2026-09-30T03:00:00+00:00")
        self.assertIn("original_sender", message.raw_payload["review_required"])
        self.assertEqual(before, {p: p.read_bytes() for p in folder.iterdir()})

    def test_cursor_restart_finds_new_random_directory_sorting_before_existing(self):
        self.acquisition("f" * 32, folder_name="zzzz")
        source = NativeMessageSource(self.archive)
        first = SyncEngine(self.store, source, page_size=1).sync()
        self.acquisition("0" * 32, folder_name="0000")
        restarted = CollectorStore(self.store.path)
        second = SyncEngine(restarted, NativeMessageSource(self.archive), page_size=1).sync()
        self.assertEqual((first.inserted_count, second.inserted_count), (1, 1))
        self.assertEqual(SyncEngine(restarted, NativeMessageSource(self.archive)).sync().inserted_count, 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_same_acquisition_repeated_batch_is_one_message_event(self):
        self.acquisition("a" * 32)
        source = NativeMessageSource(self.archive)
        first = source.fetch_page(None, SyncMode.LIVE, 1000)
        second = source.fetch_page(None, SyncMode.LIVE, 1000)
        self.assertEqual(first.messages[0].message_id, second.messages[0].message_id)
        self.store.persist_batch(source.source_name, SyncMode.LIVE, first, None)
        result = self.store.persist_batch(source.source_name, SyncMode.LIVE, second, first.next_cursor)
        self.assertEqual(result.duplicate_count, 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_distinct_acquisitions_of_identical_text_are_both_retained(self):
        self.acquisition("a" * 32)
        self.acquisition("b" * 32)
        source = NativeMessageSource(self.archive)
        result = SyncEngine(self.store, source, page_size=1).sync()
        self.assertEqual((result.pages, result.inserted_count), (2, 2))
        with self.store.connect() as db:
            rows = db.execute("SELECT raw_content,fingerprint,message_id FROM messages").fetchall()
        self.assertEqual(rows[0]["raw_content"], rows[1]["raw_content"])
        self.assertNotEqual(rows[0]["fingerprint"], rows[1]["fingerprint"])
        self.assertNotEqual(rows[0]["message_id"], rows[1]["message_id"])

    def test_live_runner_and_backfill_keep_metadata_unknown_and_never_ack(self):
        self.acquisition("a" * 32)
        source = NativeMessageSource(self.archive)
        path = self.root / "business.db"
        result = run_live_iteration(self.store, source, {"processing_mode": "ACK_ONLY", "business_database": str(path)})
        self.assertEqual((result["sync"]["inserted_count"], result["events_processed"], result["pending_ack_count"]), (1, 1, 0))
        live_cursor = self.store.get_sync_state(source.source_name)["cursor"]
        SyncEngine(self.store, source).sync(SyncMode.BACKFILL)
        self.assertEqual(self.store.get_sync_state(source.source_name)["cursor"], live_cursor)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT SUM(auto_reply_allowed) FROM events").fetchone()[0], 0)
        business = Store(path)
        try:
            self.assertEqual(business.one("SELECT state FROM collector_answer_tasks")[0], "ACK_HELD")
            self.assertEqual(business.one("SELECT COUNT(*) FROM outbox")[0], 0)
            self.assertEqual(business.one("SELECT COUNT(*) FROM performance_units")[0], 0)
        finally:
            business.close()

    def test_tampered_archive_and_changed_verified_evidence_do_not_advance_cursor(self):
        folder = self.acquisition("a" * 32)
        source = NativeMessageSource(self.archive)
        SyncEngine(self.store, source).sync()
        cursor = self.store.get_sync_state(source.source_name)["cursor"]
        manifest = folder / "manifest.json"
        value = json.loads(manifest.read_bytes())
        value["observed_group_label"] = "different observed group"
        manifest.write_text(json.dumps(value), encoding="utf-8")
        # Even a still-verifiable manifest change for a previously seen ID is detected.
        with self.assertRaises(ValueError):
            SyncEngine(self.store, source).sync()
        self.assertEqual(self.store.get_sync_state(source.source_name)["cursor"], cursor)
        (folder / "原始文字记录.txt").write_bytes(b"tampered raw")
        with self.assertRaises(ValueError):
            source.fetch_page(None, SyncMode.LIVE, 1000)

    def test_cursor_capacity_root_mode_and_page_validation_fail_explicitly(self):
        self.acquisition("a" * 32)
        self.acquisition("b" * 32)
        with self.assertRaises(CursorExpired):
            NativeMessageSource(self.archive, max_acquisitions=1).fetch_page(None, SyncMode.LIVE, 1)
        source = NativeMessageSource(self.archive)
        cursor = source.fetch_page(None, SyncMode.LIVE, 1).next_cursor
        with self.assertRaises(CursorExpired):
            source.fetch_page(cursor, SyncMode.BACKFILL, 1)
        for size in (True, 0, -1, 1001):
            with self.assertRaises(ValueError):
                source.fetch_page(None, SyncMode.LIVE, size)
        with self.assertRaises(CursorExpired):
            source.fetch_page("not-json", SyncMode.LIVE, 1)

    def test_factory_requires_explicit_native_root_and_kind(self):
        source = create_source({"source": {"name": "windows_native_staging", "kind": "native_clipboard_archive", "native_root": str(self.archive)}})
        self.assertEqual(source.source_name, "windows_native_staging")
        with self.assertRaises(ValueError):
            create_source({"source": {"native_root": str(self.archive)}})
        with self.assertRaises(FileNotFoundError):
            create_source({"source": {"kind": "native_clipboard_archive", "native_root": str(self.root / "missing")}})

    def test_scope_excludes_non_english_but_includes_exited_english_as_unverified(self):
        self.acquisition("a" * 32, observed_group="eNgLiSh答疑群（已退出）")
        self.acquisition("b" * 32, observed_group="数学答疑群")
        source = NativeMessageSource(self.archive)
        result = SyncEngine(self.store, source).sync()
        self.assertEqual(result.inserted_count, 1)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM messages").fetchone()
        self.assertEqual(row["room_name"], "eNgLiSh答疑群（已退出）")
        self.assertTrue(row["room_id"].startswith("unverified-room:"))
        self.assertIsNone(row["sent_at_utc"])
        self.assertEqual(row["source_confidence"], "low")


if __name__ == "__main__":
    unittest.main()
