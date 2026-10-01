"""SOURCE HTTP/headless acceptance; all sources, receipts and databases are fixtures."""
from hashlib import sha256
from datetime import datetime, timezone
from http.client import HTTPConnection
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_storage import CollectorStore
from helpdesk.service import Helpdesk
from helpdesk.source_question_tasks import SourceQuestionTasks
from helpdesk.workflow import Workflow
from tests import test_demo_real_http as http_fixture
from tests import test_source_question_tasks as source_fixture


class SourceQuestionHTTPTests(unittest.TestCase):
    start = http_fixture.RealWorkbenchTests.start
    request = http_fixture.RealWorkbenchTests.request
    generate = http_fixture.RealWorkbenchTests.generate
    receive = source_fixture.SourceQuestionTasksTests.receive
    create = source_fixture.SourceQuestionTasksTests.create

    def setUp(self):
        http_fixture.RealWorkbenchTests.setUp(self)
        self.db = self.store
        self.raw = CollectorStore(self.base / 'fixture-source.db')
        self.binding = Helpdesk(self.store).bind('fixture-room', 'fixture-student', 'fixture student', verified=True)
        self.tasks = SourceQuestionTasks(self.store)
        self.ack = CollectorDispatcher(self.raw, self.store)
        self.resolver = CollectorDispatcher(self.raw, self.store, processing_mode='CASE_RESOLUTION')
        self.photo = self.base / 'fixture-original.png'
        Image.new('RGB', (32, 24), 'white').save(self.photo)
        self.message, self.receipt, self.outcome = self.receive('http-original', photo=self.photo)
        self.draft = self.create(self.receipt)
        self.stubs = [patch(name, return_value=self.bundle) for name in (
            'helpdesk.operator_tasks.verify_bundle', 'helpdesk.workflow.verify_bundle',
            'helpdesk.automatic_preparation.verify_bundle')]
        for stub in self.stubs:
            stub.start()
        flow = Workflow(self.store)
        flow.set_require_ack_before_generation(True)
        flow.set_require_source_clarity_review(True)
        self.config['auto_prepare_after_question_review'] = True
        self.actor = Mock(side_effect=AssertionError('No real desktop or generation actor allowed'))
        self.start(real_generator=self.actor, transport_factory=self.actor, desktop_factory=self.actor)

    def tearDown(self):
        try:
            http_fixture.RealWorkbenchTests.tearDown(self)
        finally:
            for stub in reversed(self.stubs):
                stub.stop()

    def review(self):
        return self.request('POST', '/api/operator-tasks', dict(action='review', draft_id=self.draft['id'],
            revision=self.draft['revision'], reviewer='Fixture source reviewer',
            source_evidence='Fixture original image and full transcription are clear'))

    def listed(self):
        code, data = self.request('GET', '/api/operator-tasks')
        self.assertEqual(code, 200)
        return data

    def original(self, data):
        return next(d for d in data['drafts'] if d['id'] == self.draft['id'])

    def persisted(self):
        tables = ('bindings', 'messages', 'cases', 'questions', 'question_versions', 'turns', 'outbox',
                  'runs', 'audit', 'reviews', 'answers', 'performance_units', 'delivery_checks',
                  'operator_drafts', 'operator_tasks')
        tables += tuple(r[0] for r in self.store.all("SELECT name FROM sqlite_master WHERE name LIKE 'reviewed_question_%'"))
        return {t: [dict(r) for r in self.store.all(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}

    def image_request(self, query):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request('GET', '/api/source-question-image?' + query)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_get_original_source_and_image_mime_hash_are_readonly(self):
        before = self.persisted()
        draft = self.original(self.listed())
        self.assertEqual(draft['label'], 'SOURCE_MESSAGE')
        self.assertTrue(draft['source_valid'])
        source = draft['original_source']
        self.assertEqual(source['group_name'], 'Fixture English group')
        self.assertEqual(source['student_display_name'], 'fixture student')
        self.assertEqual(source['student_sent_at'], self.store.one('SELECT source_sent_at FROM messages WHERE id=?',
            (self.outcome.message_id,))[0])
        self.assertEqual(datetime.fromisoformat(source['student_sent_at']).astimezone(timezone.utc),
                         datetime.fromisoformat('2026-09-30T22:58:00+08:00').astimezone(timezone.utc))
        self.assertEqual(source['image_count'], 1)
        code, headers, data = self.image_request(f"draft_id={self.draft['id']}&index=0")
        self.assertEqual(code, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(sha256(data).hexdigest(), sha256(self.photo.read_bytes()).hexdigest())
        self.listed()
        self.assertEqual(self.persisted(), before)
        self.actor.assert_not_called()

    def test_invalid_image_queries_and_changed_original_file_fail_closed(self):
        prefix = f"draft_id={self.draft['id']}"
        for query in (prefix, prefix + '&index=-1', prefix + '&index=1', prefix + '&index=bad',
                      prefix + '&index=0&index=0', prefix + '&index=0&path=C:/private',
                      'draft_id=unknown&index=0'):
            with self.subTest(query=query):
                self.assertEqual(self.image_request(query)[0], 404)
        self.photo.write_bytes(self.photo.read_bytes() + b'changed after intake')
        self.assertEqual(self.image_request(prefix + '&index=0')[0], 404)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.actor.assert_not_called()

    def test_one_confirmation_waits_ack_preserves_original_records_and_audit(self):
        tables = ('messages', 'outbox', 'cases', 'questions', 'question_versions', 'turns', 'bindings')
        before = {t: [dict(r) for r in self.store.all(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
        code, data = self.review()
        self.assertEqual(code, 200)
        task = data['result']
        self.assertEqual(task['label'], 'SOURCE_MESSAGE')
        self.assertEqual(task['binding_id'], self.binding)
        self.assertEqual(task['message_id'], self.outcome.message_id)
        self.assertEqual(task['case_id'], self.outcome.case_id)
        self.assertEqual(task['auto_queue']['phase'], 'WAITING_ACK')
        self.assertFalse(task['auto_queue']['source_review_required_again'])
        self.assertIsNone(task['run_id'])
        self.assertEqual(self.review()[1]['result']['id'], task['id'])
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")[0], 1)
        self.assertEqual({t: [dict(r) for r in self.store.all(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}, before)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM bindings WHERE group_key LIKE 'local-operator-test:%'")[0], 0)
        before = self.persisted()
        self.listed()
        self.listed()
        self.assertEqual(self.persisted(), before)
        self.actor.assert_not_called()

    def test_actual_fixture_ack_background_admits_without_second_post_or_desktop(self):
        task = self.review()[1]['result']
        before_runs = self.store.one('SELECT COUNT(*) FROM runs')[0]
        # Fixture receipt update represents an independently confirmed delivery;
        # no business or desktop method in this test sends it.
        self.store.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=0,sent_at='2026-09-30T23:06:00+08:00' WHERE purpose='ACK' AND message_id=?",
                           (self.outcome.message_id,))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = self.listed()
            current = next(t for t in data['tasks'] if t['id'] == task['id'])
            if current['auto_queue']['enqueued']:
                break
            time.sleep(.05)
        else:
            self.fail('Trusted background admission did not resume after the fixture ACK')
        self.assertEqual(current['auto_queue']['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], before_runs + 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.actor.assert_not_called()
        before = self.persisted()
        self.listed()
        self.assertEqual(self.persisted(), before)

    def test_headless_source_ui_has_original_image_one_gate_and_source_footer(self):
        try:
            from playwright.sync_api import sync_playwright, Error
        except ImportError:
            self.skipTest('Playwright unavailable; HTTP acceptance still runs')
        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Error as exc:
                if 'Executable doesn\'t exist' in str(exc) or 'browserType.launch' in str(exc) and 'install' in str(exc):
                    self.skipTest('Headless Chromium runtime unavailable')
                raise
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.origin)
                entry = page.locator('#operator-tasks details').filter(has_text='Fixture English group')
                entry.wait_for()
                entry.locator('summary').click()
                image = entry.get_by_alt_text('学生原题图片 1')
                image.wait_for(state='visible')
                image.scroll_into_view_if_needed()
                page.wait_for_function('(img) => img.complete && img.naturalWidth > 0', arg=image.element_handle(), timeout=8000)
                self.assertEqual(entry.get_by_role('button').count(), 1)
                self.assertTrue(entry.get_by_role('button', name='确认题面清楚并排队').is_enabled())
                self.assertIn('沿用学生原消息，草稿尚未交付', entry.inner_text())
                self.assertNotIn('本机测试，不计绩效', entry.inner_text())
                page.locator('#operator-reviewer').fill('Fixture source reviewer')
                page.locator('#operator-evidence').fill('Fixture original image and full transcription are clear')
                entry.get_by_role('button', name='确认题面清楚并排队').click()
                page.locator('#status').filter(has_text='无需再次审核题面').wait_for()
                entry = page.locator('#operator-tasks details').filter(has_text='Fixture English group')
                self.assertEqual(entry.get_by_role('button').count(), 0)
                self.assertIn('等待收到确认', entry.inner_text())
                self.store.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=0,sent_at='2026-09-30T23:06:00+08:00' WHERE purpose='ACK' AND message_id=?",
                                   (self.outcome.message_id,))
                entry.locator('summary').filter(has_text='等待 Luna').wait_for(timeout=8000)
                self.assertTrue(entry.evaluate('(node) => node.open'))
                refreshed_image = entry.get_by_alt_text('学生原题图片 1')
                self.assertTrue(refreshed_image.is_visible())
                page.wait_for_function('(img) => img.complete && img.naturalWidth > 0',
                                       arg=refreshed_image.element_handle(), timeout=8000)
                self.assertEqual(entry.get_by_role('button').count(), 0)
                self.assertFalse(errors, errors)
                self.actor.assert_not_called()
            finally:
                browser.close()

    def test_poll_connection_recovery_does_not_erase_uncertain_delivery_warning(self):
        from playwright.sync_api import sync_playwright
        before = self.persisted()
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.add_init_script('window.setInterval = () => 0;')
                failed = [False]
                posts = []
                page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
                page.route('**/api/state', lambda route: route.abort() if failed[0] else route.continue_())
                page.goto(self.origin)
                page.wait_for_function("() => document.querySelector('#status').textContent.includes('就绪')")
                failed[0] = True
                page.evaluate('() => pollBoard()')
                self.assertIn('正在自动重试', page.locator('#status').inner_text())
                self.assertTrue(page.locator('#status').evaluate("node => node.classList.contains('error')"))
                failed[0] = False
                page.evaluate('() => pollBoard()')
                self.assertIn('连接已恢复', page.locator('#status').inner_text())
                self.assertFalse(page.locator('#status').evaluate("node => node.classList.contains('error')"))
                warning = '发送结果不确定，请核验'
                page.evaluate('(message) => showStatus(message, true)', warning)
                for failed[0] in (False, True, False):
                    page.evaluate('() => pollBoard()')
                    self.assertEqual(page.locator('#status').inner_text(), warning)
                    self.assertTrue(page.locator('#status').evaluate("node => node.classList.contains('error')"))
                self.assertEqual(posts, [])
                self.assertEqual(self.persisted(), before)
                self.actor.assert_not_called()
            finally:
                browser.close()

    def test_source_without_current_evidence_stays_disabled_after_page_refresh(self):
        try:
            from playwright.sync_api import sync_playwright, Error
        except ImportError:
            self.skipTest('Playwright unavailable; source validation is tested separately')
        self.photo.write_bytes(self.photo.read_bytes() + b'changed after intake')
        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest('Headless Chromium runtime unavailable')
                raise
            try:
                page = browser.new_page()
                page.goto(self.origin)
                entry = page.locator('#operator-tasks details').filter(has_text='第12题')
                entry.wait_for()
                entry.locator('summary').click()
                button = entry.get_by_role('button', name='确认题面清楚并排队')
                self.assertTrue(button.is_disabled())
                with page.expect_response(lambda response: response.url.endswith('/api/state')):
                    pass
                self.assertTrue(button.is_disabled())
                self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")[0], 0)
                self.actor.assert_not_called()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
