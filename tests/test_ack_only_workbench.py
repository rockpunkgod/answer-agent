"""Workbench reports persisted collector truth, with all teaching writes disabled."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.message_sources import NormalizedMessage, MessageBatch, SyncMode, normalize_sent_time
from helpdesk.service import Helpdesk
from helpdesk.storage import Store


class AckOnlyWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.message_path = self.root / "messages.db"
        self.business_path = self.root / "business.db"
        config = self.root / "collector.toml"
        config.write_text("[collector]\ndatabase=" + json.dumps(self.message_path.as_posix()) +
            "\nbusiness_database=" + json.dumps(self.business_path.as_posix()) +
            '\nprocessing_mode="ACK_ONLY"\n[source]\nname="wecom_archive"\nfactory="helpdesk.wecom_archive:create_source"\n[archive]\nenabled=false\n', encoding="utf-8")
        self.server = DemoHTTPServer(("127.0.0.1", 0), self.root / "unused-demo.db",
            processing_mode="ACK_ONLY", collector_config=config)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.token = self.request("GET", "/api/state")[1]["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method, path, payload=None):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        if hasattr(self, "token"):
            headers["X-CSRF-Token"] = self.token
        client.request(method, path, None if payload is None else json.dumps(payload), headers)
        response = client.getresponse()
        raw = response.read()
        result = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw.decode()
        client.close()
        return response.status, result

    def test_never_synced_unconfigured_and_no_listener_claim(self):
        code, data = self.request("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(data["mode"], "ACK_ONLY")
        self.assertEqual(data["collector"]["metrics"]["health"], "NEVER_SYNCED")
        self.assertEqual(data["collector"]["account_status"], "NOT_CONFIGURED")
        self.assertFalse(data["collector"]["listener_started_by_workbench"])
        self.assertFalse(data["collector"]["student_send_enabled"])
        self.assertFalse(data["collector"]["teaching_enabled"])
        self.assertEqual(data["collector"]["pending_ack_count"], 0)
        self.assertEqual(data["allowed_actions"], ["collector_start", "collector_stop", "resume", "stop"])
        self.assertEqual(self.server.db_path, self.business_path)
        self.assertFalse(self.message_path.exists())

    def test_native_import_worker_is_not_claimed_as_live_desktop_listener(self):
        from types import SimpleNamespace
        original = self.server.collector_supervisor
        self.server.collector_config["source"]["kind"] = "native_clipboard_archive"
        self.server.collector_supervisor = SimpleNamespace(snapshot=lambda: {
            "state": "RUNNING", "running": True, "worker_alive": True})
        try:
            _, data = self.request("GET", "/api/state")
            collector = data["collector"]
            self.assertEqual(collector["collection_kind"], "NATIVE_CLIPBOARD_IMPORT")
            self.assertEqual(collector["account_status"], "NOT_APPLICABLE")
            self.assertTrue(collector["source_polling_started_by_workbench"])
            self.assertFalse(collector["listener_started_by_workbench"])
            self.assertFalse(collector["desktop_listener_live_verified"])
            self.assertFalse(collector["student_send_enabled"])
        finally:
            self.server.collector_supervisor = original

    def test_pending_ack_and_success_metrics_come_from_persisted_databases(self):
        collector = CollectorStore(self.message_path)
        business = Store(self.business_path)
        try:
            Helpdesk(business).bind("room", "student", "学生", verified=True)
            utc, local = normalize_sent_time("2026-09-30T23:00:00+08:00")
            msg = NormalizedMessage(source_type="wecom_archive", source_message_id="real-shaped-fixture", room_id="room", sender_id="student",
                normalized_text="请问这道题怎么做？", sent_at_utc=utc, sent_at_local=local)
            collector.persist_batch("wecom_archive", SyncMode.LIVE, MessageBatch((msg,), "1"), None)
            CollectorDispatcher(collector, business).drain()
        finally:
            business.close()
        _, data = self.request("GET", "/api/state")
        self.assertEqual(data["collector"]["metrics"]["health"], "HEALTHY")
        self.assertEqual(data["collector"]["metrics"]["current_cursor"], "1")
        self.assertEqual(data["collector"]["pending_ack_count"], 1)
        self.assertEqual([(row["purpose"], row["body"], row["state"]) for row in data["dashboard"]["outbox"]], [("ACK", "收到", "PENDING")])
        self.assertFalse(data["test_delivery_available"])
        self.assertFalse(data["collector"]["account_live_verified"])

    def test_all_teaching_and_send_actions_blocked_before_any_side_effect(self):
        for action in ("generate", "approve", "dispatch", "dispatch_ack", "real_generate", "real_approve", "real_dispatch_test", "new_alice", "recover"):
            code, result = self.request("POST", "/api/action", {"action": action})
            self.assertEqual(code, 403, action)
            self.assertEqual(result["processing_mode"], "ACK_ONLY")
        for action in ("freeze", "generate", "review", "create"):
            code, _ = self.request("POST", "/api/operator-tasks", {"action": action, "task_id": "anything"})
            self.assertEqual(code, 403, action)
        business = Store(self.business_path)
        try:
            for table in ("messages", "cases", "questions", "runs", "answers", "outbox"):
                self.assertEqual(business.one("SELECT COUNT(*) FROM " + table)[0], 0)
        finally:
            business.close()
        self.assertEqual(self.server.job_snapshot(), {})
        with self.assertRaises(ValueError):
            self.server.start_operator_generation("anything")
        with self.assertRaises(ValueError):
            self.server.start_real_job("real_generate")

    def test_frontend_has_ack_status_and_disables_teaching_entries(self):
        _, html = self.request("GET", "/")
        _, script = self.request("GET", "/app.js")
        self.assertIn('id="collector-panel"', html)
        self.assertIn("当前阶段配置", html)
        self.assertIn("data-teaching-entry", html)
        self.assertIn('ackOnly=data.processing_mode==="ACK_ONLY"', script)
        self.assertIn("缺少企业存档账号配置", script)
        self.assertIn("当前使用本机原生复制记录，无需存档账号", script)
        self.assertIn("NEVER_SYNCED", script)
        self.assertIn("node.hidden=ackOnly", script)


if __name__ == "__main__":
    unittest.main()
