"""Controlled loop and HTTP actions use real local persistence, mock sources only."""
from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from helpdesk.collector_supervisor import CollectorSupervisor
from helpdesk.collector_storage import CollectorStore
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.message_sources import NormalizedMessage, MessageBatch, SyncMode, normalize_sent_time
from helpdesk.service import Helpdesk
from helpdesk.storage import Store
from helpdesk.wecom_archive import ArchiveNotConfigured


class ControlledSource:
    source_name = "fixture"
    def __init__(self, message=None):
        self.message = message
        self.calls = []
        self.closed = False
    def fetch_page(self, cursor, mode, page_size):
        self.calls.append((cursor, mode))
        return MessageBatch((self.message,), "1") if cursor is None and self.message else MessageBatch((), cursor)
    def close(self):
        self.closed = True


class CollectorSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {"collector": {"database": str(self.root / "messages.db"), "business_database": str(self.root / "business.db"),
            "processing_mode": "ACK_ONLY", "poll_seconds": 1}, "source": {"name": "fixture"}}
        self.supervisors = []

    def tearDown(self):
        for supervisor in self.supervisors:
            supervisor.stop(wait_seconds=3)
        self.tmp.cleanup()

    def supervisor(self, factory, config=None):
        supervisor = CollectorSupervisor(self.config if config is None else config, source_factory=factory)
        self.supervisors.append(supervisor)
        return supervisor

    def wait_state(self, supervisor, expected):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            status = supervisor.snapshot()
            if status["state"] == expected:
                return status
            time.sleep(.01)
        self.fail("Supervisor did not reach " + expected + ": " + str(supervisor.snapshot()))

    def test_missing_authorization_blocks_without_fake_running_or_secret_leak(self):
        def factory(config):
            raise ArchiveNotConfigured("SECRET TOKEN MUST NOT LEAK")
        supervisor = self.supervisor(factory)
        result = supervisor.start()
        self.assertEqual((result["state"], result["health"]), ("BLOCKED", "NEEDS_ADMIN_CONFIGURATION"))
        self.assertFalse(result["worker_alive"])
        self.assertFalse(result["running"])
        self.assertNotIn("SECRET TOKEN", json.dumps(result))
        self.assertFalse(Path(self.config["collector"]["database"]).exists())

    def test_start_sync_ack_stop_restart_keeps_activation_cursor_and_one_ack(self):
        path = self.config["collector"]["business_database"]
        business = Store(path)
        try:
            Helpdesk(business).bind("room", "student", "学生", verified=True)
        finally:
            business.close()
        utc, local = normalize_sent_time((datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        message = NormalizedMessage(source_type="wecom_archive", source_message_id="new-question", room_id="room", sender_id="student",
            normalized_text="请问这道题怎么做？", sent_at_utc=utc, sent_at_local=local)
        sources = []
        def factory(config):
            source = ControlledSource(message)
            sources.append(source)
            return source
        supervisor = self.supervisor(factory)
        supervisor.start()
        first = self.wait_state(supervisor, "RUNNING")
        self.assertEqual(first["last_result"]["pending_ack_count"], 1)
        self.assertFalse(supervisor.start()["resubmitted"])
        self.assertEqual(len(sources), 1)
        supervisor.stop(wait_seconds=3)
        self.assertTrue(sources[0].closed)
        store = CollectorStore(self.config["collector"]["database"])
        activation = store.get_live_activation("fixture")
        self.assertEqual(store.get_sync_state("fixture")["cursor"], "1")
        supervisor.start()
        self.wait_state(supervisor, "RUNNING")
        supervisor.stop(wait_seconds=3)
        self.assertEqual(store.get_live_activation("fixture"), activation)
        self.assertEqual(sources[1].calls[0], ("1", SyncMode.LIVE))
        business = Store(path)
        try:
            self.assertEqual([tuple(row) for row in business.all("SELECT purpose,body,state FROM outbox")], [("ACK", "收到", "PENDING")])
            for table in ("cases", "questions", "runs", "answers"):
                self.assertEqual(business.one("SELECT COUNT(*) FROM " + table)[0], 0)
        finally:
            business.close()

    def test_only_explicit_ack_mode_can_start_and_stop_waits_current_call(self):
        called = []
        config = {"collector": {"processing_mode": "CASE_RESOLUTION"}}
        supervisor = self.supervisor(lambda config: called.append(True), config)
        self.assertEqual(supervisor.start()["state"], "BLOCKED")
        self.assertEqual(called, [])
        entered, release = Event(), Event()
        class Slow(ControlledSource):
            def fetch_page(self, cursor, mode, page_size):
                entered.set()
                release.wait(3)
                return super().fetch_page(cursor, mode, page_size)
        source = Slow()
        supervisor = self.supervisor(lambda config: source)
        supervisor.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(supervisor.snapshot()["state"], "STARTING")
        self.assertFalse(supervisor.snapshot()["running"])
        self.assertEqual(supervisor.stop(wait_seconds=0)["state"], "STOPPING")
        self.assertFalse(source.closed)
        release.set()
        self.wait_state(supervisor, "STOPPED")
        self.assertTrue(source.closed)

    def test_workbench_fixed_actions_no_client_config_or_backfill(self):
        config_path = self.root / "collector.toml"
        config_path.write_text('[collector]\nprocessing_mode="ACK_ONLY"\ndatabase=' + json.dumps(self.config["collector"]["database"].replace('\\', '/')) +
            '\nbusiness_database=' + json.dumps(self.config["collector"]["business_database"].replace('\\', '/')) + '\npoll_seconds=1\n', encoding="utf-8")
        factory_checked = Event()
        def factory(config):
            factory_checked.set()
            raise ArchiveNotConfigured("private credential")
        server = DemoHTTPServer(("127.0.0.1", 0), self.root / "unused.db", collector_config=config_path,
            processing_mode="ACK_ONLY", collector_source_factory=factory)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(payload=None):
                client = HTTPConnection("127.0.0.1", server.server_port)
                headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:" + str(server.server_port), "X-CSRF-Token": server.csrf_token}
                client.request("GET" if payload is None else "POST", "/api/state" if payload is None else "/api/action", None if payload is None else json.dumps(payload), headers)
                response = client.getresponse()
                result = response.status, json.loads(response.read())
                client.close()
                return result
            code, result = request({"action": "collector_start"})
            self.assertEqual(code, 409)
            self.assertEqual(result["result"]["state"], "BLOCKED")
            self.assertFalse(result["result"]["running"])
            self.assertFalse(result["result"]["worker_alive"])
            self.assertTrue(factory_checked.is_set())
            self.assertFalse(Path(self.config["collector"]["database"]).exists())
            self.assertNotIn("private credential", json.dumps(result))
            for extra in ({"config": "other.toml"}, {"command": "arbitrary"}, {"mode": "BACKFILL"}):
                self.assertEqual(request({"action": "collector_start", **extra})[0], 400)
            self.assertFalse(request()[1]["collector"]["listener_started_by_workbench"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_stop_during_first_page_commits_only_that_page_without_second_fetch(self):
        entered, release = Event(), Event()
        utc, local = normalize_sent_time((datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        message = NormalizedMessage(source_type="wecom_archive", source_message_id="first-page", room_id="room", sender_id="student", sent_at_utc=utc, sent_at_local=local)
        class Paged(ControlledSource):
            def fetch_page(self, cursor, mode, page_size):
                self.calls.append((cursor, mode))
                entered.set()
                release.wait(3)
                if cursor is None:
                    return MessageBatch((message,), "1", True)
                raise AssertionError("second page must not start after stop request")
        source = Paged()
        self.config["collector"]["max_pages"] = 1000
        supervisor = self.supervisor(lambda config: source)
        supervisor.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(supervisor.stop(wait_seconds=0)["state"], "STOPPING")
        release.set()
        self.wait_state(supervisor, "STOPPED")
        self.assertEqual(source.calls, [(None, SyncMode.LIVE)])
        store = CollectorStore(self.config["collector"]["database"])
        self.assertEqual(store.get_sync_state("fixture")["cursor"], "1")
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertIsNotNone(db.execute("SELECT processed_at FROM events").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
