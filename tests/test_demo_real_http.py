"""HTTP real workbench integration; injected generation/transport never touches desktop."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from helpdesk.__main__ import demo_question
from helpdesk.demo_server import DemoHTTPServer, WorkbenchTestDesktop
from helpdesk.domain import Intent
from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.operator_tasks import OperatorTasks
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.test_answer_queue import queue_test_answer
from helpdesk.test_routing import TestRecipient
from helpdesk.workflow import Workflow


class RealWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db_path = self.base / 'real.db'
        self.store = Store(self.db_path)
        app = Helpdesk(self.store)
        bid = app.bind('verified-group', 'verified-student', '学生', verified=True)
        self.turn = app.ingest(Incoming(bid, '请讲第12题', Intent.NEW,
            verified_question=demo_question(), raw_material='Passage', verified_material='Passage')).turn_id
        course = self.base / 'course.md'; course.write_text('Verified course', encoding='utf-8')
        self.manifest = self.base / 'manifest.json'; self.manifest.write_text('{}', encoding='utf-8')
        self.bundle = {'answer_generation_allowed_by_course': True,
            'workflow_teaching_paths': [str(course)],
            'files': [{'snapshot_path': str(course), 'snapshot_sha256': sha256(course.read_bytes()).hexdigest()}],
            'reviewed_policy_id': 'reviewed', 'question_type': '阅读理解'}
        with patch('helpdesk.workflow.verify_bundle', return_value=self.bundle):
            flow = Workflow(self.store, generation_adapter=PreparedDeepSeekGenerator(None,
                self.base / 'preparation.json', self.base / 'attempts'), teaching_manifest=self.manifest)
            self.run = flow.start(self.turn)
        self.snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?', (self.run,))[0])
        self.preparation = self.base / 'preparation.json'
        self.preparation.write_text(json.dumps({'run_id': self.run, 'operator_verified': True}), encoding='utf-8')
        self.config = {'database': 'real.db', 'run_id': self.run, 'preparation': 'preparation.json', 'manifest': 'manifest.json'}
        self.config_path = self.base / 'real.json'
        self.calls = 0
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        self.store.close(); self.tmp.cleanup()

    def finish(self, store):
        flow = Workflow(store)
        option = self.snapshot['student_question']['options'][0]
        return flow.finish(self.run, dict(adapter=PreparedDeepSeekGenerator.identity, simulated=False,
            run_id=self.run, session_id=self.snapshot['session_id'], web_session_evidence='test-only',
            uploaded_teaching_hashes={x['path']: x['sha256'] for x in self.snapshot['teaching_skills']},
            uploads_confirmed=True, complete=True, correct_option_id=option['id'], text='同学，本题选A，依据原文。'))

    def generate(self, store, run, preparation, manifest):
        self.calls += 1
        self.assertEqual(run, self.run)
        self.assertEqual(preparation, self.preparation)
        self.assertEqual(manifest, self.manifest)
        return self.finish(store)

    def start(self, **deps):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.server = DemoHTTPServer(('127.0.0.1', 0), self.base / 'unused.db',
            real_config=self.config_path, real_generator=deps.pop('real_generator', self.generate), **deps)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.token = self.request('GET', '/api/state')[1]['csrf_token']

    def request(self, method, path, body=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json', 'Origin': getattr(self, 'origin', ''),
                   'X-CSRF-Token': getattr(self, 'token', '')}
        conn.request(method, path, body=json.dumps(body) if body else None, headers=headers)
        response = conn.getresponse(); raw = response.read(); code = response.status
        content_type = response.getheader('Content-Type', ''); conn.close()
        return code, json.loads(raw) if 'application/json' in content_type else raw.decode('utf-8')

    def action(self, action):
        return self.request('POST', '/api/action', {'action': action})

    def job(self, action):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            job = self.request('GET', '/api/state')[1]['jobs'].get(action)
            if job and job['state'] != 'RUNNING': return job
            time.sleep(.01)
        self.fail('Background job did not complete')

    def test_real_state_and_fixed_actions(self):
        self.start()
        state = self.request('GET', '/api/state')[1]
        self.assertFalse(state['simulation']); self.assertFalse(state['dashboard']['simulation'])
        self.assertEqual(state['mode'], 'REAL_PREPARED'); self.assertFalse(state['test_delivery_available'])
        self.assertEqual(self.server.db_path, self.db_path)
        for action in ('new_alice','generate','dispatch','dispatch_ack','recover'):
            self.assertEqual(self.action(action)[0], 400)
        self.assertEqual(self.action('real_dispatch_test')[0], 400)
        self.assertEqual(self.request('POST', '/api/action', {'action':'real_generate','run_id':'other'})[0],400)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM runs')[0],1)
        script = self.request('GET','/app.js')[1]
        self.assertIn('node.hidden=realMode', script)
        self.assertIn('发送界面已确认（非已读回执）', script)

    def test_generate_then_review_and_repeat_without_resubmit(self):
        self.start()
        self.assertEqual(self.action('real_generate')[0],202)
        result = self.job('real_generate')['result']; self.assertEqual(result['state'],'GENERATED')
        self.assertFalse(self.action('real_generate')[1]['resubmitted']); self.assertEqual(self.calls,1)
        self.assertEqual(self.action('real_approve')[0],202)
        approved = self.job('real_approve')['result']; self.assertEqual(approved['state'],'APPROVED')
        self.assertEqual(self.store.one('SELECT simulated FROM outbox WHERE id=?',(approved['outbox_id'],))[0],0)
        self.action('real_approve')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0],1)

    def test_busy_operation_and_stop_do_not_replay(self):
        entered, release = Event(), Event()
        def blocked(*args):
            entered.set(); self.assertTrue(release.wait(3)); return self.generate(*args)
        self.start(real_generator=blocked)
        try:
            self.assertEqual(self.action('real_generate')[0],202); self.assertTrue(entered.wait(1))
            self.assertFalse(self.action('real_generate')[1]['resubmitted'])
            self.assertEqual(self.action('real_approve')[0],409)
            self.assertEqual(self.action('stop')[0],200)
            self.assertTrue(self.request('GET','/api/state')[1]['dashboard']['health']['stopped'])
            self.assertEqual(self.action('resume')[0],200)
        finally: release.set()
        self.job('real_generate'); self.assertEqual(self.calls,1)

    def test_uncertain_job_is_reported_and_never_replayed(self):
        def fail(*args):
            self.calls += 1; raise TimeoutError('uncertain test completion')
        self.start(real_generator=fail)
        self.action('real_generate'); self.assertEqual(self.job('real_generate')['state'],'FAILED')
        self.action('real_generate'); self.assertEqual(self.calls,1)

    def prepare_copy(self):
        source = self.finish(self.store)['outbox_id']; Workflow(self.store).approve(source)
        recipient = TestRecipient('wecom','verified-test-session','苇中鹤','test evidence',datetime.now(timezone.utc))
        copy = queue_test_answer(self.store, source, recipient)
        pin_path = self.base / 'pin.json'; pin_path.write_text(json.dumps({'outbox_id':copy}),encoding='utf-8')
        self.config.update(test_outbox_id=copy,pin='pin.json')
        return source, copy, recipient

    def test_only_configured_test_copy_dispatches_and_repeat_opens_no_transport(self):
        source, copy, recipient = self.prepare_copy()
        opened, sent = [], []
        @contextmanager
        def transport():
            opened.append(1); yield object()
        class Desktop:
            simulated=False; test_only=True; test_answer_transport=True
            lock_path=str(self.base/'delivery.lock')
            def __init__(self, transport, pin, root): pass
            def authorize(self, bound):
                if bound.outbox_id != copy or bound.group_key!='wecom': raise ValueError('Wrong test destination')
            def preflight(self, bound): self.authorize(bound)
            def send(self, bound):
                self.authorize(bound); sent.append(bound.outbox_id)
                return dict(confirmed=True,simulated=False,body_hash=bound.body_hash,target_platform='wecom',
                    target_key=recipient.stable_key,source_student_delivered=False)
        self.start(transport_factory=transport,desktop_factory=Desktop)
        self.action('real_dispatch_test'); job=self.job('real_dispatch_test')
        self.assertEqual(job['result']['state'],'SENT_UI_CONFIRMED')
        self.action('real_dispatch_test'); self.assertEqual(opened,[1]); self.assertEqual(sent,[copy])
        self.assertEqual(self.store.one('SELECT state FROM outbox WHERE id=?',(source,))[0],'PENDING')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM performance_units WHERE completion_outbox_id IS NOT NULL')[0],0)

    def test_original_answer_cannot_be_dispatched_as_test_copy(self):
        source, copy, recipient=self.prepare_copy()
        self.config['test_outbox_id']=source
        (self.base/'pin.json').write_text(json.dumps({'outbox_id':source}),encoding='utf-8')
        self.start(transport_factory=lambda:self.fail('Desktop opened'))
        self.action('real_dispatch_test'); job=self.job('real_dispatch_test')
        self.assertEqual(job['state'],'FAILED')
        self.assertEqual(self.store.one('SELECT state FROM outbox WHERE id=?',(source,))[0],'PENDING')

    def test_stale_source_review_fails(self):
        self.finish(self.store)
        self.store.execute('UPDATE questions SET context_revision=context_revision+1')
        self.start(); self.action('real_approve')
        self.assertEqual(self.job('real_approve')['state'],'FAILED')
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0],0)

    def test_stopped_generation_is_unqueued_and_can_run_after_resume(self):
        self.start(); self.action('stop')
        self.assertEqual(self.action('real_generate')[0],409)
        self.assertFalse(self.request('GET','/api/state')[1]['jobs'])
        self.action('resume'); self.action('real_generate')
        self.assertEqual(self.job('real_generate')['result']['state'],'GENERATED')

    def test_browser_real_controls_and_labels(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest("Optional Playwright dependency unavailable")
        self.start()
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(); page.goto(self.origin)
            page.wait_for_selector('#real-controls',state='visible')
            self.assertFalse(page.locator('button[data-action="new_alice"]').is_visible())
            self.assertTrue(page.locator('button[data-action="real_dispatch_test"]').is_disabled())
            self.assertIn('REAL',page.locator('#mode-label').inner_text())
            page.locator('button[data-action="real_generate"]').click()
            expect(page.locator("#real-jobs")).to_contain_text("已生成")
            self.assertEqual(self.calls,1)
            self.assertNotIn('模拟答复',page.locator('#answers').inner_text())
            browser.close()

    def test_fixed_app_switch_precedes_inherited_pin_verification(self):
        class Transport:
            def __init__(self): self.calls=[]
            def call(self, tool, arguments):
                self.calls.append((tool,arguments));return {'is_error':False}
        transport=Transport(); desktop=WorkbenchTestDesktop(transport,{},self.base)
        with patch('helpdesk.mcp_test_delivery.MCPTestAnswerDesktop.preflight',return_value='verified') as verified:
            self.assertEqual(desktop.preflight('bound'),'verified')
            verified.assert_called_once_with('bound')
        self.assertEqual(transport.calls,[('App',{'mode':'switch','name':'企业微信'})])
        transport.call=lambda *_: {'is_error':True}
        with patch('helpdesk.mcp_test_delivery.MCPTestAnswerDesktop.preflight') as verified:
            with self.assertRaises(RuntimeError): desktop.preflight('bound')
            verified.assert_not_called()

    def test_unreviewed_config_is_rejected(self):
        self.preparation.write_text(json.dumps({'run_id':self.run,'operator_verified':False}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'reviewed'): self.start()

    def operator_fixture(self, *, prepare=True, entered=None, release=None):
        tasks = OperatorTasks(self.store)
        draft = tasks.create_draft({'passage': 'He returned home to look after his mother.',
            'stem': 'Why did he return home?', 'number': '12', 'question_type': '阅读理解',
            'options': {'A': 'To look after his mother.', 'B': 'To get a new job.',
                        'C': 'To meet his friends.', 'D': 'To spend his holiday.'}})
        task = tasks.review(draft['id'], expected_revision=1, reviewer='Fixture reviewer',
                            source_evidence='Local test source Q12')
        patcher = patch('helpdesk.workflow.verify_bundle', return_value=self.bundle)
        patcher.start(); self.addCleanup(patcher.stop)
        with patch('helpdesk.operator_tasks.verify_bundle', return_value=self.bundle):
            task = tasks.freeze(task['id'], teaching_manifest=self.manifest,
                preparation_path=self.base / 'operator-preparation.json', evidence_dir=self.base / 'operator-attempts')
        snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?', (task['run_id'],))[0])
        url = 'https://chat.deepseek.com/a/chat/s/local-fixture'
        def page(tail):
            return {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text':
                'UI Tree:\ndesktop\n└── window "DeepSeek - Microsoft Edge"\n'
                f'    ├── (1,2) 文档 "DeepSeek" [value:"{url}"]\n' + tail}]}
        course = Path(snapshot['teaching_skills'][0]['path'])
        excerpt = course.read_text(encoding='utf-8')
        proof = self.base / 'operator-proof.json'
        proof.write_text(json.dumps(page(f'    └── text "{course.name} {excerpt}"')), encoding='utf-8')
        prep = {'run_id': task['run_id'], 'operator_verified': True,
            'status': 'OPERATOR_VERIFIED_UPLOAD_AND_INPUT', 'session_url': url,
            'input_fingerprint': input_fingerprint(snapshot), 'readback_evidence': str(proof),
            'readback_sha256': sha256(proof.read_bytes()).hexdigest(),
            'uploaded_teaching_hashes': {item['path']: item['sha256'] for item in snapshot['teaching_skills']},
            'course_readback_excerpts': {str(course): excerpt}}
        def write_preparation():
            Path(task['preparation_path']).write_text(json.dumps(prep), encoding='utf-8')
        if prepare: write_preparation()
        opened, calls = [], []
        class Transport:
            prompt = ''
            submitted = False
            def call(inner, tool, arguments):
                calls.append((tool, arguments))
                if tool == 'Type': inner.prompt = arguments['text']; return {}
                if tool == 'Shortcut': inner.submitted = True; return {}
                if not inner.prompt:
                    return page('    └── (100,200) 编辑 "给 DeepSeek 发送消息"\n')
                if not inner.submitted:
                    return page(f'    └── (100,200) 编辑 "给 DeepSeek 发送消息" [focused] [value:"{inner.prompt}"]\n')
                token = 'answer_run_' + task['run_id']
                answer = json.dumps({'option_label': 'A', 'text': '同学，我们来分析一下。选A，原文说明他回家照顾母亲。'}, ensure_ascii=False)
                return page('    ├── 按钮 "朗读"\n'
                    f'    ├── text "BEGIN_{token}"\n    ├── text "{answer}"\n'
                    f'    └── text "END_{token}"\n')
        @contextmanager
        def factory():
            opened.append(1)
            if entered: entered.set()
            if release: self.assertTrue(release.wait(3))
            yield Transport()
        return task, factory, opened, calls, write_preparation

    def operator_generate(self, task_id, **extra):
        return self.request('POST', '/api/operator-tasks', {'action': 'generate', 'task_id': task_id, **extra})

    def test_operator_generation_waits_for_verified_preparation_without_cached_failure(self):
        task, factory, opened, calls, prepare = self.operator_fixture(prepare=False)
        self.start(transport_factory=factory)
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.assertEqual(opened, [])
        self.assertNotIn('operator_generate:' + task['id'], self.server.job_snapshot())
        self.assertFalse(self.request('GET', '/api/operator-tasks')[1]['tasks'][0]['preparation_reviewed'])
        prepare()
        prep = json.loads(Path(task['preparation_path']).read_text(encoding='utf-8'))
        Path(task['preparation_path']).write_text(json.dumps(prep | {'operator_verified': False}), encoding='utf-8')
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.assertEqual(opened, [])
        prepare()
        self.assertTrue(self.request('GET', '/api/operator-tasks')[1]['tasks'][0]['preparation_reviewed'])
        self.assertEqual(self.operator_generate(task['id'])[0], 202)
        job = self.job('operator_generate:' + task['id'])
        self.assertEqual(job['result']['state'], 'GENERATED')
        self.assertEqual(job['action'], 'operator_generate')
        self.assertEqual(job['task_id'], task['id'])
        self.assertEqual(opened, [1]); self.assertEqual(self.calls, 0)
        self.assertEqual(sum(tool == 'Shortcut' for tool, _ in calls), 1)
        prompt = next(arguments['text'] for tool, arguments in calls if tool == 'Type')
        self.assertFalse(any(character in prompt for character in '{}\r\n\t'))
        self.assertIn('Why did he return home?', prompt)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM reviews')[0], 0)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_operator_generation_rejects_extra_paths_unrelated_or_stale_task(self):
        task, factory, opened, _, _ = self.operator_fixture()
        self.start(transport_factory=factory)
        for extra in ({'run_id': self.run}, {'preparation_path': 'injected'}, {'destination': 'someone'}):
            self.assertEqual(self.operator_generate(task['id'], **extra)[0], 400)
        for identifier in ('../../somewhere', self.run, ['bad']):
            self.assertEqual(self.operator_generate(identifier)[0], 400)
        self.store.execute("UPDATE operator_tasks SET label='OTHER' WHERE id=?", (task['id'],))
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.store.execute("UPDATE operator_tasks SET label='OPERATOR_TEST' WHERE id=?", (task['id'],))
        self.store.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?', (task['question_id'],))
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.assertEqual(opened, []); self.assertEqual(self.server.job_snapshot(), {})

    def test_operator_generation_requires_real_configuration_and_current_session(self):
        task, factory, opened, _, _ = self.operator_fixture()
        self.start(transport_factory=factory)
        config = self.server.real_config
        self.server.real_config = None
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.server.real_config = config
        run = self.store.one('SELECT session_id FROM runs WHERE id=?', (task['run_id'],))
        self.store.execute("UPDATE sessions SET state='REPLACED' WHERE id=?", (run['session_id'],))
        self.assertEqual(self.operator_generate(task['id'])[0], 400)
        self.assertEqual(opened, []); self.assertEqual(self.server.job_snapshot(), {})

    def test_operator_generation_duplicate_and_fixed_action_share_single_active_job(self):
        entered, release = Event(), Event()
        task, factory, opened, calls, _ = self.operator_fixture(entered=entered, release=release)
        self.start(transport_factory=factory)
        try:
            self.assertEqual(self.operator_generate(task['id'])[0], 202)
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.operator_generate(task['id'])[1]['resubmitted'])
            self.assertEqual(self.action('real_generate')[0], 409)
            self.assertEqual(self.action('real_approve')[0], 409)
        finally: release.set()
        self.assertEqual(self.job('operator_generate:' + task['id'])['result']['state'], 'GENERATED')
        self.assertFalse(self.operator_generate(task['id'])[1]['resubmitted'])
        # A fresh process job cache also respects the persisted terminal run.
        with self.server.job_lock: self.server.jobs.clear()
        self.assertEqual(self.operator_generate(task['id'])[0], 202)
        self.assertEqual(self.job('operator_generate:' + task['id'])['result']['existing_run']['state'], 'GENERATED')
        self.assertEqual(opened, [1]); self.assertEqual(sum(tool == 'Shortcut' for tool, _ in calls), 1)

    def test_operator_uncertain_generation_is_never_retried(self):
        task, _, _, _, _ = self.operator_fixture()
        opened = []
        def failed_factory():
            opened.append(1)
            raise TimeoutError('fixture outcome uncertain')
        self.start(transport_factory=failed_factory)
        self.assertEqual(self.operator_generate(task['id'])[0], 202)
        job = self.job('operator_generate:' + task['id'])
        self.assertEqual(job['result']['state'], 'REJECTED')
        self.assertEqual(job['result']['reason'], 'GENERATION_UNCERTAIN')
        self.assertFalse(self.operator_generate(task['id'])[1]['resubmitted'])
        self.assertEqual(opened, [1])

    def test_operator_stopped_before_queue_can_resume_and_stop_race_opens_no_transport(self):
        task, factory, opened, _, _ = self.operator_fixture()
        self.start(transport_factory=factory)
        self.action('stop')
        self.assertEqual(self.operator_generate(task['id'])[0], 409)
        self.assertEqual(self.server.job_snapshot(), {})
        self.action('resume')
        entered, release = Event(), Event()
        from tools.run_prepared_deepseek import run_existing
        def gated(*args, **kwargs):
            entered.set(); self.assertTrue(release.wait(3))
            return run_existing(*args, **kwargs)
        with patch('tools.run_prepared_deepseek.run_existing', side_effect=gated):
            try:
                self.assertEqual(self.operator_generate(task['id'])[0], 202)
                self.assertTrue(entered.wait(1))
                self.action('stop')
            finally: release.set()
            job = self.job('operator_generate:' + task['id'])
        self.assertEqual(job['state'], 'FAILED'); self.assertEqual(opened, [])
        self.action('resume')
        self.assertFalse(self.operator_generate(task['id'])[1]['resubmitted'])
        self.assertEqual(opened, [])


if __name__=='__main__': unittest.main()
