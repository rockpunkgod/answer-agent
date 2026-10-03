"""Opt-in local execution of the existing reviewed-question queue.

Luna locates controls; the existing page, preparation and generation contracts
decide which actions are allowed. This runtime never sends WeCom messages.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Event, Lock
import time
from types import SimpleNamespace

from . import luna_navigation
from .call_costs import RunMeter
from .locking import resource_lock
from .mcp_page_contract import DeepSeekPage, PageUnconfirmed, snapshot_text
from .mcp_transport import MCPProcess
from .storage import Store, encode, now


class _AnswerConnection(MCPProcess):
    def __init__(self, runtime):
        super().__init__(bound_input_process='msedge')
        self.runtime = runtime
        self.held_lock = None

    def call(self, tool, args, **kwargs):
        # Share the existing desktop lock with delivery, but release it between
        # observations while DeepSeek is generating. Never hold a DB transaction.
        guard = resource_lock(self.runtime.workspace / 'data/windows-interactive-desktop.lock')
        guard.__enter__()
        try:
            self.runtime.require_running()
            return super().call(tool, args, **kwargs)
        except BaseException:
            if self.uncertain:
                self.held_lock = guard
                guard = None
                self.runtime.quarantine(self)
            raise
        finally:
            if guard is not None:
                guard.__exit__(None, None, None)


class AutomaticAnswerRuntime:
    def __init__(self, database, config=None, *, manifest=None, reference_config=None,
                 transport_factory=None, navigator=None):
        value = (json.loads(Path(config).read_text(encoding='utf-8'))
                 if isinstance(config, (str, Path)) else deepcopy(config))
        value = {'enabled': False} if value is None else value
        if (not isinstance(value, dict) or type(value.get('enabled')) is not bool
                or set(value) - {'enabled', 'display_index', 'new_chat_button', 'preparation_controls'}):
            raise ValueError('Explicit local automatic answer configuration required')
        self.enabled = value['enabled']
        self.database = Path(database)
        self.workspace = Path(__file__).resolve().parents[1]
        self.manifest, self.reference_config = manifest, reference_config
        self.config = value
        self.transport_factory, self.navigator = transport_factory, navigator
        if navigator is not None and transport_factory is None:
            raise ValueError('Injected navigation requires a simulated transport')
        self.simulation = bool(transport_factory or navigator)
        self.pause_requested, self.lock = Event(), Lock()
        self.pending_process = None
        self.current_transport = None
        self.last_result = None
        self.error_type = None
        if self.enabled:
            controls = value.get('preparation_controls')
            if (type(value.get('display_index')) is not int or value['display_index'] < 0
                    or not isinstance(controls, dict)
                    or controls.get('preparation_mode') != 'VERIFY_THEN_TEACH'
                    or 'display_index' in controls):
                raise ValueError('Automatic answers require a display and VERIFY_THEN_TEACH controls')
            name = value.get('new_chat_button')
            if (not isinstance(name, str) or not name.strip() or len(name) > 100
                    or any(c in name for c in ('\r', '\n', '"', '\x00'))):
                raise ValueError('A reviewed literal new-chat button name is required')
            from .teaching_bundle import verify_bundle
            verify_bundle(manifest, for_generation=True)
            self.manifest_sha256 = sha256(Path(manifest).read_bytes()).hexdigest()

    def require_running(self):
        if self.pause_requested.is_set() or self.pending_process is not None:
            raise ValueError('AUTOMATIC_ANSWER_PAUSED')
        with closing(Store(self.database.resolve(strict=True))) as store:
            stop = store.one("SELECT value FROM settings WHERE key='stop_requested'")
        if stop and stop[0] == 'true':
            raise ValueError('AUTOMATIC_ANSWER_PAUSED')

    def quarantine(self, process):
        self.pending_process = process
        self.pause_requested.set()
        self.error_type = 'NATIVE_CALL_UNCERTAIN'
        with closing(Store(self.database.resolve(strict=True))) as store:
            from .workflow import Workflow
            Workflow(store).set_stop(True)

    def control(self, action):
        if action == 'pause':
            self.pause_requested.set()
        elif action == 'resume':
            if self.pending_process is not None:
                child = self.pending_process._proc
                if child is not None and child.poll() is None:
                    raise ValueError('Answer native call still pending; wait before resuming')
                if self.pending_process.held_lock is not None:
                    self.pending_process.held_lock.__exit__(None, None, None)
                    self.pending_process.held_lock = None
                self.pending_process = None
            self.pause_requested.clear()
        else:
            raise ValueError('Use pause or resume')

    def snapshot(self, store=None):
        stop = store.one("SELECT value FROM settings WHERE key='stop_requested'") if store else None
        return {'enabled': self.enabled, 'simulation': self.simulation,
                'paused': self.pause_requested.is_set() or bool(stop and stop[0] == 'true'),
                'busy': self.lock.locked(),
                'last_result': self.last_result, 'error_type': self.error_type,
                'native_call_pending': bool(self.pending_process and self.pending_process._proc
                    and self.pending_process._proc.poll() is None),
                'live_validation': 'NOT_VERIFIED'}

    @contextmanager
    def _connection(self):
        if self.current_transport is not None:
            yield self.current_transport
            return
        connection = self.transport_factory() if self.transport_factory else _AnswerConnection(self)
        with connection as transport:
            if self.simulation and (isinstance(transport, MCPProcess) or getattr(transport, 'simulated', None) is not True):
                raise ValueError('Injected automatic answer transport must be explicitly simulated')
            yield transport

    def _course(self, task):
        if (task['manifest_sha256'] != self.manifest_sha256
                or sha256(Path(self.manifest).read_bytes()).hexdigest() != self.manifest_sha256):
            raise ValueError('AUTOMATIC_ANSWER_MANIFEST_CHANGED')

    def _observe(self, transport):
        return transport.call('Snapshot', {'use_dom': True, 'use_vision': False,
                                          'display': [self.config['display_index']]})

    def _page(self, observed, *, expected_url=None):
        text = snapshot_text(observed)
        tree = text.split('UI Tree:', 1)
        if len(tree) != 2:
            raise PageUnconfirmed('NO_ACTIVE_TREE')
        first = re.search(r'window "([^\n]+)"', tree[1])
        urls = re.findall(r'文档 .*?\[value:"(https://chat\.deepseek\.com/[^"\n]*)"\]', tree[1])
        if (not first or not re.search(r'Microsoft\u200b? Edge', first[1]) or len(urls) != 1):
            raise PageUnconfirmed('DEEPSEEK_PAGE_NOT_UNIQUE')
        controls = re.findall(r'(?:按钮|编辑) "([^"\n]+)"', tree[1])
        if any(name in ('登录', '登录 / 注册', '登录/注册', 'Sign in', 'Log in')
               or re.search(r'验证码|安全验证|captcha', name, re.I) for name in controls):
            raise PageUnconfirmed('LOGIN_OR_VERIFICATION_REQUIRED')
        # The root is observable only for locating New chat, never for upload,
        # matching, generation or claiming a business session.
        url = urls[0]
        if url == 'https://chat.deepseek.com/' and expected_url is None:
            page = None
            self._region(text)
        else:
            page = DeepSeekPage(url, self.config['display_index'])
            page.inspect(observed)
            if expected_url is not None and url != expected_url:
                raise PageUnconfirmed('CONVERSATION_MISMATCH')
        editors = re.findall(r'\((-?\d+),(-?\d+)\) 编辑 "给 DeepSeek 发送消息"([^\n]*)', tree[1])
        if len(editors) != 1 or '[value:' in editors[0][2] or '正在思考' in tree[1]:
            raise PageUnconfirmed('EDITOR_NOT_EMPTY_OR_AMBIGUOUS')
        return page, url, tree[1]

    def _region(self, text):
        return DeepSeekPage.display_region(SimpleNamespace(display_index=self.config['display_index']), text)

    def _point(self, text, point):
        point = list(map(int, point))
        left, top, right, bottom = self._region(text)
        if not (left <= point[0] < right and top <= point[1] < bottom):
            raise PageUnconfirmed('ACTION_OUTSIDE_SELECTED_DISPLAY')
        return point

    def _locate_and_click(self, transport, store, snapshot, attempt_id, *, new_chat=False, expected_url=None):
        before = self._observe(transport)
        page, url, tree = self._page(before, expected_url=expected_url)
        name = self.config['new_chat_button'] if new_chat else '给 DeepSeek 发送消息'
        role = '按钮' if new_chat else '编辑'
        pattern = r'\((-?\d+),(-?\d+)\) ' + role + ' "' + re.escape(name) + '"[^\n]*'
        points = re.findall(pattern, tree)
        if len(points) != 1:
            raise PageUnconfirmed('DEEPSEEK_CONTROL_NOT_UNIQUE')
        point = self._point(snapshot_text(before), points[0])
        screenshot = transport.call('Screenshot', {'display': [self.config['display_index']]})
        attempt = screenshot.get('attempt_id', '')
        if not isinstance(attempt, str) or not re.fullmatch('[0-9a-f]{32}', attempt):
            raise ValueError('LUNA_NATIVE_JOURNAL_REQUIRED')
        archive = (self.workspace / 'data/private/windows-mcp').resolve(strict=True)
        journal = json.loads((archive / ('attempt-' + attempt + '.json')).read_text(encoding='utf-8'))
        record_path = Path(journal['result_path']).resolve(strict=True)
        if not record_path.is_relative_to(archive) or json.loads(record_path.read_text(encoding='utf-8')) != screenshot:
            raise ValueError('LUNA_NATIVE_JOURNAL_CHANGED')
        luna_navigation._source(record_path, self.config['display_index'])
        if self.navigator is None and not luna_navigation.shutil.which('codex'):
            raise RuntimeError('LUNA_CODEX_NOT_INSTALLED')
        action = 'DEEPSEEK_NEW_CHAT' if new_chat else 'DEEPSEEK_COMPOSER'
        meter = RunMeter(store.path, snapshot['run_id'], injected=self.simulation)
        call = meter.start('SCHEDULER', luna_navigation.MODEL, action, attempt_id)
        try:
            proposal = (self.navigator or luna_navigation.suggest)(record_path,
                'Locate only the visible DeepSeek ' + role + ' named "' + name + '". '
                'Do not inspect or judge the English question.', [action, 'STOP'],
                display_index=self.config['display_index'])
        except BaseException:
            meter.finish(call, 'UNKNOWN', 'luna-navigation-result-unconfirmed')
            raise
        meter.finish(call, 'CONFIRMED', 'luna-navigation-structured-result')
        meter.close('SCHEDULER', 'reviewed-queue-navigation')
        self.require_running()
        after = self._observe(transport)
        current, current_url, tree = self._page(after, expected_url=None if page is None else url)
        new_points = re.findall(pattern, tree)
        if (current_url != url or len(new_points) != 1 or self._point(snapshot_text(after), new_points[0]) != point
                or list(self._region(snapshot_text(after))) != proposal.get('display_region')
                or proposal.get('action') != action or proposal.get('loc') is None
                or len(proposal['loc']) != 2 or any(type(p) is not int for p in proposal['loc'])
                or any(abs(a - b) > 32 for a, b in zip(proposal['loc'], point))):
            raise PageUnconfirmed('LUNA_CONTROL_OR_LAYOUT_UNCONFIRMED')
        # The model's position never becomes the actual input target.
        from .mcp_generation import PreparedDeepSeekGenerator
        PreparedDeepSeekGenerator(None, '', '', store_path=store.path)._verify_current_input(snapshot)
        result = transport.call('Click', {'loc': point})
        if result.get('is_error') is True:
            raise PageUnconfirmed('DEEPSEEK_CLICK_UNCONFIRMED')
        return current_url

    def _session(self, *, store, task, snapshot, attempt_id):
        self._course(task)
        with self._connection() as transport:
            previous = self._locate_and_click(transport, store, snapshot, attempt_id, new_chat=True)
            for index in range(3):
                self.require_running()
                observed = self._observe(transport)
                _, url, _ = self._page(observed)
                if url != previous and url != 'https://chat.deepseek.com/':
                    DeepSeekPage(url, self.config['display_index']).stage_action(observed, 'probe')
                    if store.one('SELECT session_id FROM deepseek_chats WHERE session_url=?', (url,)):
                        raise PageUnconfirmed('NEW_CHAT_ALREADY_OWNED')
                    with store.transaction():
                        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                            (snapshot['run_id'], 'LUNA_NEW_CHAT_OBSERVED',
                             encode({'attempt_id': attempt_id, 'session_url': url, 'observation': observed,
                                     'simulation': self.simulation}), now()))
                    return url
                if index < 2:
                    time.sleep(1)
        # No synthetic UUID and no first prompt just to manufacture a URL.
        raise PageUnconfirmed('NEW_CHAT_EXACT_URL_UNCONFIRMED')

    def _prepare(self, *, store, task, snapshot, attempt_id, session_url, candidate_path):
        self._course(task)
        from .question_matching import prepare_input
        from .reference_lookup import LookupConfig, ReferenceLookup
        from .mcp_preparation import DeepSeekSessionPreparer
        lookup = None if snapshot.get('intent') == 'FOLLOWUP' else ReferenceLookup(LookupConfig.load(
            self.reference_config or self.workspace / 'config/reference-lookup.example.toml'))
        snapshot = prepare_input(store, snapshot['run_id'], lookup)
        controls = dict(self.config['preparation_controls'], display_index=self.config['display_index'])
        with self._connection() as transport:
            self._locate_and_click(transport, store, snapshot, attempt_id, expected_url=session_url)
            return DeepSeekSessionPreparer(transport, snapshot, session_url, candidate_path,
                                           controls, store_path=store.path).run()

    def _generate(self, *, store, task, snapshot, attempt_id):
        self._course(task)
        from tools.run_prepared_deepseek import run_existing
        owned = store.one('SELECT session_url FROM deepseek_chats WHERE session_id=?', (snapshot['session_id'],))
        if owned is None:
            raise ValueError('QUEUE_CHAT_OWNERSHIP_MISSING')
        with self._connection() as transport:
            self._locate_and_click(transport, store, snapshot, attempt_id, expected_url=owned[0])
            # run_existing owns its context manager. Reuse the already guarded
            # connection without entering or closing its native process twice.
            from contextlib import nullcontext
            return run_existing(store, snapshot['run_id'], task['preparation_path'], task['manifest_path'],
                evidence_dir=task['evidence_dir'], transport_factory=lambda: nullcontext(transport))

    def _execute_ready(self, store, queued):
        from .reviewed_question_queue import advance, get, _phase
        task_id = queued['task_id']
        # Recover persisted facts and validate source BEFORE opening the desktop.
        queued = advance(store, task_id)
        stages = {'WAITING_DESKTOP_EXECUTOR': 'SESSION_CREATION', 'READY_FOR_PREPARATION': 'PREPARATION',
                  'ATTACHMENTS_READY': 'GENERATION'}
        stage = stages.get(queued['phase'])
        if stage is None:
            return queued
        task = dict(store.one('SELECT * FROM operator_tasks WHERE id=?', (task_id,)))
        try:
            self._course(task)
        except ValueError:
            _phase(store, task_id, 'NEEDS_ATTENTION', reason='AUTOMATIC_ANSWER_MANIFEST_CHANGED')
            return get(store, task_id)
        waits = [json.loads(row[0]) for row in store.all(
            "SELECT details FROM audit WHERE run_id=? AND event='ANSWER_PAGE_PREFLIGHT'", (queued['run_id'],))]
        failures = sum(item['stage'] == stage and item['state'] != 'READY' for item in waits)
        if failures >= 3:
            _phase(store, task_id, 'NEEDS_ATTENTION', reason='PAGE_WAIT_LIMIT_REACHED')
            return get(store, task_id)
        owned = store.one('SELECT c.session_url FROM deepseek_chats c JOIN runs r ON r.session_id=c.session_id WHERE r.id=?',
                          (queued['run_id'],))
        details = {'task_id': task_id, 'stage': stage, 'state': 'STARTED', 'simulation': self.simulation}
        with store.transaction():
            identifier = store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                (queued['run_id'], 'ANSWER_PAGE_PREFLIGHT', encode(details), now())).lastrowid
        try:
            with self._connection() as transport:
                self._page(self._observe(transport), expected_url=owned[0] if owned else None)
                details['state'] = 'READY'
                with store.transaction():
                    store.execute('UPDATE audit SET details=? WHERE id=?', (encode(details), identifier))
                self.current_transport = transport
                try:
                    return advance(store, task_id, executor='LUNA', session_creator=self._session,
                                   preparer=self._prepare, generator=self._generate)
                finally:
                    self.current_transport = None
        except Exception as exc:
            if details['state'] == 'READY':
                # Execution has its own durable attempt. Never downgrade its
                # uncertainty to a safely retryable preflight failure.
                raise
            details.update(state='UNAVAILABLE', error_type=type(exc).__name__)
            with store.transaction():
                store.execute('UPDATE audit SET details=? WHERE id=?', (encode(details), identifier))
            verification = isinstance(exc, PageUnconfirmed) and str(exc) == 'LOGIN_OR_VERIFICATION_REQUIRED'
            if verification:
                self.pause_requested.set()
                from .workflow import Workflow
                Workflow(store).set_stop(True)
            _phase(store, task_id, 'NEEDS_ATTENTION' if failures == 2 or self.pending_process or verification else 'WAITING_FOR_PAGE',
                   reason='LOGIN_OR_VERIFICATION_REQUIRED' if verification else
                          'PAGE_WAIT_LIMIT_REACHED' if failures == 2 else 'PAGE_NOT_READY')
            return get(store, task_id)

    def tick(self):
        if not self.enabled:
            return {'state': 'DISABLED'}
        if not self.lock.acquire(blocking=False):
            return {'state': 'EXECUTOR_BUSY'}
        try:
            self.require_running()
            from .reviewed_question_queue import list_queue
            with closing(Store(self.database.resolve(strict=True))) as store:
                eligible = {row[0] for row in store.all('SELECT id FROM operator_tasks WHERE label=?',
                    ('SOURCE_MESSAGE',))} if not self.simulation and store.one(
                        "SELECT name FROM sqlite_master WHERE name='operator_tasks'") else None
                candidates = [q for q in list_queue(store) if (eligible is None or q['task_id'] in eligible) and q['phase'] in (
                    'WAITING_DESKTOP_EXECUTOR', 'READY_FOR_PREPARATION', 'ATTACHMENTS_READY', 'WAITING_FOR_PAGE',
                    'SESSION_CREATION_STARTED', 'PREPARATION_STARTED', 'GENERATION_STARTED')]
                if not candidates:
                    return {'state': 'IDLE'}
                task_id = candidates[0]['task_id']
                # Production only executes tasks admitted from real source
                # review. Injected anonymous fixtures remain marked simulation.
                task = store.one('SELECT label FROM operator_tasks WHERE id=?', (task_id,))
                if not self.simulation and (task is None or task['label'] != 'SOURCE_MESSAGE'):
                    raise ValueError('AUTOMATIC_ANSWER_REQUIRES_SOURCE_MESSAGE')
                outcome = self._execute_ready(store, candidates[0])
                self.last_result = {'task_id': task_id, 'state': outcome['phase'], 'reason': outcome['last_error']}
                self.error_type = None if self.pending_process is None else 'NATIVE_CALL_UNCERTAIN'
                return self.last_result
        finally:
            self.lock.release()
