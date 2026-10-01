"""Source-only local workbench without a seed run; all message/LLM/receipt data are anonymous fixtures."""
from http.client import HTTPConnection
import json
from threading import Thread
import time
import unittest
from unittest.mock import Mock, patch

from helpdesk.demo_server import DemoHTTPServer
from helpdesk.operator_tasks import OperatorTasks
from helpdesk.reviewed_question_queue import advance
from helpdesk.semantic_decisions import SharedSemanticDecisions
from helpdesk.workflow import Workflow
from tests import test_source_question_tasks as source_fixture
from tests.test_shared_source_delivery_integration import FixtureManualAdapter


class PersonalSourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.fx = source_fixture.SourceQuestionTasksTests()
        # Initialize original source/resolution only; this test creates the shared draft once.
        with patch.object(self.fx, 'create', return_value=None):
            self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.db = self.fx.db
        self.actor = Mock(side_effect=AssertionError('The web workbench must not drive the desktop'))
        self.bundle_stub = patch('helpdesk.teaching_bundle.verify_bundle', return_value=self.fx.manifest)
        self.bundle_stub.start()
        self.addCleanup(self.bundle_stub.stop)
        shared = SharedSemanticDecisions(self.db, self.fx.raw)
        self.decision = shared.confirm(self.fx.receipt['id'], question_type='阅读理解',
            actor='anonymous LLM resolution fixture', evidence='fixture original material and question')
        self.draft = self.fx.tasks.create_from_semantic(self.fx.raw, self.decision)
        self.unit = shared.counting_unit(self.decision)

    def serve(self, *, enabled=True, configured=False):
        config = None
        if configured:
            config = self.fx.base / 'collector.toml'
            config.write_text('[collector]\nbusiness_database = ' +
                json.dumps((self.fx.base / 'business.db').as_posix()) +
                '\n[stage]\nsource_review_manifest = ' +
                json.dumps(self.fx.manifest_path.as_posix()) + '\n', encoding='utf-8')
        server = DemoHTTPServer(('127.0.0.1', 0), self.fx.base / 'business.db',
            processing_mode='ACK_ONLY', performance_enabled=True,
            collector_config=config,
            source_review_manifest=self.fx.manifest_path if enabled and not configured else None,
            native_archive_root=self.fx.base / 'no-native-records',
            real_generator=self.actor, transport_factory=self.actor, desktop_factory=self.actor)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join(3)
        self.addCleanup(cleanup)
        return server

    def request(self, server, path, payload=None, *, csrf=True):
        headers = {'Origin': 'http://127.0.0.1:' + str(server.server_port)}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            if csrf: headers['X-CSRF-Token'] = server.csrf_token
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('GET' if payload is None else 'POST', path,
            json.dumps(payload) if payload is not None else None, headers)
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    def review(self, server):
        return self.request(server, '/api/operator-tasks', {
            'action': 'review', 'draft_id': self.draft['id'], 'revision': self.draft['revision'],
            'reviewer': 'anonymous human fixture', 'source_evidence': 'fixture original question is clear'})

    def wait_enqueued(self, server, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = self.request(server, '/api/operator-tasks')[1]
            task = next(task for task in data['tasks'] if task['id'] == task_id)
            if task.get('auto_queue', {}).get('enqueued'): return task
            time.sleep(.05)
        self.fail('Original reviewed task did not enqueue after its recorded fixture ACK')

    def test_review_needs_no_seed_run_and_preserves_ack_delivery_and_quantity_boundaries(self):
        server = self.serve()
        self.assertIsNone(server.real_config)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        state = self.request(server, '/api/state')[1]
        self.assertIs(state['source_review_enabled'], True)
        self.assertIs(state['question_auto_continue'], True)
        self.assertIs(state['simulation'], False)
        self.assertIs(state['collector']['configured'], False)
        status, data = self.review(server)
        self.assertEqual(status, 200)
        task = data['result']
        self.assertEqual(task['message_id'], self.fx.outcome.message_id)
        self.assertEqual(task['auto_queue']['phase'], 'WAITING_ACK')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        self.assertEqual(self.review(server)[1]['result']['id'], task['id'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")[0], 1)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units WHERE id=?', (self.unit,))[0], 0)
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=1 WHERE purpose='ACK'")
        self.assertEqual(self.request(server, '/api/operator-tasks')[1]['tasks'][0]['auto_queue']['phase'], 'WAITING_ACK')
        self.fx.confirm_fixture_ack()
        task = self.wait_enqueued(server, task['id'])
        self.assertEqual(task['auto_queue']['phase'], 'WAITING_DESKTOP_EXECUTOR')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviewed_question_attempts')[0], 0)
        for kwargs in ({'session_creator': lambda **_: source_fixture.URL},
                       {'preparer': self.fx.prepare}, {'generator': self.fx.generate}):
            advance(self.db, task['id'], executor='LUNA', **kwargs)
        row = self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        self.assertEqual(row['body'], self.fx.fixture_answer)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units WHERE id=?', (self.unit,))[0], 0)
        flow = Workflow(self.db)
        observer = FixtureManualAdapter(self.fx.base)
        flow.stage_manual_answer(row['id'], adapter=observer)
        self.assertEqual(flow.verify_manual_delivery(row['id'], adapter=observer)['counting_status'], 'CONFIRMED')
        first = self.request(server, '/api/performance?date=2026-09-30')[1]
        second = self.request(server, '/api/performance?date=2026-09-30')[1]
        self.assertEqual(first, second)
        self.assertEqual(first['report']['summary']['day_composite_articles'], 1)
        self.assertEqual(first['report']['summary']['night_articles'], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_report_versions')[0], 0)
        self.actor.assert_not_called()

    def test_trusted_collector_config_enables_review_without_old_prepared_session(self):
        server = self.serve(configured=True)
        self.assertIsNone(server.real_config)
        state = self.request(server, '/api/state')[1]
        self.assertIs(state['source_review_enabled'], True)
        self.assertIs(state['question_auto_continue'], True)
        self.assertEqual(self.review(server)[1]['result']['auto_queue']['phase'], 'WAITING_ACK')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        self.actor.assert_not_called()

    def test_invalid_manifest_configuration_fails_before_database_or_desktop_use(self):
        for value in (False, 1, '', [], {}):
            config = self.fx.base / 'invalid-collector.toml'
            value_text = json.dumps(value)
            config.write_text('[stage]\nsource_review_manifest = ' + value_text + '\n', encoding='utf-8')
            with self.subTest(value=value), patch('helpdesk.demo_server._store') as store:
                with self.assertRaisesRegex(ValueError, 'source_review_manifest must be a nonempty path'):
                    DemoHTTPServer(('127.0.0.1', 0), self.fx.base / 'must-not-create.db',
                        collector_config=config, processing_mode='ACK_ONLY')
                store.assert_not_called()
        self.assertFalse((self.fx.base / 'must-not-create.db').exists())
        self.actor.assert_not_called()

    def test_enabling_source_review_does_not_reenable_a_disabled_delivery_purpose(self):
        Workflow(self.db).set_delivery_policy(
            {'ACK': 'AUTO', 'ANSWER': 'DISABLED', 'CORRECTION': 'DISABLED'}, 'fixture delivery disabled')
        server = self.serve(configured=True)
        policy, _ = Workflow(self.db)._delivery_policy_config()
        self.assertEqual(policy['ANSWER'], 'DISABLED')
        self.assertEqual(policy['CORRECTION'], 'DISABLED')
        self.assertEqual(self.review(server)[1]['result']['auto_queue']['phase'], 'WAITING_ACK')
        self.actor.assert_not_called()

    def test_existing_operator_tests_and_arbitrary_generation_or_target_requests_stay_blocked(self):
        server = self.serve()
        test_draft = OperatorTasks(self.db).create_draft(self.draft['payload'], request_id='anonymous-test')
        listed = self.request(server, '/api/operator-tasks')[1]
        self.assertEqual([draft['id'] for draft in listed['drafts']], [self.draft['id']])
        for payload in ({'action': 'create', 'payload': self.draft['payload'], 'request_id': 'forged'},
                        {'action': 'generate', 'task_id': self.draft['id']},
                        {'action': 'freeze', 'task_id': self.draft['id']},
                        {'action': 'review', 'draft_id': test_draft['id'], 'revision': 1,
                         'reviewer': 'fixture', 'source_evidence': 'fixture'}):
            self.assertEqual(self.request(server, '/api/operator-tasks', payload)[0], 403)
        for action in ('dispatch', 'dispatch_ack', 'real_generate', 'new_alice'):
            self.assertEqual(self.request(server, '/api/action', {'action': action})[0], 403)
        invalid = {'action': 'review', 'draft_id': self.draft['id'], 'revision': 1,
                   'reviewer': 'fixture', 'source_evidence': 'fixture', 'recipient': 'arbitrary'}
        self.assertEqual(self.request(server, '/api/operator-tasks', invalid)[0], 400)
        self.assertEqual(self.request(server, '/api/operator-tasks', invalid, csrf=False)[0], 403)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        self.actor.assert_not_called()

    def test_missing_or_changed_source_evidence_cannot_be_reviewed(self):
        server = self.serve()
        with self.fx.raw.connect() as source:
            source.execute('UPDATE messages SET sender_id=?', ('different-student',))
        data = self.request(server, '/api/operator-tasks')[1]
        self.assertIs(data['drafts'][0]['source_valid'], False)
        self.assertEqual(self.review(server)[0], 400)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM operator_tasks')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)

    def test_disabled_default_and_unapproved_bundle_do_not_open_review(self):
        server = self.serve(enabled=False)
        self.assertIs(self.request(server, '/api/state')[1]['source_review_enabled'], False)
        self.assertEqual(self.review(server)[0], 403)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM operator_tasks')[0], 0)
        self.bundle_stub.stop()
        self.bundle_stub = patch('helpdesk.teaching_bundle.verify_bundle', return_value={
            **self.fx.manifest, 'answer_generation_allowed_by_course': False})
        self.bundle_stub.start()
        self.addCleanup(self.bundle_stub.stop)
        with patch('helpdesk.demo_server._store') as store:
            with self.assertRaisesRegex(ValueError, 'approved answer-generation'):
                self.serve()
            store.assert_not_called()

    def test_headless_single_source_review_keeps_test_form_hidden_and_original_time(self):
        from playwright.sync_api import sync_playwright, expect
        server = self.serve()
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto('http://127.0.0.1:' + str(server.server_port))
                expect(page.locator('#question-review-panel')).to_be_visible()
                expect(page.locator('#operator-form')).to_be_hidden()
                entry = page.locator('#operator-tasks details').first
                entry.locator('summary').click()
                expect(entry).to_contain_text('Fixture English group')
                expect(entry).to_contain_text('22:58')
                expect(entry).not_to_contain_text('23:05')
                page.locator('#operator-reviewer').fill('anonymous human fixture')
                page.locator('#operator-evidence').fill('fixture original question clear')
                entry.get_by_role('button', name='确认题面清楚并排队').click()
                expect(page.locator('#status')).to_contain_text('无需再次审核题面')
                expect(entry).to_contain_text('等待收到确认')
                self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
                page.locator('button[data-action="stop"]').click()
                expect(page.locator('#health')).to_contain_text('发送已停止')
                self.assertIs(self.request(server, '/api/state')[1]['dashboard']['health']['stopped'], True)
                self.actor.assert_not_called()
            finally:
                browser.close()


if __name__ == '__main__': unittest.main()
