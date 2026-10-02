"""Local automatic sender lifecycle, using the existing Outbox and MCP process."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

from .delivery_tasks import AutomaticDelivery, RetryPolicy, collector_ack_validator
from .mcp_group_delivery import MCPGroupDesktop
from .storage import Store


class _Connection:
    """Start the trusted native connection only when a due task needs a UI read."""
    def __init__(self):
        self.process = None

    def call(self, tool, arguments):
        if self.process is None:
            from .mcp_transport import MCPProcess
            self.process = MCPProcess(bound_input_process='WXWork')
            self.process.__enter__()
        return self.process.call(tool, arguments)

    def close(self):
        if self.process is not None:
            self.process.__exit__(None, None, None)


class AutomaticDeliveryRuntime:
    def __init__(self, database, config=None, *, collector_config=None, desktop_factory=None):
        value = json.loads(Path(config).read_text(encoding='utf-8')) if isinstance(config, (str, Path)) else deepcopy(config)
        if value is None:
            value = {'enabled': False}
        if (not isinstance(value, dict) or type(value.get('enabled')) is not bool
                or set(value) - {'enabled', 'retry', 'desktop'}):
            raise ValueError('Explicit local automatic delivery configuration required')
        self.enabled = value['enabled']
        self.policy = RetryPolicy(**value.get('retry', {}))
        self.desktop_config = value.get('desktop')
        if self.enabled:
            MCPGroupDesktop.validate_config(self.desktop_config)
        self.database = Path(database)
        self.workspace = Path(__file__).resolve().parents[1]
        self.collector_config = deepcopy(collector_config or {})
        self.desktop_factory = desktop_factory
        self.pause_requested = Event()
        self.lock = Lock()
        self.pending_process = None
        self.last_result = None
        self.error_type = None

    def _validator(self):
        def validate(store, row):
            from .collector_storage import CollectorStore
            settings = self.collector_config.get('collector', {})
            source = settings.get('database')
            if not source or not Path(source).is_file():
                raise ValueError('ACTUAL_ACK_COLLECTOR_UNAVAILABLE')
            # The sender only reads existing collector tables; ingestion owns migrations.
            collector = CollectorStore.__new__(CollectorStore)
            collector.path = Path(source)
            return collector_ack_validator(collector,
                self_sender_ids=settings.get('self_sender_ids', ()),
                teacher_sender_ids=settings.get('teacher_sender_ids', ()))(store, row)
        return validate

    def _engine(self, store, connection):
        if self.desktop_factory:
            desktop = self.desktop_factory()
            if desktop.simulated is not True:
                raise ValueError('Injected automatic sender is for simulation only')
        else:
            desktop = MCPGroupDesktop(connection, self.desktop_config, self.workspace)
        return AutomaticDelivery(store, desktop, policy=self.policy,
                                 ack_source_validator=self._validator() if desktop.simulated is False else None,
                                 pause_requested=self.pause_requested.is_set)

    def snapshot(self, store):
        # This read-only task view never opens an MCP process or enables simulated sends.
        view = AutomaticDelivery(store, SimpleNamespace(simulated=True, lock_path=''), policy=self.policy).snapshot()
        groups = {row['group_key']: row['header_name'] for row in (self.desktop_config or {}).get('groups', [])}
        for task in view['ack_tasks'] + view['answer_tasks']:
            task['group_name'] = groups.get(task['group_key'], task['group_key'])
        real_count = store.one("""SELECT COUNT(*) FROM outbox WHERE simulated=0 AND state='SENT_UI_CONFIRMED'
            AND id IN (SELECT outbox_id FROM audit WHERE event='TASK_SEND_CONFIRMED')""")[0]
        return dict(view, enabled=self.enabled, simulation=bool(self.desktop_factory),
                    paused=self.pause_requested.is_set() or view_pause(store),
                    last_result=self.last_result, error_type=self.error_type,
                    real_confirmed_tasks=real_count,
                    native_call_pending=bool(self.pending_process and self.pending_process._proc
                        and self.pending_process._proc.poll() is None),
                    live_validation='DELIVERY_UI_VERIFIED' if real_count else 'NOT_VERIFIED', retry_policy=self.policy.__dict__)

    def _execute(self, action):
        connection = _Connection()
        try:
            with closing(Store(self.database)) as store:
                result = action(self._engine(store, connection))
            self.last_result, self.error_type = result, None
            return result
        finally:
            process = connection.process
            connection.close()
            if process is not None and process.uncertain:
                self.pending_process = process
                self.pause_requested.set()
                with closing(Store(self.database)) as store:
                    from .workflow import Workflow
                    Workflow(store).set_stop(True)
                self.error_type = 'NATIVE_CALL_UNCERTAIN'

    def tick(self):
        if not self.enabled:
            return {'state': 'DISABLED'}
        if self.pending_process is not None:
            return {'state': 'NEEDS_ATTENTION', 'reason': 'NATIVE_CALL_UNCERTAIN', 'automatic_retry_allowed': False}
        if not self.lock.acquire(timeout=.05):
            return {'state': 'DISPATCHER_BUSY'}
        try:
            return self._execute(lambda engine: engine.tick())
        finally:
            self.lock.release()

    def control(self, store, action, *, outbox_id=None):
        from .workflow import Workflow
        if action == 'pause':
            self.pause_requested.set()
            Workflow(store).set_stop(True)
            return {'state': 'STOPPED'}
        if self.pending_process is not None:
            child = self.pending_process._proc
            if child is not None and child.poll() is None:
                raise ValueError('Native call still pending; wait for the process to exit before resuming')
            self.pending_process = None
        if action == 'resume':
            Workflow(store).set_stop(False)
            self.pause_requested.clear()
            return {'state': 'RESUMED'}
        if action == 'approve' and self.enabled and isinstance(outbox_id, str) and 0 < len(outbox_id) <= 128:
            Workflow(store).approve(outbox_id, reviewer='local_automatic_delivery_operator')
            return {'state': 'APPROVED', 'task_id': outbox_id}
        if action == 'inspect' and self.enabled and isinstance(outbox_id, str) and 0 < len(outbox_id) <= 128:
            with self.lock:
                return self._execute(lambda engine: {'state': engine.flow.inspect_unknown(outbox_id), 'task_id': outbox_id})
        raise ValueError('Use pause, resume, approve or read-only inspect of an existing task')


def view_pause(store):
    return store.one("SELECT value FROM settings WHERE key='stop_requested'")[0] == 'true'
