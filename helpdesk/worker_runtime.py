"""Bounded per-action worker guard; no native actor or business Store is owned here."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .locking import resource_lock
from .worker_environment import ProfileManager
from .worker_contracts import (Action, ExecutionStatus, HealthState, READ_ACTIONS, WRITE_ACTIONS,
    SideEffect, TakeoverState, WorkerControl, WorkerHealth, WorkerResult, instant, utc_now)


class SafeReadFailure(RuntimeError):
    """Trusted adapter explicitly certifies the failed read's native call ended."""


class GuardBlocked(RuntimeError):
    def __init__(self, reason, status=ExecutionStatus.WAITING_HUMAN):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class WorkerRuntime:
    def __init__(self, resource_root, *, native_timeout_seconds=45):
        if str(resource_root).startswith(('\\\\', '//')):
            raise ValueError('LOCAL_WORKER_RESOURCE_ROOT_REQUIRED')
        if type(native_timeout_seconds) not in (int, float) or not 0 < native_timeout_seconds <= 60:
            raise ValueError('BOUNDED_NATIVE_TIMEOUT_REQUIRED')
        self.native_timeout_seconds = native_timeout_seconds
        self.root = Path(resource_root).resolve()
        self.state_root = self.root / 'data' / 'worker-runtime'
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.desktop_lock = self.root / 'data' / 'windows-interactive-desktop.lock'
        self.state_lock = self.state_root / 'state.lock'
        self.profiles = ProfileManager(self.root / 'data' / 'worker-profiles')

    def _path(self, kind, key):
        return self.state_root / (kind + '-' + hashlib.sha256(key.encode()).hexdigest() + '.json')

    def _read(self, kind, key):
        p = self._path(kind, key)
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

    def _write(self, kind, key, value):
        p = self._path(kind, key)
        temporary = p.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(p)

    def _checkpoint(self, guard, code, *, pending=False, result=None):
        c = guard.command
        value = {'command_id': c.command_id, 'task_id': c.task_id, 'account_id': c.account_id,
                 'worker_id': c.worker_id,
                 'profile_id': c.profile_id, 'session_id': c.session_id,
                 'idempotency_key': c.idempotency_key, 'execution_epoch': c.execution_epoch,
                 'command_sha256': hashlib.sha256(json.dumps(c.to_dict(), sort_keys=True).encode()).hexdigest(),
                 'checkpoint': code, 'native_call_pending': pending,
                 'write_started': guard.write_started, 'tool_calls': guard.calls,
                 'state_reads': guard.reads, 'refreshes': guard.refreshes,
                 'observed_at': utc_now().isoformat()}
        if result:
            value['result'] = result.to_dict()
        with resource_lock(self.state_lock):
            self._write('command', c.command_id, value)
            bound = self._read('idempotency', c.idempotency_key)
            if not bound:
                self._write('idempotency', c.idempotency_key, {'command_id': c.command_id})
            if pending or result and result.status == ExecutionStatus.OUTCOME_UNKNOWN:
                previous = self._read('quarantine', 'desktop')
                if not previous or previous['command_id'] == c.command_id:
                    self._write('quarantine', 'desktop', {'command_id': c.command_id,
                        'account_id': c.account_id, 'checkpoint': 'NATIVE_PENDING' if pending else 'OUTCOME_UNKNOWN'})
        guard.persisted = True

    def reconcile_quarantine(self, command_id, *, health, evidence, native_probe):
        """Trusted local recovery only; never clear merely on lease expiry.

        Caller must verify native process quiescence and supply HEALTH/SESSION
        evidence from the worker detector. This does not retry the old command.
        """
        with resource_lock(self.desktop_lock), resource_lock(self.state_lock):
            q = self._read('quarantine', 'desktop')
            old = self._read('command', command_id)
            if not q or q['command_id'] != command_id or not old:
                raise ValueError('NO_MATCHING_QUARANTINE')
            if (not isinstance(health, WorkerHealth) or health.account_id != q['account_id']
                    or health.worker_id != old['worker_id']
                    or not health.gui_ready or abs((utc_now() - instant(health.observed_at)).total_seconds()) > 30):
                raise ValueError('NATIVE_QUIESCENCE_NOT_VERIFIED')
            proof = native_probe()
            if (not isinstance(proof, dict) or set(proof) != {'command_id','trusted','native_call_pending','process_quiescent'}
                    or proof['command_id'] != command_id or proof['trusted'] is not True
                    or proof['native_call_pending'] is not False or proof['process_quiescent'] is not True):
                raise ValueError('NATIVE_TERMINATION_PROOF_REQUIRED')
            if not evidence or any(e.get('kind') not in {'HEALTH', 'SESSION'} for e in evidence):
                raise ValueError('QUIESCENCE_EVIDENCE_REQUIRED')
            if any(not instant(old['observed_at']) <= instant(e['observed_at']) <= utc_now() for e in evidence):
                raise ValueError('RECOVERY_EVIDENCE_STALE')
            # The descriptors are validated through the public result contract.
            old_result = old.get('result')
            # Validate descriptors even after a process crash before final result.
            if old_result:
                r = WorkerResult.from_dict(dict(old_result, observed_at=utc_now().isoformat(), evidence=evidence,
                                               native_call_pending=False))
                old['result'] = r.to_dict()
            else:
                from .worker_contracts import digest, reference, strict_keys
                for e in evidence:
                    strict_keys(e, {'evidence_ref', 'sha256', 'kind', 'observed_at'})
                    reference(e['evidence_ref']); digest(e['sha256'])
                    if not instant(old['observed_at']) <= instant(e['observed_at']) <= utc_now():
                        raise ValueError('RECOVERY_EVIDENCE_STALE')
            old['native_call_pending'] = False
            old['checkpoint'] = 'NATIVE_QUIESCENCE_VERIFIED'
            old['quiescence_evidence'] = list(evidence)
            old['native_termination_verified'] = True
            self._write('command', command_id, old)
            self._path('quarantine', 'desktop').unlink()

    def execute(self, command, authorization, policy, *, control_provider, health_provider, handler):
        guard = WorkerGuard(self, command, authorization, policy, control_provider, health_provider)
        # One local execution owner; server lease is independently checked for
        # every call. Persistent attempts protect against process restart.
        try:
            with resource_lock(self._path('execution-lock', command.command_id), timeout=.1), \
                    resource_lock(self._path('idempotency-lock', command.idempotency_key), timeout=.1):
                with resource_lock(self.state_lock):
                    old = self._read('command', command.command_id)
                    idem = self._read('idempotency', command.idempotency_key)
                    if old and old.get('result'):
                        if old.get('command_sha256') != hashlib.sha256(json.dumps(command.to_dict(), sort_keys=True).encode()).hexdigest():
                            raise GuardBlocked('COMMAND_BINDING_CHANGED', ExecutionStatus.STALE)
                        result = WorkerResult.from_dict(old['result'])
                        result.validate(command)
                        return result
                    if old:
                        raise GuardBlocked('PREVIOUS_ATTEMPT_UNRESOLVED', ExecutionStatus.OUTCOME_UNKNOWN)
                    if idem:
                        raise GuardBlocked('IDEMPOTENCY_ALREADY_BOUND', ExecutionStatus.STALE)
                guard.check()
                with resource_lock(self.state_lock):
                    if command.session_id:
                        owner = self._read('session', command.session_id)
                        identity = {'task_id': command.task_id, 'account_id': command.account_id,
                                    'profile_id': command.profile_id}
                        if owner and owner != identity:
                            raise GuardBlocked('SESSION_ALREADY_BOUND', ExecutionStatus.STALE)
                        self._write('session', command.session_id, identity)
                self._checkpoint(guard, 'HANDLER_STARTED')
                with ExitStack() as locks:
                    if command.profile_id:
                        locks.enter_context(self.profiles.acquire(command.profile_id, command.account_id, timeout=.1))
                    if command.session_id:
                        locks.enter_context(resource_lock(self._path('session-lock', command.session_id), timeout=.1))
                    result = handler(command, guard)
                if not isinstance(result, WorkerResult):
                    raise GuardBlocked('VERIFICATION_REQUIRED')
                result.validate(command)
                if result.status == ExecutionStatus.SUCCEEDED_VERIFIED:
                    guard.check()
                    if (not guard.calls or result.native_call_pending or not guard.valid_evidence(result.evidence)
                            or command.action in WRITE_ACTIONS and not guard.write_started):
                        raise GuardBlocked('VERIFICATION_REQUIRED')
                elif guard.write_started:
                    result = guard.result(ExecutionStatus.OUTCOME_UNKNOWN, result.reason_code,
                                          evidence=result.evidence, checkpoint=result.checkpoint)
                if result.native_call_pending:
                    guard.pending = True
                self._checkpoint(guard, result.checkpoint, pending=guard.pending, result=result)
                if (result.status == ExecutionStatus.SUCCEEDED_VERIFIED and command.action == Action.CHECK_SESSION
                        and control_provider().takeover == TakeoverState.RESUME_CHECK):
                    # A verified, explicitly authorised resume observation is
                    # the only local account-pause release. Server pause remains
                    # server-owned and must be reconciled independently.
                    with resource_lock(self.state_lock):
                        self._write('account-recovery', command.account_id,
                            {'command_id': command.command_id, 'evidence': list(result.evidence),
                             'checkpoint': 'SESSION_RESUME_VERIFIED'})
                        self._path('account-pause', command.account_id).unlink(missing_ok=True)
                return result
        except GuardBlocked as exc:
            result = guard.result(exc.status, exc.reason)
        except TimeoutError:
            previous = self._read('command', command.command_id)
            if previous and not previous.get('result') and not guard.persisted:
                guard.write_started = previous.get('write_started', False)
                guard.pending = previous.get('native_call_pending', False)
                result = guard.result(ExecutionStatus.OUTCOME_UNKNOWN, 'PREVIOUS_ATTEMPT_UNRESOLVED')
            else:
                result = guard.result(ExecutionStatus.OUTCOME_UNKNOWN if guard.write_started or guard.pending
                                      else ExecutionStatus.FAILED_BEFORE_ACTION, 'BOUNDED_WAIT_EXPIRED')
        except Exception:
            # Never record exception content, captures, tool arguments or secrets.
            result = guard.result(ExecutionStatus.OUTCOME_UNKNOWN if guard.write_started or guard.pending
                                  else ExecutionStatus.FAILED_BEFORE_ACTION, 'WORKER_EXECUTION_FAILED')
        # Do not overwrite another command's already persisted attempt.
        with resource_lock(self.state_lock):
            old = self._read('command', command.command_id)
        if not old or guard.persisted:
            self._checkpoint(guard, result.checkpoint, pending=guard.pending, result=result)
        return result


class WorkerGuard:
    def __init__(self, runtime, command, authorization, policy, control_provider, health_provider):
        self.runtime, self.command = runtime, command
        self.authorization, self.policy = authorization, policy
        self.control_provider, self.health_provider = control_provider, health_provider
        self.started = time.monotonic()
        self.calls = self.reads = self.refreshes = self.no_progress = 0
        self.write_started = self.pending = False
        self.persisted = False
        self.waited = 0.0

    def check(self):
        c = self.command
        if time.monotonic() - self.started >= c.budget.total_seconds:
            raise GuardBlocked('TOTAL_BUDGET_EXHAUSTED', ExecutionStatus.FAILED_AFTER_ACTION if self.write_started else ExecutionStatus.FAILED_BEFORE_ACTION)
        try:
            self.authorization.validate(c)
            self.policy.validate(c)
        except ValueError:
            raise GuardBlocked('AUTHORIZATION_INVALID', ExecutionStatus.STALE)
        control = self.control_provider()
        if not isinstance(control, WorkerControl):
            raise GuardBlocked('CONTROL_UNAVAILABLE')
        recovery = control.takeover == TakeoverState.RESUME_CHECK and c.action in {Action.CHECK_SESSION, Action.VERIFY_OUTBOX}
        if (control.stop and not recovery) or control.manually_handled:
            raise GuardBlocked('STOPPED' if control.stop else 'MANUALLY_HANDLED', ExecutionStatus.CANCELLED)
        if control.takeover_epoch != c.execution_epoch:
            raise GuardBlocked('EXECUTION_EPOCH_CHANGED', ExecutionStatus.STALE)
        if control.takeover != TakeoverState.AUTO_ACTIVE and not recovery:
            raise GuardBlocked('HUMAN_TAKEOVER')
        if not control.lease_live or not control.authorization_current or not control.version_current:
            raise GuardBlocked('LEASE_OR_BINDING_STALE', ExecutionStatus.STALE)
        if control.resources_quarantined or self.runtime._read('quarantine', 'desktop'):
            raise GuardBlocked('NATIVE_RESOURCE_QUARANTINED')
        if control.paused_reason and not recovery:
            raise GuardBlocked(str(control.paused_reason))
        health = self.health_provider()
        if not isinstance(health, WorkerHealth):
            raise GuardBlocked('HEALTH_UNAVAILABLE')
        if health.native_call_pending:
            self.pending = True
            self.runtime._checkpoint(self, 'NATIVE_PENDING', pending=True)
            raise GuardBlocked('NATIVE_PENDING', ExecutionStatus.OUTCOME_UNKNOWN)
        try:
            health.validate(c)
        except ValueError:
            reason = str(health.state) if health.state != HealthState.HEALTHY else 'DESKTOP_OR_TARGET_UNAVAILABLE'
            # Same-account pause persists across runtime instances. Root also
            # receives the typed reason and propagates account health remotely.
            if health.state != HealthState.HEALTHY:
                with resource_lock(self.runtime.state_lock):
                    self.runtime._write('account-pause', c.account_id, {'reason': str(health.state)})
            raise GuardBlocked(reason)
        pause = self.runtime._read('account-pause', c.account_id)
        if pause and not recovery:
            raise GuardBlocked(pause['reason'])
        return health

    def call(self, fn, *, read_only, refresh=False):
        if type(read_only) is not bool or type(refresh) is not bool:
            raise ValueError('EXPLICIT_ACTION_KIND_REQUIRED')
        if not read_only and self.command.action in READ_ACTIONS:
            raise GuardBlocked('READ_COMMAND_CANNOT_WRITE', ExecutionStatus.FAILED_BEFORE_ACTION)
        if refresh and not read_only:
            raise GuardBlocked('REFRESH_MUST_BE_READ_ONLY')
        if not read_only and self.control_provider().takeover == TakeoverState.RESUME_CHECK:
            raise GuardBlocked('RESUME_CHECK_IS_READ_ONLY')
        retries = self.command.budget.read_retries if read_only else 0
        for attempt in range(retries + 1):
            self.check()
            b = self.command.budget
            if (self.calls >= b.max_tool_calls or read_only and self.reads >= b.max_state_reads
                    or refresh and self.refreshes >= b.max_refreshes or self.no_progress >= b.max_no_progress):
                raise GuardBlocked('ACTION_BUDGET_EXHAUSTED')
            with ExitStack() as stack:
                # One lock order for every worker action, matching old draft's
                # desktop lock; no desktop lock is held during generation wait.
                stack.enter_context(resource_lock(self.runtime.desktop_lock, timeout=.1))
                for kind, key in [('account-lock', self.command.account_id),
                                  ('profile-lock', self.command.profile_id),
                                  ('clipboard-lock', 'desktop')]:
                    if key:
                        stack.enter_context(resource_lock(self.runtime._path(kind, key), timeout=.1))
                self.check()  # Revalidate after waiting for locks.
                self.calls += 1
                self.reads += int(read_only)
                self.refreshes += int(refresh)
                self.no_progress += 1
                self.write_started |= not read_only
                self.pending = True
                self.runtime._checkpoint(self, 'NATIVE_CALL_STARTED', pending=True)
                try:
                    # Do not kill an arbitrary native call. On deadline its
                    # daemon may still run; quarantine survives return/restart.
                    completed = threading.Event()
                    outcome = {}
                    def invoke():
                        try:
                            outcome['value'] = fn()
                        except BaseException as exc:
                            outcome['exception'] = exc
                        finally:
                            completed.set()
                    thread = threading.Thread(target=invoke, daemon=True)
                    thread.start()
                    allowance = min(self.runtime.native_timeout_seconds,
                                    b.total_seconds - (time.monotonic() - self.started))
                    if allowance <= 0 or not completed.wait(allowance):
                        raise TimeoutError()
                    if 'exception' in outcome:
                        raise outcome['exception']
                    value = outcome['value']
                except TimeoutError:
                    # Native timeout does not prove native termination, even
                    # for a read. Durable quarantine must outlive lock release.
                    raise GuardBlocked('NATIVE_TIMEOUT', ExecutionStatus.OUTCOME_UNKNOWN)
                except SafeReadFailure:
                    if not read_only:
                        raise GuardBlocked('WRITE_OUTCOME_UNKNOWN', ExecutionStatus.OUTCOME_UNKNOWN)
                    self.pending = False
                    with resource_lock(self.runtime.state_lock):
                        self._clear_own_inflight()
                    self.runtime._checkpoint(self, 'NATIVE_CALL_FAILED')
                    if attempt >= retries:
                        raise GuardBlocked('READ_RETRIES_EXHAUSTED')
                except BaseException:
                    raise GuardBlocked('NATIVE_OUTCOME_UNKNOWN', ExecutionStatus.OUTCOME_UNKNOWN)
                else:
                    self.pending = False
                    with resource_lock(self.runtime.state_lock):
                        self._clear_own_inflight()
                    self.runtime._checkpoint(self, 'NATIVE_CALL_RETURNED')
                    self.check()
                    return value
            self.wait(min(.05 * (2 ** attempt), .5))

    def wait(self, seconds):
        if type(seconds) not in (int, float) or not 0 <= seconds <= 60:
            raise ValueError('BOUNDED_WAIT_REQUIRED')
        if self.waited + seconds > self.command.budget.generation_wait_seconds:
            raise GuardBlocked('GENERATION_WAIT_EXHAUSTED')
        self.waited += seconds
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        self.check()

    def _clear_own_inflight(self):
        q = self.runtime._read('quarantine', 'desktop')
        if q and q['command_id'] == self.command.command_id and q['checkpoint'] == 'NATIVE_PENDING':
            self.runtime._path('quarantine', 'desktop').unlink()

    def mark_progress(self):
        self.check()
        self.no_progress = 0

    def result(self, status, reason, *, evidence=(), checkpoint=None):
        c = self.command
        if self.pending or self.write_started and status != ExecutionStatus.SUCCEEDED_VERIFIED:
            status = ExecutionStatus.OUTCOME_UNKNOWN
        side = (SideEffect.UNKNOWN if status == ExecutionStatus.OUTCOME_UNKNOWN else
                SideEffect.VERIFIED if status == ExecutionStatus.SUCCEEDED_VERIFIED else
                SideEffect.UNVERIFIED if self.write_started else SideEffect.NO_ACTION)
        return WorkerResult(contract_version=1, command_id=c.command_id, task_id=c.task_id,
            outbox_id=c.outbox_id, worker_id=c.worker_id, account_id=c.account_id,
            execution_epoch=c.execution_epoch, question_version=c.question_version,
            context_revision=c.context_revision, status=status, side_effect=side,
            action_started=self.write_started, native_call_pending=self.pending,
            checkpoint=checkpoint or reason, reason_code=reason,
            observed_at=utc_now().isoformat(), evidence=evidence)

    def verified(self, evidence, *, checkpoint='BUSINESS_EVIDENCE_VERIFIED'):
        self.check()
        if not self.valid_evidence(evidence):
            raise GuardBlocked('ACTION_EVIDENCE_REQUIRED')
        return self.result(ExecutionStatus.SUCCEEDED_VERIFIED, 'OK', evidence=evidence, checkpoint=checkpoint)

    def valid_evidence(self, evidence):
        kinds = {Action.READ_MESSAGES: 'MESSAGE_CAPTURE', Action.SAVE_ATTACHMENTS: 'ATTACHMENT_MANIFEST',
                 Action.CHECK_SESSION: 'SESSION', Action.UPLOAD_APPROVED_ATTACHMENTS: 'UPLOAD',
                 Action.SUBMIT_BOUND_QUESTION: 'GENERATION', Action.READ_BOUND_GENERATION: 'GENERATION',
                 Action.STAGE_OUTBOX: 'DRAFT', Action.VERIFY_OUTBOX: 'DELIVERY',
                 Action.EXECUTE_APPROVED_OUTBOX: 'DELIVERY'}
        return bool(evidence and any(e.get('kind') == kinds[self.command.action]
                    and instant(self.command.created_at) <= instant(e['observed_at']) <= utc_now() for e in evidence))
