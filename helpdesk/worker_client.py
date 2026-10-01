"""Pull-only business client and local execution cache, never a business ledger."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .locking import resource_lock
from .storage import ensure_local_database

from .worker_contracts import (ExecutionStatus, SideEffect, WorkerAuthorization, WorkerCommand,
                              WorkerControl, WorkerHealth, WorkerPolicy, WorkerResult, reference)


class BusinessAPIError(RuntimeError):
    """Diagnostic code only. Never expose transport errors or bearer tokens."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BusinessAPIError('REDIRECT_FORBIDDEN')


class HTTPBusinessClient:
    def __init__(self, base_url, *, token_env='HELPDESK_WORKER_TOKEN', timeout=10):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or not 0 < timeout <= 60):
            raise ValueError('Invalid business endpoint')
        try:
            parsed.port
        except ValueError:
            raise ValueError('Invalid business endpoint') from None
        local = parsed.hostname.lower() in {'127.0.0.1', 'localhost'}
        if parsed.scheme == 'http' and not local:
            raise ValueError('Remote business endpoint requires HTTPS')
        token = os.environ.get(token_env, '')
        if (not local and not token) or any(c in token for c in ('\r', '\n', '\x00')):
            raise ValueError('Valid environment bearer token required')
        self.base_url, self.timeout, self._token = base_url.rstrip('/'), timeout, token
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path, payload=None, *, max_response=262144):
        headers = {'Content-Type': 'application/json'}
        if self._token:
            headers['Authorization'] = 'Bearer ' + self._token
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') if payload is not None else None
        try:
            with self._opener.open(Request(self.base_url + path, data=body, headers=headers,
                                          method='POST' if body is not None else 'GET'), timeout=self.timeout) as response:
                if response.status != 200:
                    raise BusinessAPIError('API_RESPONSE_REJECTED')
                raw = response.read(max_response+1)
                if len(raw) > max_response:
                    raise BusinessAPIError('API_RESPONSE_TOO_LARGE')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise BusinessAPIError('API_RESPONSE_INVALID')
            return value
        except Exception:
            raise BusinessAPIError('BUSINESS_API_UNAVAILABLE_OR_INVALID') from None

    def heartbeat(self, health):
        return self._request('/api/worker/heartbeat', health.to_dict() if isinstance(health, WorkerHealth) else health)

    def pull(self, worker_id):
        return self._request('/api/worker/pull', {'worker_id': reference(worker_id)})

    def authorize(self, worker_id, command_id, lease_id, execution_epoch):
        return self._request('/api/worker/authorize', {'worker_id': worker_id, 'command_id': command_id,
                            'lease_id': lease_id, 'execution_epoch': execution_epoch})

    def result(self, worker_id, lease_id, result):
        return self._request('/api/worker/result', {'worker_id': worker_id, 'lease_id': lease_id,
                            'result': result.to_dict() if isinstance(result, WorkerResult) else result})

    def command(self, worker_id, command_id):
        return self._request('/api/worker/command?' + urlencode({'command_id': command_id, 'worker_id': worker_id}))

    def worker_state(self,worker_id):
        return self._request('/api/worker/status?' + urlencode({'worker_id':reference(worker_id)}))

    def resource(self,worker_id,command_id,lease_id,resource_ref):
        """Fixed approved resource, never a server path or arbitrary URL."""
        from hashlib import sha256
        import base64
        value=self._request('/api/worker/resource?' + urlencode({
            'worker_id':reference(worker_id),'command_id':reference(command_id),
            'lease_id':reference(lease_id),'resource_ref':reference(resource_ref)}),max_response=34_000_000)
        if (set(value)!={'resource_ref','kind','sha256','encoding','content'} or value['resource_ref']!=resource_ref
                or value['encoding']!='base64' or value['kind'] not in {'INPUT','ATTACHMENT','OUTBOX_CONTENT'}):
            raise BusinessAPIError('BOUND_RESOURCE_REQUIRED')
        try:
            content=base64.b64decode(value['content'],validate=True)
        except Exception:
            raise BusinessAPIError('RESOURCE_BYTES_INVALID') from None
        if not 0<len(content)<=25_000_000 or sha256(content).hexdigest()!=value['sha256']:
            raise BusinessAPIError('RESOURCE_HASH_MISMATCH')
        return {'resource_ref':resource_ref,'kind':value['kind'],'sha256':value['sha256'],'content':content}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class _JournalGuard:
    def __init__(self, guard, journal, command_id):
        self.guard, self.journal, self.command_id = guard, journal, command_id

    def __getattr__(self, name):
        return getattr(self.guard, name)

    def call(self, fn, *, read_only=False, refresh=False):
        # Record intent immediately before the guarded native invocation. If
        # authorization denies it, the runtime's final result preserves that fact.
        with self.journal:
            self.journal.execute('UPDATE execution_journal SET native_intent=1,updated_at=? WHERE command_id=?',
                                 (_now(), self.command_id))
        return self.guard.call(fn, read_only=read_only, refresh=refresh)


class WorkerClient:
    def __init__(self, api, worker_id, journal_path, *, runtime, health_provider, handler):
        self.api, self.worker_id = api, reference(worker_id)
        self.runtime, self.health_provider, self.handler = runtime, health_provider, handler
        path = Path(ensure_local_database(journal_path))
        self.lock_path = str(path.resolve()) + '.client.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.journal = sqlite3.connect(path)
        self.journal.row_factory = sqlite3.Row
        tables = {row[0] for row in self.journal.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {'execution_journal'}:
            self.journal.close()
            raise ValueError('Worker cache cannot use a business database')
        self.journal.execute('PRAGMA synchronous=FULL')
        self.journal.execute('''CREATE TABLE IF NOT EXISTS execution_journal(
            command_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            execution_epoch INTEGER NOT NULL, command_json TEXT NOT NULL, lease_id TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT, server_ack INTEGER NOT NULL DEFAULT 0,
            native_intent INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)''')
        self.journal.commit()

    def close(self):
        self.journal.close()

    def _unknown(self, command, *, started, reason='RESTART_REQUIRES_RECONCILIATION', pending=False):
        return WorkerResult(1, command.command_id, command.task_id, command.outbox_id,
            command.worker_id, command.account_id, command.execution_epoch, command.question_version,
            command.context_revision, ExecutionStatus.OUTCOME_UNKNOWN, SideEffect.UNKNOWN,
            started, pending, 'LOCAL_RECOVERY', reason, _now(), ())

    def _save_result(self, command, result):
        result.validate(command)
        if result.side_effect == SideEffect.UNKNOWN and result.status != ExecutionStatus.OUTCOME_UNKNOWN:
            result = self._unknown(command, started=result.action_started,
                                   pending=result.native_call_pending, reason='SIDE_EFFECT_REQUIRES_RECONCILIATION')
        with self.journal:
            self.journal.execute('UPDATE execution_journal SET status=?,result_json=?,server_ack=0,updated_at=? WHERE command_id=?',
                (str(result.status), _encoded(result.to_dict()), _now(), command.command_id))
        return result

    def recover_pending(self):
        """No native execution: reconcile server identity and upload cached results."""
        with resource_lock(self.lock_path, timeout=1):
            reader=getattr(self.api,'worker_state',None)
            allow_health=True
            if callable(reader):
                try:
                    state=reader(self.worker_id)
                    allow_health=state.get('worker_id')==self.worker_id and state.get('desktop_reads_allowed') is True
                except Exception:allow_health=False
            return self._recover_pending(allow_health=allow_health)

    def _recover_pending(self, *, allow_health=True):
        rows = self.journal.execute('SELECT * FROM execution_journal ORDER BY updated_at,command_id').fetchall()
        blocked = False
        for row in rows:
            command = WorkerCommand.from_dict(json.loads(row['command_json']))
            if row['status'] == ExecutionStatus.RUNNING:
                self._save_result(command, self._unknown(command, started=bool(row['native_intent']),
                                                         pending=bool(row['native_intent'])))
                row = self.journal.execute('SELECT * FROM execution_journal WHERE command_id=?', (command.command_id,)).fetchone()
            uncertain = row['status'] in {ExecutionStatus.OUTCOME_UNKNOWN, ExecutionStatus.WAITING_HUMAN}
            if row['server_ack'] and not uncertain:
                continue
            try:
                state = self.api.command(self.worker_id, command.command_id)
                server_status = ExecutionStatus(state['status'])
                if type(state.get('current')) is not bool:
                    raise BusinessAPIError('INVALID_COMMAND_STATUS')
                if not row['server_ack']:
                    result = WorkerResult.from_dict(json.loads(row['result_json']))
                    acknowledgement = self.api.result(self.worker_id, row['lease_id'], result)
                    if (acknowledgement.get('accepted') is not True
                            or acknowledgement.get('command_id') != command.command_id
                            or type(acknowledgement.get('duplicate')) is not bool):
                        raise BusinessAPIError('RESULT_NOT_ACKNOWLEDGED')
                    with self.journal:
                        # Acceptance acknowledges receipt, not independently
                        # verified completion. A failed status query must retain
                        # a local recovery barrier even for claimed success.
                        self.journal.execute("UPDATE execution_journal SET server_ack=1,status='OUTCOME_UNKNOWN',updated_at=? WHERE command_id=?",
                                             (_now(), command.command_id))
                    uncertain = True
                    state = self.api.command(self.worker_id, command.command_id)
                    server_status = ExecutionStatus(state['status'])
                    if type(state.get('current')) is not bool:
                        raise BusinessAPIError('INVALID_COMMAND_STATUS')
                if uncertain:
                    health = self.health_provider() if allow_health else None
                    if (health is None or health.native_call_pending or server_status not in {ExecutionStatus.SUCCEEDED_VERIFIED,
                            ExecutionStatus.FAILED_BEFORE_ACTION, ExecutionStatus.FAILED_AFTER_ACTION}):
                        blocked = True
                    else:
                        with self.journal:
                            self.journal.execute('UPDATE execution_journal SET status=?,updated_at=? WHERE command_id=?',
                                                 (str(server_status), _now(), command.command_id))
            except Exception:
                blocked = True
        return not blocked

    def _control(self, command, lease_id):
        try:
            response = self.api.authorize(self.worker_id, command.command_id, lease_id, command.execution_epoch)
            return WorkerControl.from_dict(response['control'])
        except Exception:
            # Defaults deny lease/version/approval as well as quarantining writes.
            return WorkerControl(stop=True, resources_quarantined=True)

    def run_once(self):
        try:
            with resource_lock(self.lock_path, timeout=1):
                return self._run_once()
        except TimeoutError:
            return {'state': 'LOCAL_EXECUTOR_BUSY', 'executed': False}

    def _run_once(self):
        state_reader=getattr(self.api,'worker_state',None)
        if callable(state_reader):
            try:
                state=state_reader(self.worker_id)
                if state.get('worker_id')!=self.worker_id or type(state.get('desktop_reads_allowed')) is not bool:
                    raise ValueError('Bound worker state required')
                if not state['desktop_reads_allowed']:
                    # Flushing persisted facts is safe; acquiring another
                    # screenshot/clipboard/health observation is not.
                    self._recover_pending(allow_health=False)
                    return {'state':'WAITING_HUMAN_OR_PAUSED','executed':False}
            except Exception:
                return {'state':'OFFLINE_OR_REJECTED','executed':False}
        try:
            health = self.health_provider()
            if not isinstance(health, WorkerHealth) or health.worker_id != self.worker_id:
                return {'state': 'WAITING_HEALTH', 'executed': False}
            if self.api.heartbeat(health).get('accepted') is not True:
                raise BusinessAPIError('HEARTBEAT_NOT_ACCEPTED')
            # Unhealthy accounts must reach server-wide pause logic even when
            # this node cannot execute. Cached results may still be uploaded.
            if not self._recover_pending():
                return {'state': 'PENDING_RECOVERY', 'executed': False}
            if not health.gui_ready:
                return {'state': 'WAITING_HEALTH', 'executed': False}
            package = self.api.pull(self.worker_id)
            if package.get('command') is None:
                return {'state': 'IDLE', 'executed': False}
            if set(package) != {'command', 'authorization', 'policy', 'lease_id'}:
                raise BusinessAPIError('INVALID_PULL_PACKAGE')
            command = WorkerCommand.from_dict(package['command'])
            authorization = WorkerAuthorization.from_dict(package['authorization'])
            policy = WorkerPolicy.from_dict(package['policy'])
            lease_id = reference(package['lease_id'])
            if command.worker_id != self.worker_id:
                raise BusinessAPIError('WORKER_BINDING_MISMATCH')
            authorization.validate(command)
            policy.validate(command)
            health.validate(command)
            current = self.api.command(self.worker_id, command.command_id)
            if current.get('current') is not True or current.get('status') != ExecutionStatus.NOT_STARTED:
                return {'state': 'STALE_OR_ALREADY_STARTED', 'executed': False}
            prior = self.journal.execute('SELECT * FROM execution_journal WHERE command_id=? OR idempotency_key=?',
                                        (command.command_id, command.idempotency_key)).fetchone()
            if prior:
                return {'state': 'DUPLICATE_NOT_REPLAYED', 'executed': False}
            with self.journal:
                self.journal.execute('INSERT INTO execution_journal(command_id,idempotency_key,execution_epoch,command_json,lease_id,status,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (command.command_id, command.idempotency_key, command.execution_epoch,
                     _encoded(command.to_dict()), lease_id, str(ExecutionStatus.RUNNING), _now()))
        except Exception:
            return {'state': 'OFFLINE_OR_REJECTED', 'executed': False}
        def trusted_handler(bound_command, guard):
            return self.handler(bound_command, _JournalGuard(guard, self.journal, command.command_id))
        try:
            result = self.runtime.execute(command, authorization, policy,
                control_provider=lambda: self._control(command, lease_id), health_provider=self.health_provider,
                handler=trusted_handler)
            if not isinstance(result, WorkerResult):
                raise ValueError('Bound WorkerResult required')
            result = self._save_result(command, result)
        except Exception:
            row = self.journal.execute('SELECT native_intent FROM execution_journal WHERE command_id=?', (command.command_id,)).fetchone()
            result = self._unknown(command, started=bool(row[0]), pending=bool(row[0]),
                                   reason='EXECUTOR_RESULT_UNCONFIRMED')
            self._save_result(command, result)
        returned = self._recover_pending()
        return {'state': str(result.status) if returned else 'PENDING_RECOVERY',
                'command_id': command.command_id, 'executed': result.action_started,
                'result_pending': not returned}
