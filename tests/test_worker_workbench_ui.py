"""Anonymous, isolated browser checks for the optional Worker control panel."""
import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from playwright.sync_api import sync_playwright
from helpdesk.demo_server import DemoHTTPServer


SCRIPT = Path(__file__).resolve().parents[1] / "helpdesk" / "static" / "worker-control.js"
ORIGIN = "http://127.0.0.1:49151"


class WorkerWorkbenchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.posts = []
        self.gets = 0
        self.fail_get = False
        self.fail_post = False
        self.post_result = {}
        self.data = {
            "csrf_token": "test-csrf", "mode": "OBSERVE_ONLY",
            "workers": [{"worker_id": "worker-1", "account_id": "account-1",
                         "health": {"state": "HEALTHY", "connected": True,
                                    "interactive_desktop": True, "desktop_unlocked": True,
                                    "native_call_pending": False}, "gui_ready": True}],
            "accounts": [{"account_id": "account-1", "stop": False,
                          "takeover": "AUTO_ACTIVE", "takeover_epoch": 0,
                          "pause_reason": None, "quarantined": False,
                          "active_worker": "worker-1",
                          "available_actions": ["stop", "request_takeover"]}],
            "commands": [],
        }

        def route_request(route):
            request = route.request
            if request.url == ORIGIN + "/":
                route.fulfill(status=200, content_type="text/html",
                              body="<!doctype html><html lang='zh-CN'><body><main><button id='refresh'>刷新看板</button></main></body></html>")
            elif request.url == ORIGIN + "/api/worker/control":
                if request.method == "POST":
                    self.posts.append({"headers": request.headers,
                                       "body": json.loads(request.post_data)})
                    if self.fail_post:
                        route.fulfill(status=409, content_type="application/json",
                                      body=json.dumps({"error": "状态尚未核验，请人工处理"}))
                    else:
                        route.fulfill(status=200, content_type="application/json",
                                      body=json.dumps({"result": self.post_result}))
                else:
                    self.gets += 1
                    if self.fail_get:
                        route.fulfill(status=503, content_type="application/json", body=json.dumps({'error': '运行状态读取失败'}))
                        return
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(self.data))
            else:
                route.fulfill(status=404, body="")

        self.page.route("**/*", route_request)

    def tearDown(self):
        self.context.close()

    def mount(self):
        self.page.goto(ORIGIN + "/")
        self.page.add_script_tag(path=str(SCRIPT))
        self.page.get_by_text("Worker 状态已更新。").wait_for(state='attached')

    def test_unused_panel_is_hidden_and_manual_refresh_can_reveal_new_binding(self):
        configured = self.data
        self.data = {**configured, 'workers': [], 'accounts': [], 'commands': []}
        self.mount()
        self.assertTrue(self.page.locator('#worker-control-panel').is_hidden())
        self.assertEqual(self.gets, 1)
        self.assertEqual(self.posts, [])
        self.data = configured
        self.page.locator('#refresh').click()
        self.page.get_by_text('账号 account-1', exact=True).wait_for()
        self.assertTrue(self.page.locator('#worker-control-panel').is_visible())
        self.assertEqual(self.gets, 2)
        self.assertEqual(self.posts, [])

    def test_unknown_command_remains_visible_without_a_connected_worker(self):
        self.data.update(workers=[], accounts=[], commands=[{
            'command_id': 'unresolved-1', 'status': 'OUTCOME_UNKNOWN',
            'available_actions': ['verify'], 'evidence_refs': []}])
        self.mount()
        panel = self.page.locator('#worker-control-panel')
        self.assertTrue(panel.is_visible())
        self.assertIn('OUTCOME_UNKNOWN', panel.inner_text())
        self.assertTrue(self.page.get_by_role('button', name='核验发送结果').is_enabled())
        self.assertEqual(self.posts, [])

    def test_unreadable_worker_state_is_visible_instead_of_hidden(self):
        self.fail_get = True
        self.page.goto(ORIGIN + '/')
        self.page.add_script_tag(path=str(SCRIPT))
        self.page.get_by_text('运行状态读取失败', exact=True).wait_for()
        self.assertTrue(self.page.locator('#worker-control-panel').is_visible())
        self.assertEqual(self.posts, [])

    def test_default_observe_only_and_no_phantom_listener(self):
        self.data["accounts"] = []
        self.mount()
        panel = self.page.locator("#worker-control-panel")
        self.assertIn("仅观察", panel.inner_text())
        self.assertIn("尚未接入 Worker 账号", panel.inner_text())
        self.assertIn("暂无 Worker 命令", panel.inner_text())
        self.assertEqual(self.posts, [])

    def test_takeover_phases_and_unknown_native_call_disable_confirmation(self):
        self.data["accounts"][0].update(takeover="QUIESCING",
                                         available_actions=["confirm_takeover"])
        self.data["workers"][0]["health"]["native_call_pending"] = True
        self.mount()
        confirm = self.page.get_by_role("button", name="确认已停止，开始接管")
        self.assertTrue(confirm.is_disabled())
        self.assertIn("尚未授予人工操作权", self.page.locator("#worker-control-panel").inner_text())
        self.data["workers"][0]["health"]["native_call_pending"] = False
        self.data["accounts"][0]["quarantined"] = True
        self.page.get_by_role("button", name="刷新 Worker 状态").click()
        self.assertTrue(confirm.is_disabled())
        self.data["accounts"][0]["quarantined"] = False
        self.data["workers"][0]["gui_ready"] = False
        self.data["workers"][0]["health"]["desktop_unlocked"] = False
        self.page.get_by_role("button", name="刷新 Worker 状态").click()
        self.page.get_by_text("桌面：不可用，等待人工检查").wait_for()
        self.assertTrue(confirm.is_disabled())
        self.assertIn("桌面：不可用，等待人工检查", self.page.locator("#worker-control-panel").inner_text())
        self.assertEqual(self.posts, [])

    def test_verification_page_can_be_taken_over_when_desktop_is_interactive(self):
        self.data["accounts"][0].update(takeover="QUIESCING",
                                         available_actions=["confirm_takeover"])
        self.data["workers"][0]["health"]["state"] = "VERIFICATION_REQUIRED"
        self.data["workers"][0]["gui_ready"] = False
        self.mount()
        confirm = self.page.get_by_role("button", name="确认已停止，开始接管")
        self.assertTrue(confirm.is_enabled())
        confirm.click()
        self.assertEqual(self.posts[0]["body"],
                         {"action": "confirm_takeover", "account_id": "account-1"})

    def test_resume_check_is_distinct_and_post_uses_csrf_without_retry(self):
        self.data["accounts"][0].update(takeover="OWNED",
                                         available_actions=["request_resume"])
        self.fail_post = True
        self.mount()
        self.page.get_by_role("button", name="发起恢复前检查").click()
        self.page.get_by_text("状态尚未核验，请人工处理").wait_for()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]["body"],
                         {"action": "request_resume", "account_id": "account-1"})
        self.assertEqual(self.posts[0]["headers"]["x-csrf-token"], "test-csrf")
        self.data["accounts"][0].update(takeover="RESUME_CHECK",
                                         available_actions=[])
        self.page.get_by_role("button", name="刷新 Worker 状态").click()
        self.page.get_by_text("接管阶段：恢复前检查").wait_for()
        self.assertIn("当前仅做只读健康检查", self.page.locator("#worker-control-panel").inner_text())
        self.assertTrue(self.page.get_by_role("button", name="恢复自动化").is_disabled())
        self.assertEqual(len(self.posts), 1)

    def test_manual_evidence_must_be_server_listed_and_text_is_not_html(self):
        self.data["commands"] = [{"command_id": "cmd-1", "task_id": "task-1",
            "account_id": "account-1", "action": "VERIFY_OUTBOX",
            "status": "OUTCOME_UNKNOWN", "reason_code": "<img src=x onerror=alert(1)>",
            "available_actions": ["verify", "mark_manually_handled"],
            "evidence_refs": ["evidence.1", "../private.txt"]}]
        self.mount()
        panel = self.page.locator("#worker-control-panel")
        self.assertEqual(panel.locator("img").count(), 0)
        self.assertTrue(self.page.get_by_role("button", name="标记人工已处理").is_disabled())
        self.assertEqual(self.page.locator("select option").count(), 2)
        self.page.locator("select").select_option("evidence.1")
        self.page.get_by_role("button", name="标记人工已处理").click()
        self.assertEqual(self.posts[0]["body"], {"action": "mark_manually_handled",
            "command_id": "cmd-1", "evidence_ref": "evidence.1"})

    def test_command_actions_use_fixed_server_payload(self):
        self.data["workers"][0]["health"] = {"state": "DESKTOP_UNAVAILABLE"}
        self.post_result = {"status": "WAITING_HUMAN", "verification_required": True}
        self.data["commands"] = [{"command_id": "cmd-2", "task_id": "task-2",
            "account_id": "account-1", "action": "VERIFY_OUTBOX",
            "status": "OUTCOME_UNKNOWN", "available_actions": ["verify", "cancel"]}]
        self.mount()
        self.assertIn("Worker 健康：桌面不可用", self.page.locator("#worker-control-panel").inner_text())
        self.page.get_by_role("button", name="核验发送结果").click()
        self.assertEqual(self.posts[0]["body"], {"action": "verify", "command_id": "cmd-2"})
        self.page.get_by_text("仍待可信的只读核验；未确认发送成功，也不会自动重试。").wait_for()


class WorkerControlAnonymousHTTPTests(unittest.TestCase):
    def test_static_panel_and_fixed_api_reject_unbound_command(self):
        with tempfile.TemporaryDirectory(prefix="worker-ui-http-") as directory:
            server = DemoHTTPServer(("127.0.0.1", 0), Path(directory) / "business.db",
                                    worker_boundary=True)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"

                def request(method, path, *, payload=None, headers=None):
                    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    client.request(method, path, body=None if payload is None else json.dumps(payload),
                                   headers=headers or {})
                    response = client.getresponse()
                    raw = response.read()
                    status = response.status
                    client.close()
                    return status, raw

                status, script = request("GET", "/worker-control.js")
                self.assertEqual(status, 200)
                self.assertIn(b"worker-control-panel", script)
                status, raw = request("GET", "/api/worker/control")
                self.assertEqual(status, 200)
                snapshot = json.loads(raw)
                self.assertEqual(snapshot["mode"], "OBSERVE_ONLY")
                self.assertEqual(snapshot["accounts"], [])
                self.assertEqual(snapshot["commands"], [])
                payload = {"action": "verify", "command_id": "anonymous-command"}
                headers = {"Content-Type": "application/json", "Origin": origin,
                           "X-CSRF-Token": snapshot["csrf_token"]}
                status, raw = request("POST", "/api/worker/control", payload=payload, headers=headers)
                self.assertEqual(status, 409)
                self.assertIn("不自动重试", json.loads(raw)["error"])
                headers["X-CSRF-Token"] = "wrong"
                status, _ = request("POST", "/api/worker/control", payload=payload, headers=headers)
                self.assertEqual(status, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
