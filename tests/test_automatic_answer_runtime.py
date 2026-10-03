"""Anonymous synthetic page/queue tests; no model, account or real desktop."""
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from PIL import Image
from helpdesk import luna_navigation
from helpdesk.automatic_answer_runtime import AutomaticAnswerRuntime, _AnswerConnection
from helpdesk.call_costs import task_cost
from helpdesk.locking import resource_lock
from helpdesk.mcp_transport import MCPProcess
from helpdesk.reviewed_question_queue import enqueue, get
from helpdesk.session_isolation import claim_deepseek_chat
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tests import test_question_matching as fixtures
from tests.test_mcp_preparation import URL


META = 'Selected Displays: 0\nScreenshot Region: (0,0,100,100)\n0:DISPLAY1 (0,0,100,100)\n'


class SyntheticDesktop(fixtures.TwoStageDesktop):
    simulated = True
    def __init__(self, root, snapshot, *, blank_new=False):
        super().__init__(snapshot)
        self.root = root
        self.url = 'https://chat.deepseek.com/a/chat/s/old-fixture'
        self.blank_new, self.moved = blank_new, False
        self.archive = root / 'data/private/windows-mcp'
        self.archive.mkdir(parents=True)
        self.screen = self.archive / 'SYNTHETIC-screen.png'
        Image.new('RGB', (100, 100), 'white').save(self.screen)

    def call(self, tool, args):
        if tool == 'Screenshot':
            self.calls.append((tool, args))
            identifier = uuid4().hex
            record = {'tool': tool, 'is_error': False, 'attempt_id': identifier,
                'content': [{'type': 'text', 'text': META}, {'type': 'image', 'path': str(self.screen)}]}
            path = self.archive / (identifier + '.json')
            path.write_text(json.dumps(record), encoding='utf-8')
            (self.archive / ('attempt-' + identifier + '.json')).write_text(json.dumps({
                'tool': tool, 'status': 'TOOL_RETURNED', 'attempt_id': identifier,
                'result_path': str(path), 'started_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
            return record
        if tool == 'Click' and not self.picker and args['loc'] in ([8, 9], [11, 12]):
            self.calls.append((tool, args))
            if args['loc'] == [11, 12]:
                self.url = 'https://chat.deepseek.com/' if self.blank_new else URL
            return {'is_error': False}
        result = super().call(tool, args)
        if tool == 'Snapshot':
            result['content'][0]['text'] = META + result['content'][0]['text'].replace(URL, self.url)
            if not self.picker:
                result['content'][0]['text'] += '\n    └── (11,12) 按钮 "开启新对话"\n'
                if self.moved:
                    result['content'][0]['text'] = result['content'][0]['text'].replace('(11,12)', '(21,12)')
        return result


class AutomaticAnswerTests(unittest.TestCase):
    def setUp(self):
        fixtures.TwoStageIntegrationTests.setUp(self)
        self.desktop = SyntheticDesktop(self.base, self.snapshot)
        self.config = {'enabled': True, 'display_index': 0, 'new_chat_button': '开启新对话',
                       'preparation_controls': self.controls}
        self.navigator = Mock(side_effect=self.proposal)
        self.root_patch = patch.object(luna_navigation, 'ROOT', self.base)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.queued = enqueue(self.db, self.task['id'], self.manifest)
        self.runtime = self.make_runtime()

    def proposal(self, record, instruction, actions, *, display_index):
        return {'action': actions[0], 'loc': [11, 12] if actions[0] == 'DEEPSEEK_NEW_CHAT' else [8, 9],
                'display_region': [0, 0, 100, 100], 'display_index': display_index}

    def make_runtime(self):
        runtime = AutomaticAnswerRuntime(self.db.path, self.config, manifest=self.manifest,
            reference_config=Path(__file__).resolve().parents[1] / 'config/reference-lookup.example.toml',
            transport_factory=lambda: nullcontext(self.desktop), navigator=self.navigator)
        runtime.workspace = self.base
        return runtime

    def clicks(self):
        return [args['loc'] for tool, args in self.desktop.calls if tool == 'Click']

    def test_existing_queue_runs_two_stages_once_and_keeps_draft_undelivered(self):
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_PREPARATION')
        self.assertEqual(self.runtime.tick()['state'], 'ATTACHMENTS_READY')
        self.assertEqual(self.runtime.tick()['state'], 'GENERATED')
        self.assertEqual(self.runtime.tick()['state'], 'IDLE')
        self.assertEqual(self.navigator.call_count, 3)
        self.assertEqual(len(self.desktop.submission_uploads), 2)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 1)
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE purpose='ANSWER'")[0], 'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual([a['state'] for a in get(self.db, self.task['id'])['attempts']], ['SUCCEEDED'] * 3)
        usage = task_cost(self.db, self.task['run_id'])
        self.assertEqual(usage['call_attempts']['SCHEDULER'], 3)
        self.assertEqual(usage['call_attempts']['DEEPSEEK_MATCH'], 1)
        self.assertEqual(usage['call_attempts']['DEEPSEEK_TEACH'], 1)
        self.assertTrue(usage['injected_evidence'])
        self.assertIsNone(usage['total_cny'])
        restarted = self.make_runtime()
        self.assertEqual(restarted.tick()['state'], 'IDLE')
        self.assertEqual(self.navigator.call_count, 3)

    def test_existing_owned_session_goes_directly_to_preparation(self):
        claim_deepseek_chat(self.snapshot, URL)
        self.desktop.url = URL
        self.assertEqual(self.runtime.tick()['state'], 'ATTACHMENTS_READY')
        self.assertNotIn([11, 12], self.clicks())
        self.assertEqual(self.navigator.call_args.args[2][0], 'DEEPSEEK_COMPOSER')

    def test_layout_change_after_luna_stops_before_input_and_restart_does_not_replay(self):
        def move(*args, **kwargs):
            result = self.proposal(*args, **kwargs)
            self.desktop.moved = True
            return result
        self.navigator.side_effect = move
        self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(self.clicks(), [])
        self.assertEqual(self.make_runtime().tick()['state'], 'IDLE')
        self.assertEqual(self.navigator.call_count, 1)

    def test_blank_new_chat_never_claimed_or_submitted_or_clicked_twice(self):
        self.desktop.blank_new = True
        with patch('helpdesk.automatic_answer_runtime.time.sleep'):
            self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(self.clicks(), [[11, 12]])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0], 0)
        self.assertEqual(self.desktop.uploaded, [])
        self.assertEqual(self.desktop.submission_uploads, [])
        self.assertEqual(self.make_runtime().tick()['state'], 'IDLE')

    def test_wrong_foreground_does_not_call_luna(self):
        original = self.desktop.call
        def wrong(tool, args):
            result = original(tool, args)
            if tool == 'Snapshot':
                result['content'][0]['text'] = result['content'][0]['text'].replace('Microsoft Edge', 'Not Edge')
            return result
        with patch.object(self.desktop, 'call', side_effect=wrong):
            self.assertEqual(self.runtime.tick()['state'], 'WAITING_FOR_PAGE')
            self.assertEqual(self.make_runtime().tick()['state'], 'WAITING_FOR_PAGE')
            self.assertEqual(self.runtime.tick()['state'], 'NEEDS_ATTENTION')
            self.assertEqual(self.runtime.tick()['state'], 'IDLE')
        self.assertEqual(get(self.db, self.task['id'])['attempts'], [])
        self.navigator.assert_not_called()
        self.assertEqual(self.clicks(), [])

    def test_ambiguous_new_chat_button_stops_before_luna_or_input(self):
        original = self.desktop.call
        def ambiguous(tool, args):
            result = original(tool, args)
            if tool == 'Snapshot':
                result['content'][0]['text'] += '\n    └── (31,32) 按钮 "开启新对话"\n'
            return result
        with patch.object(self.desktop, 'call', side_effect=ambiguous):
            self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.navigator.assert_not_called()
        self.assertEqual(self.clicks(), [])

    def test_verification_prompt_pauses_all_dispatch_before_any_input(self):
        original = self.desktop.call
        def verification(tool, args):
            result = original(tool, args)
            if tool == 'Snapshot':
                result['content'][0]['text'] += '\n    └── (21,22) 编辑 "验证码"\n'
            return result
        with patch.object(self.desktop, 'call', side_effect=verification):
            self.assertEqual(self.runtime.tick()['state'], 'NEEDS_ATTENTION')
        self.assertTrue(Workflow(self.db)._stopped())
        self.assertTrue(self.runtime.pause_requested.is_set())
        self.assertEqual(get(self.db, self.task['id'])['attempts'], [])
        self.navigator.assert_not_called()
        self.assertEqual(self.clicks(), [])

    def test_luna_timeout_is_recorded_unknown_and_never_replayed(self):
        self.navigator.side_effect = TimeoutError('SYNTHETIC model response unavailable')
        self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(self.make_runtime().tick()['state'], 'IDLE')
        self.assertEqual(self.navigator.call_count, 1)
        self.assertEqual(self.clicks(), [])
        usage = task_cost(self.db, self.task['run_id'])
        self.assertEqual([call['status'] for call in usage['calls']], ['UNKNOWN'])
        self.assertIsNone(usage['total_cny'])

    def test_correction_after_luna_prevents_click_on_old_question(self):
        def correct(*args, **kwargs):
            proposal = self.proposal(*args, **kwargs)
            material = self.db.one('SELECT material_id FROM questions WHERE id=?', (self.task['question_id'],))[0]
            Workflow(self.db).app.correct_material(material, self.task['message_id'],
                                                   'SYNTHETIC correction', 'SYNTHETIC correction')
            return proposal
        self.navigator.side_effect = correct
        self.assertEqual(self.runtime.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.clicks(), [])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def test_stop_is_observed_before_attempt_and_resume_does_not_reset_queue(self):
        Workflow(self.db).set_stop(True)
        with self.assertRaisesRegex(ValueError, 'PAUSED'):
            self.runtime.tick()
        self.assertEqual(get(self.db, self.task['id'])['attempts'], [])
        self.assertTrue(self.runtime.snapshot(self.db)['paused'])
        Workflow(self.db).set_stop(False)
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_PREPARATION')
        self.runtime.control('pause')
        with self.assertRaisesRegex(ValueError, 'PAUSED'):
            self.runtime.tick()
        self.runtime.control('resume')
        self.assertEqual(len(get(self.db, self.task['id'])['attempts']), 1)

    def test_changed_startup_manifest_stops_before_native_connection(self):
        self.runtime.manifest_sha256 = '0' * 64
        self.assertEqual(self.runtime.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(get(self.db, self.task['id'])['attempts'], [])
        self.assertEqual(self.desktop.calls, [])
        self.navigator.assert_not_called()

    def test_native_timeout_retains_desktop_lock_and_blocks_other_tasks_until_child_exits(self):
        process = _AnswerConnection(self.runtime)
        child = Mock(poll=Mock(return_value=None))
        process._proc = child
        def uncertain(connection, tool, arguments, **kwargs):
            connection._pending = True
            raise TimeoutError('SYNTHETIC native pending')
        with patch.object(MCPProcess, 'call', uncertain):
            with self.assertRaises(TimeoutError):
                process.call('Click', {'loc': [8, 9]})
        self.assertIs(self.runtime.pending_process, process)
        self.assertTrue(self.runtime.snapshot(self.db)['native_call_pending'])
        self.assertTrue(Workflow(self.db)._stopped())
        with self.assertRaises(TimeoutError):
            with resource_lock(self.base / 'data/windows-interactive-desktop.lock', timeout=.01):
                self.fail('Pending native input lock was released')
        with self.assertRaisesRegex(ValueError, 'still pending'):
            self.runtime.control('resume')
        child.poll.return_value = 0
        self.runtime.control('resume')
        with resource_lock(self.base / 'data/windows-interactive-desktop.lock', timeout=.01):
            pass
        self.assertIsNone(self.runtime.pending_process)
        # Only the workbench's existing workflow resume clears the global pause.
        self.assertTrue(Workflow(self.db)._stopped())

    def test_generation_wait_does_not_occupy_background_ack_loop(self):
        from helpdesk.demo_server import DemoHTTPServer
        from helpdesk.delivery import MockDesktop
        from helpdesk.domain import Intent
        from helpdesk.service import Helpdesk, Incoming
        from tests.test_mcp_group_delivery import reviewed_config
        entered, release = Event(), Event()
        def slow(runtime):
            runtime.require_running()
            entered.set()
            release.wait(6)
            return {'state': 'SYNTHETIC_WAIT'}
        Workflow(self.db).set_stop(True)
        sender = MockDesktop(self.base / 'synthetic-sender.db')
        with patch.object(AutomaticAnswerRuntime, 'tick', slow):
            server = DemoHTTPServer(('127.0.0.1', 0), Path(self.db.path), processing_mode='ACK_ONLY',
                source_review_manifest=self.manifest, automatic_answer_config=self.config,
                automatic_answer_transport_factory=lambda: nullcontext(self.desktop),
                automatic_answer_navigator=self.navigator,
                automatic_delivery_config={'enabled': True, 'desktop': reviewed_config()},
                automatic_desktop_factory=lambda: sender)
            try:
                binding = Helpdesk(self.db).bind('anonymous-second-group', 'anonymous-second-student',
                                               '匿名测试学生', verified=True)
                incoming = Helpdesk(self.db).ingest(Incoming(binding, 'SYNTHETIC new question', Intent.NEW))
                Workflow(self.db).set_delivery_policy({'ACK': 'AUTO', 'ANSWER': 'MANUAL', 'CORRECTION': 'MANUAL'},
                                                      'SYNTHETIC background test')
                Workflow(self.db).set_stop(False)
                self.assertTrue(entered.wait(3))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not sender.receipts():
                    time.sleep(.025)
                self.assertTrue(sender.receipts())
                self.assertFalse(release.is_set())
                self.assertTrue(server.answer_queue_thread.is_alive())
                self.assertTrue(server.reviewed_queue_thread.is_alive())
            finally:
                release.set()
                server.server_close()
            self.assertFalse(server.answer_queue_thread.is_alive())


class AutomaticAnswerConfigurationTests(unittest.TestCase):
    def test_disabled_runtime_never_creates_database_or_starts_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'missing.db'
            factory = Mock()
            runtime = AutomaticAnswerRuntime(path, transport_factory=factory)
            self.assertEqual(runtime.tick(), {'state': 'DISABLED'})
            runtime.snapshot()
            factory.assert_not_called()
            self.assertFalse(path.exists())

    def test_wrong_mode_and_invalid_display_rejected_before_execution(self):
        for controls, display in (({'preparation_mode': 'FAST_UPLOAD_THEN_GENERATE'}, 0),
                                   ({'preparation_mode': 'VERIFY_THEN_TEACH'}, True)):
            with self.subTest(controls=controls, display=display), self.assertRaisesRegex(ValueError, 'VERIFY_THEN_TEACH'):
                AutomaticAnswerRuntime('missing.db', {'enabled': True, 'display_index': display,
                    'new_chat_button': '开启新对话', 'preparation_controls': controls})
