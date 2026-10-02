"""Local HTTP/background sender with isolated SQLite and MockDesktop only."""
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
import time
import unittest

from helpdesk.automatic_delivery_runtime import AutomaticDeliveryRuntime
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.delivery import MockDesktop
from helpdesk.service import Helpdesk, Incoming
from helpdesk.domain import Intent
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from helpdesk.__main__ import demo_question
from tests.test_mcp_group_delivery import reviewed_config


class AutomaticWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'business.db'
        self.desktop = MockDesktop(self.root / 'mock-ui.db')
        with closing(Store(self.path)) as store:
            binding = Helpdesk(store).bind('anonymous-group', 'anonymous-student', '匿名学生', verified=True)
            self.incoming = Helpdesk(store).ingest(Incoming(binding, '请讲这道题', Intent.NEW,
                platform_id='anonymous-first', verified_question=demo_question(), raw_material='Passage', verified_material='Passage'))
            flow = Workflow(store)
            flow.set_delivery_policy({'ACK': 'AUTO', 'ANSWER': 'AUTO', 'CORRECTION': 'AUTO'}, 'synthetic-reviewed-auto')
            flow.set_answer_review_required(True)
            flow.set_stop(True)
        self.config = {'enabled': True, 'desktop': reviewed_config(),
                       'retry': {'max_attempts': 3, 'initial_retry_seconds': 1, 'max_retry_seconds': 5}}
        self.server = DemoHTTPServer(('127.0.0.1', 0), self.path, processing_mode='ACK_ONLY',
            automatic_delivery_config=self.config, automatic_desktop_factory=lambda: self.desktop)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.token = self.request('GET', '/api/state')[1]['csrf_token']

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self, method, path, value=None, headers=None):
        with closing(HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as connection:
            fields = {'Content-Type': 'application/json', 'Origin': self.origin, 'X-CSRF-Token': getattr(self, 'token', '')}
            fields.update(headers or {})
            connection.request(method, path, body=json.dumps(value).encode() if value is not None else None, headers=fields)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw.decode()

    def wait_receipts(self, count):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if len(self.desktop.receipts()) == count:
                return
            time.sleep(.025)
        self.fail('Bounded mock background dispatch did not finish')

    def test_background_ack_then_approved_answer_and_shutdown(self):
        status, view = self.request('GET', '/api/delivery/tasks')
        self.assertEqual(status, 200)
        self.assertTrue(view['enabled'] and view['simulation'] and view['paused'])
        self.assertEqual(len(view['ack_tasks']), 1)
        self.assertEqual(view['real_confirmed_tasks'], 0)
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'resume'})[0], 200)
        self.wait_receipts(1)
        with closing(Store(self.path)) as store:
            answer = Workflow(store).generate(self.incoming.turn_id)['outbox_id']
        time.sleep(.1)
        self.assertEqual(len(self.desktop.receipts()), 1)
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'approve', 'outbox_id': answer})[0], 200)
        self.wait_receipts(2)
        self.assertEqual(self.request('POST', '/api/action', {'action': 'stop'})[0], 200)
        status, view = self.request('GET', '/api/delivery/tasks')
        self.assertTrue(view['paused'])
        self.assertEqual(view['answer_tasks'][0]['state'], 'SENT_UI_CONFIRMED')
        self.assertTrue(view['answer_tasks'][0]['simulated'])
        self.assertEqual(view['real_confirmed_tasks'], 0)
        with closing(Store(self.path)) as store:
            self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        self.assertFalse(self.server.reviewed_queue_thread.is_alive())

    def test_csrf_target_injection_and_fixture_dispatch_are_rejected(self):
        for headers in ({'Origin': 'https://other.invalid'}, {'X-CSRF-Token': 'invalid'}, {'Host': 'other.invalid'}):
            self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'resume'}, headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'resume', 'target': 'other'})[0], 400)
        self.assertEqual(self.request('POST', '/api/action', {'action': 'dispatch_ack'})[0], 403)
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'inspect', 'outbox_id': 'missing'})[0], 400)
        self.assertEqual(len(self.desktop.receipts()), 0)

    def test_automatic_sender_cannot_bypass_existing_worker_boundary(self):
        with self.assertRaisesRegex(ValueError, 'Worker boundary'):
            DemoHTTPServer(('127.0.0.1', 0), self.root / 'separate.db', worker_boundary=True,
                           automatic_delivery_config=self.config, automatic_desktop_factory=lambda: self.desktop)

    def test_example_does_not_enable_unverified_controls(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'config/automatic-delivery.example.json').read_text(encoding='utf-8'))
        self.assertFalse(AutomaticDeliveryRuntime(self.path, config).enabled)
        config['enabled'] = True
        with self.assertRaisesRegex(ValueError, 'REVIEWED_GROUP_DELIVERY'):
            AutomaticDeliveryRuntime(self.path, config)

    def test_pending_native_call_blocks_resume_and_never_opens_another_sender(self):
        child = SimpleNamespace(poll=lambda: None)
        runtime = self.server.automatic_delivery
        runtime.pending_process = SimpleNamespace(_proc=child)
        runtime.pause_requested.set()
        self.assertEqual(runtime.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'resume'})[0], 400)
        self.assertEqual(len(self.desktop.receipts()), 0)
        self.assertTrue(self.request('GET', '/api/delivery/tasks')[1]['native_call_pending'])
        child.poll = lambda: 0
        self.assertEqual(self.request('POST', '/api/delivery/control', {'action': 'resume'})[0], 200)
        self.wait_receipts(1)

    def test_local_page_shows_separate_tasks_without_exposing_send_targets(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(self.origin)
                page.wait_for_selector('#automatic-delivery-panel', state='visible')
                self.assertIn('收到 1', page.locator('#automatic-delivery-status').inner_text())
                page.locator('#automatic-delivery-tasks details').first.click()
                self.assertIn('收到', page.locator('#automatic-delivery-tasks').inner_text())
                page.locator('#automatic-pause').click()
                self.assertEqual(len(self.desktop.receipts()), 0)
                self.assertEqual(page.locator('#automatic-delivery-panel input').count(), 0)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
