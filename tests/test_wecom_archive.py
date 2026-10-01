import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from helpdesk.collector_storage import CollectorStore
from helpdesk.message_media import ArchiveMediaWorker
from helpdesk.message_sources import SyncMode
from helpdesk.wecom_archive import (ArchiveAuthorization, ArchiveNotConfigured,
    ArchiveProtocolError, MediaChunk, SdkBridgeTransport, WecomArchiveSource)


AUTH = ArchiveAuthorization(True, True, True, True, "mock-only authorization fixture")


def payload(n=1, **values):
    return {"msgid": f"m{n}", "from": "student", "tolist": ["teacher"], "roomid": "r",
            "msgtime": 1790780280000, "msgtype": "text", "text": {"content": "why B?"}, **values}


class FakeSdk:
    def __init__(self, records=()):
        self.records, self.media_calls = list(records), []
        self.fail = False

    def get_chat_data(self, seq, limit):
        return {"errcode": 0, "chatdata": [{"seq": n, "msgid": p["msgid"], "decrypted": p}
                for n, p in self.records if n > seq][:limit]}

    def decrypt_message(self, envelope):
        return envelope["decrypted"]

    def get_media_data(self, sdkfileid, indexbuf):
        self.media_calls.append((sdkfileid, indexbuf))
        if indexbuf == "next" and self.fail:
            raise OSError("simulated failure; never persisted content")
        return MediaChunk(b"abc", "next", False) if not indexbuf else MediaChunk(b"def", "", True)


class ArchiveTests(unittest.TestCase):
    def test_explicit_authorization_gate(self):
        with self.assertRaises(ArchiveNotConfigured):
            WecomArchiveSource(FakeSdk(), ArchiveAuthorization())
        with self.assertRaises(ValueError):
            ArchiveAuthorization("true", True, True, True, "evidence")

    def test_official_millisecond_time_and_quote_preserved_without_invented_relation(self):
        sdk = FakeSdk([(1, payload(text={"content": "这是一条引用/回复消息：\nnick\nB"}))])
        message = WecomArchiveSource(sdk, AUTH).fetch_page(None, SyncMode.LIVE, 1000).messages[0]
        self.assertEqual(message.sent_at_local, "2026-09-30T22:58:00+08:00")
        self.assertIsNone(message.quoted_message_id)
        self.assertIn("nick", message.raw_content)

    def test_thousand_and_1001_pagination_and_persistent_dedup(self):
        sdk = FakeSdk([(n, payload(n)) for n in range(1, 1002)])
        source = WecomArchiveSource(sdk, AUTH, min_call_interval=.015)
        page = source.fetch_page(None, SyncMode.LIVE, 1000)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_cursor, "1000")
        tail = source.fetch_page(page.next_cursor, SyncMode.LIVE, 1000)
        self.assertEqual(tail.messages[0].source_message_id, "m1001")
        with tempfile.TemporaryDirectory() as directory:
            store = CollectorStore(Path(directory) / "messages.db")
            first = store.persist_batch(source.source_name, SyncMode.LIVE, page, None)
            second = store.persist_batch(source.source_name, SyncMode.LIVE, page, "1000")
            self.assertEqual(len(first.inserted_ids), 1000)
            self.assertEqual(second.duplicate_count, 1000)

    def test_invalid_sequence_or_mismatched_decryption_halts_page(self):
        class BadSdk(FakeSdk):
            def get_chat_data(self, seq, limit):
                return {"errcode": 0, "chatdata": [{"seq": seq, "msgid": "m1", "decrypted": payload()}]}
        with self.assertRaises(ArchiveProtocolError):
            WecomArchiveSource(BadSdk(), AUTH).fetch_page("2", SyncMode.LIVE, 1000)
        sdk = FakeSdk([(1, payload())])
        sdk.decrypt_message = lambda _: payload(999)
        with self.assertRaises(ArchiveProtocolError):
            WecomArchiveSource(sdk, AUTH).fetch_page(None, SyncMode.LIVE, 1000)

    def test_zero_time_is_pending_verification_and_direct_room_stable(self):
        sdk = FakeSdk([(1, payload(msgtime=0, roomid=""))])
        message = WecomArchiveSource(sdk, AUTH).fetch_page(None, SyncMode.LIVE, 1).messages[0]
        self.assertIsNone(message.sent_at_utc)
        self.assertEqual(message.time_confidence, "low")
        self.assertTrue(message.room_id.startswith("direct:"))

    def test_switch_log_is_preserved_without_blocking_cursor(self):
        sdk = FakeSdk([(1, {"msgid": "m1", "action": "switch", "time": 1790780280000, "user": "teacher"})])
        batch = WecomArchiveSource(sdk, AUTH).fetch_page(None, SyncMode.LIVE, 1000)
        self.assertEqual(batch.next_cursor, "1")
        self.assertEqual(batch.messages[0].parse_status, "system_event")
        self.assertEqual(batch.messages[0].message_type, "other")

    def test_stdio_bridge_contract_only_mock_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "mock_bridge.py"
            script.write_text("import sys,json\nr=json.load(sys.stdin)\nprint(json.dumps({'protocol':r['protocol'],'operation':r['operation'],'sdk_code':0,'errcode':0,'result':{'errcode':0,'chatdata':[]}}))", encoding="utf-8")
            bridge = SdkBridgeTransport((sys.executable, str(script)), authorization=AUTH)
            self.assertEqual(bridge.get_chat_data(0, 1000)["chatdata"], [])

    def test_bridge_rejects_boolean_codes_and_missing_required_fields(self):
        bridge = SdkBridgeTransport((sys.executable,), authorization=AUTH)
        good = {"protocol": "wecom-archive-sdk-v1", "operation": "get_chat_data",
                "sdk_code": 0, "errcode": 0, "result": {"errcode": 0, "chatdata": []}}
        bad = [dict(good, sdk_code=False), dict(good, errcode=False),
               dict(good, sdk_code=0.0), dict(good, errcode="0")]
        bad.extend({k: v for k, v in good.items() if k != missing} for missing in good)
        for response in bad:
            with self.subTest(response=response):
                result = SimpleNamespace(returncode=0, stdout=json.dumps(response))
                with patch("helpdesk.wecom_archive.subprocess.run", return_value=result):
                    with self.assertRaises(ArchiveProtocolError):
                        bridge.get_chat_data(0, 1000)

    def test_source_rejects_boolean_inner_api_code(self):
        sdk = FakeSdk()
        sdk.get_chat_data = lambda *args: {"errcode": False, "chatdata": []}
        with self.assertRaises(ArchiveProtocolError):
            WecomArchiveSource(sdk, AUTH).fetch_page(None, SyncMode.LIVE, 1000)

    def _setup_media(self, directory, sdk, count=1):
        md5 = hashlib.md5(b"abcdef").hexdigest()
        sdk.records = [(n, payload(n, **{"from": f"student{n}", "msgtype": "image", "image": {
            "sdkfileid": "shared-resource", "filesize": 6, "md5sum": md5,
            "filename": "../../hostile.jpg"}})) for n in range(1, count + 1)]
        source = WecomArchiveSource(sdk, AUTH)
        store = CollectorStore(Path(directory) / "messages.db")
        batch = source.fetch_page(None, SyncMode.LIVE, 1000)
        store.persist_batch(source.source_name, SyncMode.LIVE, batch, None)
        worker = ArchiveMediaWorker(store, sdk, AUTH, root=Path(directory) / "media")
        return store, worker, batch

    def test_failed_download_keeps_message_and_resumes_committed_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = FakeSdk(); sdk.fail = True
            store, worker, batch = self._setup_media(directory, sdk)
            self.assertEqual(worker.run_pending(), {"downloaded": 0, "failed": 1})
            self.assertIsNotNone(store.get_message(batch.messages[0].message_id))
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT status FROM message_media").fetchone()[0], "FAILED")
                self.assertEqual(db.execute("SELECT indexbuf FROM archive_media_progress").fetchone()[0], "next")
            sdk.fail = False
            # Restart with a new worker: it consumes saved SDK media index.
            worker = ArchiveMediaWorker(store, sdk, AUTH, root=Path(directory) / "media")
            self.assertEqual(worker.run_pending(), {"downloaded": 1, "failed": 0})
            self.assertEqual(sdk.media_calls, [("shared-resource", ""), ("shared-resource", "next"), ("shared-resource", "next")])
            row = store.get_message(batch.messages[0].message_id)
            self.assertEqual(Path(row["local_media_path"]).read_bytes(), b"abcdef")
            self.assertTrue(Path(row["local_media_path"]).is_relative_to(Path(directory) / "media"))

    def test_resource_reuse_keeps_two_students_messages_and_original_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = FakeSdk()
            store, worker, batch = self._setup_media(directory, sdk, count=2)
            self.assertEqual(worker.run_pending()["downloaded"], 2)
            self.assertEqual(len(sdk.media_calls), 2)
            rows = [store.get_message(x.message_id) for x in batch.messages]
            self.assertNotEqual(rows[0]["message_id"], rows[1]["message_id"])
            self.assertEqual(rows[0]["local_media_path"], rows[1]["local_media_path"])
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT original_filename FROM message_media LIMIT 1").fetchone()[0], "../../hostile.jpg")

    def test_size_checksum_and_cursor_failures_are_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = FakeSdk()
            store, worker, batch = self._setup_media(directory, sdk)
            sdk.get_media_data = lambda *args: MediaChunk(b"bad", "", True)
            self.assertEqual(worker.run_pending()["failed"], 1)
            self.assertIsNone(store.get_message(batch.messages[0].message_id)["local_media_path"])
            sdk.get_media_data = lambda *args: MediaChunk(b"abc", "", False)
            self.assertEqual(worker.run_pending()["failed"], 1)


if __name__ == "__main__":
    unittest.main()
