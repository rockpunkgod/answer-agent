from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_test_ack import queue_test_ack, CollectorTestAckWorkflow
from helpdesk.delivery import MockDesktop
from helpdesk.mcp_test_delivery import MCPTestAnswerDesktop
from helpdesk.message_sources import NormalizedMessage, MessageBatch, SyncMode, normalize_sent_time
from helpdesk.service import Helpdesk
from helpdesk.storage import Store
from helpdesk.test_routing import TestRecipient


class CollectorTestAckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collector = CollectorStore(self.root / "collector.db")
        self.store = Store(self.root / "business.db")
        Helpdesk(self.store).bind("source-group", "student", "学生", verified=True)
        utc, local = normalize_sent_time("2026-09-30T23:03:00+08:00")
        m = NormalizedMessage(source_type="wecom_archive", source_message_id="fixture-source", room_id="source-group", sender_id="student",
            normalized_text="请问怎么做？", raw_content="请问怎么做？", sent_at_utc=utc, sent_at_local=local, raw_payload={"fixture": True})
        self.collector_id = m.message_id
        self.collector.persist_batch("fixture", SyncMode.LIVE, MessageBatch((m,), "1"), None)
        CollectorDispatcher(self.collector, self.store).drain()
        self.ack = self.store.one("SELECT * FROM outbox WHERE purpose='ACK'")
        self.recipient = TestRecipient("wecom", "operator-pinned-test-session", "苇中鹤", "fixture operator target evidence", datetime.now(timezone.utc))
        self.evidence = self.root / "fixture-source-evidence.txt"
        self.evidence.write_text("fixture only, never a real WeCom capture", encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def queue(self):
        return queue_test_ack(self.store, self.collector, self.ack["id"], self.recipient, source_evidence_path=self.evidence)

    def flow(self, fault=None):
        return CollectorTestAckWorkflow(self.store, self.collector, MockDesktop(self.root / "mock-desktop.db", fault=fault))

    def test_copy_preserves_original_ack_and_source_lineage(self):
        oid = self.queue()
        self.assertEqual(self.queue(), oid)
        self.assertEqual(self.flow().dispatch(oid), "SENT_UI_CONFIRMED")
        self.assertEqual(self.store.one("SELECT state FROM outbox WHERE id=?", (self.ack["id"],))[0], "PENDING")
        copy = self.store.one("SELECT * FROM outbox WHERE id=?", (oid,))
        self.assertEqual((copy["body"], copy["purpose"], copy["simulated"]), ("收到", "PROGRESS", 1))
        mapping = self.store.one("SELECT * FROM collector_test_ack_copies")
        self.assertEqual((mapping["collector_message_id"], mapping["source_binding_id"], mapping["observation_mode"]), (self.collector_id, self.ack["binding_id"], "FIXTURE"))
        self.assertNotEqual(copy["binding_id"], self.ack["binding_id"])
        for table in ("cases", "questions", "turns", "answers", "performance_units"):
            self.assertEqual(self.store.one("SELECT COUNT(*) FROM " + table)[0], 0)

    def test_unknown_result_never_resubmits(self):
        oid = self.queue()
        flow = self.flow("unknown")
        self.assertEqual(flow.dispatch(oid), "SEND_UNKNOWN")
        self.assertEqual(flow.dispatch(oid), "SEND_UNKNOWN")
        self.assertEqual(len(flow.desktop.receipts()), 1)
        self.assertEqual(self.store.one("SELECT state FROM outbox WHERE id=?", (self.ack["id"],))[0], "PENDING")

    def test_original_group_and_default_dispatch_forbidden(self):
        self.queue()
        with self.assertRaises(ValueError):
            self.flow().dispatch()
        with self.assertRaises(ValueError):
            self.flow().dispatch(self.ack["id"])

    def test_source_conflict_after_queue_holds_test_send(self):
        oid = self.queue()
        with self.collector.connect() as db:
            db.execute("UPDATE events SET auto_reply_allowed=0")
        self.assertEqual(self.flow().dispatch(oid), "STALE")
        self.assertEqual(self.store.one("SELECT state FROM outbox WHERE id=?", (self.ack["id"],))[0], "PENDING")

    def test_fixture_cannot_be_claimed_actual_from_file(self):
        with self.assertRaises(ValueError):
            queue_test_ack(self.store, self.collector, self.ack["id"], self.recipient,
                source_evidence_path=self.evidence, observation_mode="ACTUAL")
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM collector_test_ack_copies")[0], 0)

    def test_body_and_target_tamper_fail_closed(self):
        oid = self.queue()
        with self.store.transaction():
            self.store.execute("UPDATE outbox SET body='讲解答案' WHERE id=?", (oid,))
        self.assertEqual(self.flow().dispatch(oid), "STALE")

    def test_evidence_content_change_blocks_dispatch(self):
        oid = self.queue()
        self.evidence.write_text("changed evidence", encoding="utf-8")
        self.assertEqual(self.flow().dispatch(oid), "STALE")

    def test_fixture_real_transport_rejected_before_any_desktop_call(self):
        oid = self.queue()
        desktop = MCPTestAnswerDesktop(object(), {}, self.root)
        flow = CollectorTestAckWorkflow(self.store, self.collector, desktop)
        self.assertEqual(flow.dispatch(oid), "STALE")
        self.assertEqual(desktop.last_frames, [])

    def test_target_change_to_original_binding_never_sends(self):
        oid = self.queue()
        with self.store.transaction():
            self.store.execute("UPDATE outbox SET binding_id=? WHERE id=?", (self.ack["binding_id"], oid))
        flow = self.flow()
        self.assertEqual(flow.dispatch(oid), "STALE")
        self.assertEqual(flow.desktop.receipts(), [])

    def test_business_source_text_tamper_rejected(self):
        with self.store.transaction():
            self.store.execute("UPDATE messages SET raw_text='tampered' WHERE id=?", (self.ack["message_id"],))
        with self.assertRaises(ValueError):
            self.queue()

    def test_cli_queue_and_list_never_start_desktop(self):
        from helpdesk.collector_test_ack_cli import main
        recipient = self.root / "recipient.json"
        recipient.write_text(json.dumps({"platform": self.recipient.platform, "stable_key": self.recipient.stable_key,
            "display_name": self.recipient.display_name, "verification_evidence": self.recipient.verification_evidence,
            "verified_at": self.recipient.verified_at.isoformat()}), encoding="utf-8")
        common = ["--database", self.store.path, "--collector-database", str(self.collector.path)]
        with patch("helpdesk.collector_test_ack_cli.MCPProcess") as mcp, redirect_stdout(StringIO()) as output:
            main(["queue", *common, "--source-ack", self.ack["id"], "--recipient", str(recipient), "--source-evidence", str(self.evidence)])
            queued = json.loads(output.getvalue())
            self.assertEqual(queued["observation_mode"], "FIXTURE")
            self.assertEqual(queued["state"], "QUEUED_NOT_SENT")
            output.truncate(0)
            output.seek(0)
            main(["list", *common])
            listed = json.loads(output.getvalue())
            self.assertEqual(listed[0]["simulated"], 1)
            mcp.assert_not_called()

    def test_cli_fixture_dispatch_rejects_before_desktop(self):
        from helpdesk.collector_test_ack_cli import main, reviewed_pin_for_ack
        oid = self.queue()
        with patch("helpdesk.collector_test_ack_cli.MCPProcess") as mcp:
            with self.assertRaises(ValueError):
                main(["dispatch", "--database", self.store.path, "--collector-database", str(self.collector.path),
                      "--outbox", oid, "--pin", str(self.root / "missing-pin.json")])
            mcp.assert_not_called()
        with self.assertRaises(ValueError):
            reviewed_pin_for_ack(self.store, oid, {})

    def test_untagged_payload_cannot_be_promoted_without_native_receipt(self):
        # A hand-entered ACTUAL option or evidence filename is not a signed SDK capture.
        with self.collector.connect() as db:
            db.execute("UPDATE messages SET raw_payload='{}'")
        with patch.dict("os.environ", {"WECOM_ARCHIVE_RUNTIME_MODE": "FIXTURE"}):
            with self.assertRaises(ValueError):
                queue_test_ack(self.store, self.collector, self.ack["id"], self.recipient,
                    source_evidence_path=self.evidence, observation_mode="ACTUAL")
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM collector_test_ack_copies")[0], 0)

    def test_processed_source_with_later_fingerprint_collision_is_held(self):
        with self.collector.connect() as db:
            db.execute("UPDATE messages SET fingerprint='collision-fingerprint'")
        utc, local = normalize_sent_time("2026-09-30T23:04:00+08:00")
        duplicate_observation = NormalizedMessage(source_type="wecom_archive", source_message_id="different-fixture",
            room_id="source-group", sender_id="student", fingerprint="collision-fingerprint", sent_at_utc=utc, sent_at_local=local)
        self.collector.persist_batch("fixture", SyncMode.LIVE, MessageBatch((duplicate_observation,), "2"), "1")
        with self.assertRaises(ValueError):
            self.queue()


if __name__ == "__main__":
    unittest.main()
