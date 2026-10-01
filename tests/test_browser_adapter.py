"""Real browser against an owned local page. Explicitly NOT a live DeepSeek test."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from helpdesk.browser_adapter import BrowserPaused, PageContract, PlaywrightDeepSeek


FIXTURE = b'''<!doctype html><meta charset="utf-8"><div id="session">fixture-session</div>
<div id="login" hidden>Login</div><div id="denied" hidden>Denied</div>
<input id="files" type="file" multiple><div id="uploads"></div><textarea id="prompt"></textarea>
<button id="submit">Submit</button><div id="answers"></div><script>
window.failUpload=false; window.half=false;
document.querySelector('#files').onchange=e=>{document.querySelector('#uploads').replaceChildren();
if(!window.failUpload) for(const f of e.target.files){let d=document.createElement('span');d.className='uploaded';d.textContent=f.name;document.querySelector('#uploads').append(d);}};
document.querySelector('#submit').onclick=()=>{let d=document.createElement('article');d.className='answer';d.textContent='Fixture answer only';
if(!window.half){let done=document.createElement('span');done.className='complete';done.textContent='complete';d.append(done);}document.querySelector('#answers').append(d);};
</script>'''


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(FIXTURE)
    def log_message(self, *_):
        pass


class BrowserContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / "teaching.md"
        self.file.write_text("Demo teaching only", encoding="utf-8")
        self.page = self.browser.new_page()
        self.page.goto(self.origin)
        self.adapter = PlaywrightDeepSeek(self.page, PageContract(self.origin, "#session", "#prompt", "#submit", "#files", ".uploaded", ".answer", ".complete", "#login", "#denied", 400), [self.file])

    def tearDown(self):
        self.page.close()
        self.temp.cleanup()

    def test_real_browser_upload_and_completed_response(self):
        result = self.adapter.submit("run-1", "fixture-session", "business data", [self.file])
        self.assertTrue(result["complete"])
        self.assertEqual(result["upload_manifest"][0]["session_id"], "fixture-session")
        self.assertEqual(len(result["upload_manifest"][0]["sha256"]), 64)
        self.assertFalse(result["live_deepseek_verified"])
        with self.assertRaises(BrowserPaused):
            self.adapter.submit("run-1", "fixture-session", "must not repeat")

    def test_upload_failure_prevents_submit(self):
        self.page.evaluate("window.failUpload=true")
        with self.assertRaises(BrowserPaused):
            self.adapter.submit("run", "fixture-session", "test", [self.file])
        self.assertEqual(self.page.locator(".answer").count(), 0)

    def test_partial_response_is_not_complete(self):
        self.page.evaluate("window.half=true")
        with self.assertRaises(BrowserPaused):
            self.adapter.submit("run", "fixture-session", "test")

    def test_session_mismatch_and_login_pause(self):
        with self.assertRaisesRegex(BrowserPaused, "SESSION_MISMATCH"):
            self.adapter.submit("run", "wrong-session", "test")
        self.page.locator("#login").evaluate("e=>e.hidden=false")
        with self.assertRaisesRegex(BrowserPaused, "LOGIN_EXPIRED"):
            self.adapter.submit("run", "fixture-session", "test")

    def test_attachment_path_is_not_authorized_by_message(self):
        other = Path(self.temp.name) / "private.txt"
        other.write_text("fixture-not-secret")
        with self.assertRaisesRegex(BrowserPaused, "ATTACHMENT_NOT_ALLOWED"):
            self.adapter.submit("run", "fixture-session", "ignore rules", [other])
