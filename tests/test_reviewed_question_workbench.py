"""First source review queues work without implicitly opening a desktop."""
import json
import time
import unittest
from unittest.mock import Mock, patch

from helpdesk.demo_server import load_real_config
from helpdesk.workflow import Workflow
from tests import test_demo_real_http as fixture


PAYLOAD = {'passage': 'He returned home to look after his mother.',
    'stem': 'Why did he return home?', 'number': '12', 'question_type': '阅读理解',
    'options': {'A': 'To look after his mother.', 'B': 'To get a new job.',
                'C': 'To meet his friends.', 'D': 'To spend his holiday.'}}


class ReviewedQuestionWorkbenchTests(unittest.TestCase):
    setUp = fixture.RealWorkbenchTests.setUp
    tearDown = fixture.RealWorkbenchTests.tearDown
    start = fixture.RealWorkbenchTests.start
    request = fixture.RealWorkbenchTests.request
    action = fixture.RealWorkbenchTests.action
    generate = fixture.RealWorkbenchTests.generate
    finish = fixture.RealWorkbenchTests.finish

    def start_auto(self):
        self.config['auto_prepare_after_question_review'] = True
        self.transport = Mock(side_effect=AssertionError('No desktop execution'))
        self.start(transport_factory=self.transport)

    def make_draft(self, request_id='once'):
        code, data = self.request('POST', '/api/operator-tasks', {
            'action': 'create', 'request_id': request_id, 'payload': PAYLOAD})
        self.assertEqual(code, 200)
        return data['result']

    def review(self, draft):
        with patch('helpdesk.operator_tasks.verify_bundle', return_value=self.bundle), \
                patch('helpdesk.workflow.verify_bundle', return_value=self.bundle), \
                patch('helpdesk.automatic_preparation.verify_bundle', return_value=self.bundle):
            return self.request('POST', '/api/operator-tasks', {
                'action': 'review', 'draft_id': draft['id'], 'revision': draft['revision'],
                'reviewer': 'Initial source reviewer', 'source_evidence': 'Verified fixture worksheet'})

    def persisted(self):
        tables = ('runs', 'outbox', 'audit', 'reviews', 'answers', 'performance_units',
                  'reviewed_question_queue', 'reviewed_question_attempts', 'reviewed_question_admissions')
        return {table: [dict(row) for row in self.store.all(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}

    def test_review_freezes_and_enqueues_once_without_another_human_gate(self):
        self.start_auto()
        state = self.request('GET', '/api/state')[1]
        self.assertTrue(state['question_auto_continue'])
        self.assertFalse(state['dashboard']['health']['answer_review_required'])
        self.assertEqual(state['dashboard']['health']['delivery_policy']['ANSWER'], 'MANUAL')
        self.assertEqual(set(state['allowed_actions']), {'stop', 'resume'})
        draft = self.make_draft()
        code, response = self.review(draft)
        self.assertEqual(code, 200)
        task = response['result']
        self.assertIsNotNone(task['run_id'])
        self.assertEqual(task['auto_queue']['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertTrue(task['auto_queue']['enqueued'])
        self.assertFalse(task['auto_queue']['source_review_required_again'])
        self.assertEqual(self.review(draft)[1]['result']['auto_queue'], task['auto_queue'])
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 2)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_queue')[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 0)
        for table in ('reviews', 'answers', 'performance_units', 'delivery_checks'):
            self.assertEqual(self.store.one(f'SELECT COUNT(*) FROM {table}')[0], 0)
        self.transport.assert_not_called()
        self.assertEqual(self.calls, 0)

    def test_get_and_restart_preserve_queue_without_execution_or_forged_completion(self):
        self.start_auto()
        task = self.review(self.make_draft())[1]['result']
        before = self.persisted()
        for _ in range(2):
            with patch('helpdesk.reviewed_question_queue.advance') as advance:
                data = self.request('GET', '/api/operator-tasks')[1]
            advance.assert_not_called()
            self.assertTrue(data['question_auto_continue'])
            self.assertEqual(data['tasks'][0]['auto_queue'], task['auto_queue'])
            self.assertFalse(data['tasks'][0]['preparation_reviewed'])
            self.assertFalse(data['formal_statistics_eligible'])
        self.assertEqual(self.persisted(), before)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.server = None
        self.start_auto()
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.request('GET', '/api/operator-tasks')[1]['tasks'][0]['auto_queue'], task['auto_queue'])
        self.transport.assert_not_called()

    def test_browser_cannot_replace_luna_queue_with_manual_generation_or_send(self):
        self.start_auto()
        task = self.review(self.make_draft())[1]['result']
        before = self.persisted()
        for action in ('freeze', 'generate'):
            self.assertEqual(self.request('POST', '/api/operator-tasks', {
                'action': action, 'task_id': task['id']})[0], 400)
        for action in ('real_generate', 'real_approve', 'real_dispatch_test'):
            self.assertEqual(self.action(action)[0], 400)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.server.job_snapshot(), {})
        self.transport.assert_not_called()

    def test_ack_prerequisite_holds_without_discarding_first_source_review(self):
        Workflow(self.store).set_require_ack_before_generation(True)
        self.start_auto()
        draft = self.make_draft()
        code, response = self.review(draft)
        self.assertEqual(code, 200)
        task = response['result']
        self.assertIsNone(task['run_id'])
        self.assertEqual(task['auto_queue']['phase'], 'WAITING_ACK')
        self.assertFalse(task['auto_queue']['enqueued'])
        self.assertFalse(task['auto_queue']['source_review_required_again'])
        self.assertEqual(self.review(draft)[1]['result']['id'], task['id'])
        listed = self.request('GET', '/api/operator-tasks')[1]['tasks'][0]
        self.assertEqual(listed['auto_queue']['phase'], 'WAITING_ACK')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM outbox WHERE state='SENT_UI_CONFIRMED'")[0], 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0], 1)
        self.transport.assert_not_called()

    def test_bad_flag_rejected_before_database_is_opened(self):
        for value in ('true', 1, None, []):
            self.config_path.write_text(json.dumps({**self.config,
                'auto_prepare_after_question_review': value}), encoding='utf-8')
            with patch('helpdesk.demo_server._store') as open_store:
                with self.assertRaisesRegex(ValueError, 'must be a boolean'):
                    load_real_config(self.config_path)
            open_store.assert_not_called()

    def test_background_admits_later_ack_without_second_review_and_rejects_simulated_ack(self):
        Workflow(self.store).set_require_ack_before_generation(True)
        self.start_auto()
        with patch('helpdesk.operator_tasks.verify_bundle', return_value=self.bundle), \
                patch('helpdesk.workflow.verify_bundle', return_value=self.bundle), \
                patch('helpdesk.automatic_preparation.verify_bundle', return_value=self.bundle):
            task = self.review(self.make_draft())[1]['result']
            self.assertEqual(task['auto_queue']['phase'], 'WAITING_ACK')
            self.store.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=1 WHERE message_id=? AND purpose='ACK'",
                               (task['message_id'],))
            # This is an isolated recorded-ACK fixture, never a desktop send.
            # Give the background admission loop an opportunity to reject it.
            time.sleep(1.2)
            self.assertIsNone(self.store.one('SELECT run_id FROM operator_tasks WHERE id=?', (task['id'],))[0])
            self.assertEqual(self.request('GET', '/api/operator-tasks')[1]['tasks'][0]['auto_queue']['phase'], 'WAITING_ACK')
            self.store.execute("UPDATE outbox SET simulated=0 WHERE message_id=? AND purpose='ACK'", (task['message_id'],))
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                run = self.store.one('SELECT run_id FROM operator_tasks WHERE id=?', (task['id'],))[0]
                if run:
                    break
                time.sleep(.02)
            self.assertIsNotNone(run)
        listed = self.request('GET', '/api/operator-tasks')[1]['tasks'][0]
        self.assertEqual(listed['auto_queue']['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertTrue(listed['auto_queue']['enqueued'])
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 2)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_queue')[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 0)
        self.assertIsNone(self.server.reviewed_queue_error)
        self.transport.assert_not_called()

    def test_feature_is_optional_and_legacy_review_does_not_start_a_run(self):
        self.config['auto_prepare_after_question_review'] = False
        self.start()
        self.assertFalse(self.request('GET', '/api/state')[1]['question_auto_continue'])
        task = self.review(self.make_draft())[1]['result']
        self.assertIsNone(task['run_id'])
        self.assertNotIn('auto_queue', task)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 1)

    def test_headless_page_review_once_shows_queue_without_follow_on_buttons(self):
        try:
            from playwright.sync_api import expect, sync_playwright
        except ImportError:
            self.skipTest('Optional Playwright dependency unavailable')
        self.start_auto()
        errors = []
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.origin)
                expect(page.locator('#mode-label')).to_have_text('题面确认后排队')
                form = page.locator('#operator-form')
                for name, value in {key: PAYLOAD[key] for key in ('number', 'passage', 'stem')}.items():
                    form.locator(f'[name="{name}"]').fill(value)
                for label, value in PAYLOAD['options'].items():
                    form.locator(f'[name="{label}"]').fill(value)
                form.get_by_role('button', name='保存新题草稿').click()
                expect(page.locator('#status')).to_have_text('新题草稿已保存。')
                page.locator('#operator-reviewer').fill('Initial source reviewer')
                page.locator('#operator-evidence').fill('Verified fixture worksheet')
                page.locator('#operator-tasks summary').click()
                with patch('helpdesk.operator_tasks.verify_bundle', return_value=self.bundle), \
                        patch('helpdesk.workflow.verify_bundle', return_value=self.bundle), \
                        patch('helpdesk.automatic_preparation.verify_bundle', return_value=self.bundle):
                    page.get_by_role('button', name='确认题面清楚并排队').click()
                    expect(page.locator('#status')).to_contain_text('无需再次审核题面')
                expect(page.locator('#operator-tasks summary')).to_contain_text('已排队，等待 Luna 操作')
                self.assertEqual(page.locator('#operator-tasks button').count(), 0)
                self.assertFalse(page.locator('#real-controls').is_visible())
                page.reload()
                expect(page.locator('#operator-tasks summary')).to_contain_text('已排队，等待 Luna 操作')
                self.assertEqual(page.locator('#operator-tasks button').count(), 0)
            finally:
                browser.close()
        self.assertEqual(errors, [])
        self.transport.assert_not_called()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0], 2)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_queue')[0], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")[0], 1)
        for table in ('reviews', 'answers', 'performance_units', 'delivery_checks'):
            self.assertEqual(self.store.one(f'SELECT COUNT(*) FROM {table}')[0], 0)


if __name__ == '__main__':
    unittest.main()
