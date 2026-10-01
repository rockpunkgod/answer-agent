"""Anonymous execution-cache and injected API faults; no real network or GUI."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.worker_client import BusinessAPIError, HTTPBusinessClient, WorkerClient
from helpdesk.worker_contracts import (ExecutionStatus, PermissionMode, SideEffect,
    WorkerControl, WorkerPolicy, WorkerResult, utc_now)
from tests.test_worker_contracts import approval, command, health


def result(c, status=ExecutionStatus.SUCCEEDED_VERIFIED, *, started=True, pending=False):
    at = utc_now().isoformat()
    verified = status == ExecutionStatus.SUCCEEDED_VERIFIED
    effect = SideEffect.VERIFIED if verified else SideEffect.UNKNOWN if status == ExecutionStatus.OUTCOME_UNKNOWN else SideEffect.NO_ACTION
    return WorkerResult(1, c.command_id, c.task_id, c.outbox_id, c.worker_id, c.account_id, c.execution_epoch,
        c.question_version, c.context_revision, status, effect, started, pending, 'FIXTURE_CHECKPOINT',
        'FIXTURE_RESULT', at, ({'evidence_ref': 'anonymous-receipt', 'sha256': 'b'*64,
                               'kind': 'GENERATION', 'observed_at': at},) if verified else ())


class FixtureAPI:
    def __init__(self, c):
        self.c = c
        self.status = ExecutionStatus.NOT_STARTED
        self.current = True
        self.offline = False
        self.fail_authorize = False
        self.fail_result = False
        self.fail_after_ack = False
        self.seen = set()
        self.pulls = self.results = self.authorizations = 0
        self.heartbeats = []

    def check(self):
        if self.offline:
            raise BusinessAPIError('FIXTURE_OFFLINE')

    def heartbeat(self, h):
        self.check()
        self.heartbeats.append(h)
        return {'accepted': True}

    def pull(self, worker_id):
        self.check()
        self.pulls += 1
        return {'command': self.c.to_dict(), 'authorization': approval(self.c).to_dict(),
            'policy': WorkerPolicy(mode=PermissionMode.ASSISTED, allowed_actions=(self.c.action,),
                allowed_accounts=(self.c.account_id,), allowed_scopes=(self.c.target_scope,),
                allowed_profiles=(self.c.profile_id,)).to_dict(), 'lease_id': 'lease-fixture'}

    def authorize(self, worker_id, command_id, lease_id, epoch):
        self.check()
        self.authorizations += 1
        if self.fail_authorize:
            raise BusinessAPIError('FIXTURE_DISCONNECTED_BEFORE_ACTION')
        self.status = ExecutionStatus.RUNNING
        return {'control': WorkerControl(lease_live=True, takeover_epoch=self.c.execution_epoch, version_current=self.current,
                                         authorization_current=True).to_dict()}

    def command(self, worker_id, command_id):
        self.check()
        return {'status': self.status, 'current': self.current}

    def result(self, worker_id, lease_id, observation):
        self.check()
        self.results += 1
        if self.fail_result:
            raise BusinessAPIError('FIXTURE_RETURN_FAILED')
        duplicate = observation.command_id in self.seen
        self.seen.add(observation.command_id)
        self.status = observation.status
        if self.fail_after_ack:
            self.fail_after_ack = False
            raise BusinessAPIError('FIXTURE_ACK_LOST')
        return {'accepted': True, 'command_id': observation.command_id, 'duplicate': duplicate}


class FixtureRuntime:
    """Implements the B boundary only; client tests do not claim native runtime QA."""
    def execute(self, c, auth, policy, *, control_provider, health_provider, handler):
        control = control_provider()
        if not control.lease_live or control.stop or not control.version_current:
            return result(c, ExecutionStatus.FAILED_BEFORE_ACTION, started=False)
        class Guard:
            def call(self, fn, *, read_only=False, refresh=False):
                control = control_provider()
                if not control.lease_live or control.stop or not control.version_current:
                    raise PermissionError('fixture denied')
                return fn()
            def verified(self, evidence=None):
                return result(c)
        try:
            return handler(c, Guard())
        except TimeoutError:
            return result(c, ExecutionStatus.OUTCOME_UNKNOWN, pending=True)


class WorkerClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'execution.db'
        self.c = command(question_version='version-fixture-uuid')
        self.api = FixtureAPI(self.c)
        self.calls = 0
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.tmp.cleanup()

    def handler(self, c, guard):
        def native():
            self.calls += 1
        guard.call(native, read_only=False)
        return guard.verified()

    def client(self, handler=None, h=None):
        client = WorkerClient(self.api, self.c.worker_id, self.path, runtime=FixtureRuntime(),
            health_provider=h or (lambda: health(self.c)), handler=handler or self.handler)
        self.clients.append(client)
        return client

    def restart(self, client):
        client.close()
        self.clients.remove(client)
        return self.client()

    def test_offline_before_pull_and_before_authorization_never_writes(self):
        client = self.client()
        self.api.offline = True
        self.assertFalse(client.run_once()['executed'])
        self.assertEqual(self.api.pulls, 0)
        self.api.offline, self.api.fail_authorize = False, True
        self.assertFalse(client.run_once()['executed'])
        self.assertEqual(self.calls, 0)

    def test_verified_action_return_failure_restart_does_not_call_twice(self):
        client = self.client()
        self.api.fail_result = True
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(self.calls, 1)
        pulls = self.api.pulls
        client = self.restart(client)
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(self.api.pulls, pulls)
        self.api.fail_result = False
        client.run_once()
        self.assertEqual(self.calls, 1)
        self.assertEqual(client.journal.execute('SELECT server_ack FROM execution_journal').fetchone()[0], 1)

    def test_duplicate_result_after_lost_ack_and_duplicate_epoch_never_replay(self):
        client = self.client()
        self.api.fail_after_ack = True
        client.run_once()
        self.assertEqual(self.calls, 1)
        client = self.restart(client)
        client.run_once()
        self.assertEqual(self.api.results, 2)
        self.assertEqual(self.calls, 1)
        self.api.c = replace(self.c, execution_epoch=2)
        self.api.status = ExecutionStatus.NOT_STARTED
        self.assertEqual(client.run_once()['state'], 'DUPLICATE_NOT_REPLAYED')
        self.assertEqual(self.calls, 1)

    def test_unknown_native_blocks_even_after_ack_until_server_reconciles(self):
        def timeout(c, guard):
            def native():
                self.calls += 1
                raise TimeoutError()
            return guard.call(native)
        client = self.client(timeout)
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(self.calls, 1)
        client = self.restart(client)
        pulls = self.api.pulls
        self.assertFalse(client.run_once()['executed'])
        self.assertEqual(self.api.pulls, pulls)
        self.api.status = ExecutionStatus.SUCCEEDED_VERIFIED
        self.assertTrue(client.recover_pending())
        client.run_once()
        self.assertEqual(self.calls, 1)

    def test_running_restart_is_unknown_no_handler_reexecution(self):
        def crash(c, guard):
            guard.call(lambda: setattr(self, 'calls', self.calls + 1))
            raise SystemExit('anonymous simulated process death')
        client = self.client(crash)
        with self.assertRaises(SystemExit):
            client.run_once()
        self.assertEqual(client.journal.execute('SELECT status FROM execution_journal').fetchone()[0], 'RUNNING')
        client = self.restart(client)
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(self.calls, 1)
        self.assertEqual(client.journal.execute('SELECT status FROM execution_journal').fetchone()[0], 'OUTCOME_UNKNOWN')

    def test_stale_command_pending_desktop_and_wrong_health_do_not_execute(self):
        client = self.client()
        self.api.current = False
        self.assertEqual(client.run_once()['state'], 'STALE_OR_ALREADY_STARTED')
        self.api.current = True
        client = self.client(h=lambda: health(self.c, native_call_pending=True))
        self.assertEqual(client.run_once()['state'], 'WAITING_HEALTH')
        self.assertEqual(self.calls, 0)

    def test_unhealthy_heartbeats_are_reported_without_pull_or_native_action(self):
        for changes in ({'state': 'LOGIN_REQUIRED'}, {'state': 'VERIFICATION_REQUIRED'},
                        {'desktop_unlocked': False}, {'connected': False}):
            client = self.client(h=lambda changes=changes: health(self.c, **changes))
            pulls = self.api.pulls
            heartbeats = len(self.api.heartbeats)
            self.assertEqual(client.run_once()['state'], 'WAITING_HEALTH')
            self.assertEqual(len(self.api.heartbeats), heartbeats + 1)
            self.assertEqual(self.api.pulls, pulls)
        self.assertEqual(self.calls, 0)

    def test_unhealthy_heartbeat_pauses_same_account_other_worker_in_coordinator(self):
        from helpdesk.storage import Store
        from helpdesk.worker_coordinator import WorkerCoordinator
        store = Store(Path(self.tmp.name) / 'anonymous-server.db')
        try:
            coordinator = WorkerCoordinator(store, task_validator=lambda c: True)
            policy = WorkerPolicy(mode='ASSISTED', allowed_actions=(self.c.action,),
                allowed_accounts=(self.c.account_id,), allowed_scopes=(self.c.target_scope,),
                allowed_profiles=(self.c.profile_id,))
            coordinator.configure_worker(self.c.worker_id, policy)
            coordinator.configure_worker('worker-other', policy)
            other = replace(self.c, worker_id='worker-other', command_id='command-other',
                            idempotency_key='once-other', execution_epoch=0)
            coordinator.issue(other, approval(other))
            coordinator.heartbeat(health(other))
            class Adapter:
                def heartbeat(inner, h):
                    return coordinator.heartbeat(h)
                def pull(inner, worker_id):
                    raise AssertionError('Unhealthy node must not pull')
            client = WorkerClient(Adapter(), self.c.worker_id, self.path, runtime=FixtureRuntime(),
                health_provider=lambda: health(self.c, state='VERIFICATION_REQUIRED'), handler=self.handler)
            self.clients.append(client)
            self.assertEqual(client.run_once()['state'], 'WAITING_HEALTH')
            self.assertEqual(coordinator.pull('worker-other'), {'command': None})
            self.assertEqual(store.one('SELECT pause_reason FROM worker_accounts WHERE account_id=?',
                                       (self.c.account_id,))[0], 'VERIFICATION_REQUIRED')
            self.assertEqual(self.calls, 0)
        finally:
            store.close()

    def test_failure_label_cannot_clear_unknown_side_effect(self):
        class UnknownFailureRuntime:
            def execute(inner, c, auth, policy, **kwargs):
                return replace(result(c, ExecutionStatus.OUTCOME_UNKNOWN),
                               status=ExecutionStatus.FAILED_AFTER_ACTION)
        client = self.client()
        client.runtime = UnknownFailureRuntime()
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(client.journal.execute('SELECT status FROM execution_journal').fetchone()[0], 'OUTCOME_UNKNOWN')

    def test_server_acceptance_without_independent_completion_still_blocks_pull(self):
        client = self.client()
        original = self.api.result
        def accepted_but_unknown(*args):
            ack = original(*args)
            self.api.status = ExecutionStatus.OUTCOME_UNKNOWN
            return ack
        self.api.result = accepted_but_unknown
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        pulls = self.api.pulls
        self.assertEqual(client.run_once()['state'], 'PENDING_RECOVERY')
        self.assertEqual(self.api.pulls, pulls)
        self.assertEqual(self.calls, 1)

    def test_no_token_authorization_or_profile_contents_in_journal(self):
        with patch.dict(os.environ, {'HELPDESK_WORKER_TOKEN': 'SECRET_SENTINEL_AUTH_COOKIE_PROFILE'}):
            client = self.client()
            client.run_once()
        text = json.dumps([dict(row) for row in client.journal.execute('SELECT * FROM execution_journal')])
        self.assertNotIn('SECRET_SENTINEL', text)
        self.assertNotIn('actor_ref', text)
        self.assertNotIn('allowed_profiles', text)
        self.assertIn('profile-1', text)  # Opaque profile binding only, no credentials/directory.

    def test_journal_cannot_be_network_share_or_business_database(self):
        with self.assertRaises(ValueError):
            WorkerClient(self.api, self.c.worker_id, '//server/share/file.db', runtime=FixtureRuntime(),
                         health_provider=lambda: health(self.c), handler=self.handler)
        foreign = Path(self.tmp.name) / 'business.db'
        db = sqlite3.connect(foreign)
        db.execute('CREATE TABLE performance_units(id)')
        db.commit(); db.close()
        before = foreign.read_bytes()
        with self.assertRaises(ValueError):
            WorkerClient(self.api, self.c.worker_id, foreign, runtime=FixtureRuntime(),
                         health_provider=lambda: health(self.c), handler=self.handler)
        self.assertEqual(before, foreign.read_bytes())

    def test_actual_runtime_client_binding_uses_guarded_native_and_verified_evidence(self):
        from helpdesk.worker_runtime import WorkerRuntime
        runtime = WorkerRuntime(Path(self.tmp.name) / 'anonymous-worker')
        runtime.profiles.register(self.c.profile_id, self.c.account_id,
                                  runtime.profiles.root / 'dedicated-browser')
        def handler(c, guard):
            guard.call(lambda: setattr(self, 'calls', self.calls + 1), read_only=False)
            return guard.verified([{'evidence_ref': 'anonymous-generation', 'sha256': 'c'*64,
                                    'kind': 'GENERATION', 'observed_at': utc_now().isoformat()}])
        client = WorkerClient(self.api, self.c.worker_id, self.path, runtime=runtime,
            health_provider=lambda: health(self.c), handler=handler)
        self.clients.append(client)
        outcome = client.run_once()
        self.assertEqual(outcome['state'], 'SUCCEEDED_VERIFIED')
        self.assertEqual(self.calls, 1)
        client.run_once()
        self.assertEqual(self.calls, 1)


class HTTPBusinessClientTests(unittest.TestCase):
    def test_only_local_http_remote_https_and_environment_token(self):
        for url in ('http://remote.invalid', 'file:///tmp/no', 'https://u:p@remote.invalid',
                    'http://127.0.0.1/api', 'https://remote.invalid?token=secret'):
            with self.assertRaises(ValueError):
                HTTPBusinessClient(url)
        with patch.dict(os.environ, {}, clear=True):
            HTTPBusinessClient('http://127.0.0.1:12345')
            with self.assertRaises(ValueError):
                HTTPBusinessClient('https://remote.invalid')

    def test_fixed_business_routes_and_error_does_not_expose_token(self):
        with patch.dict(os.environ, {'HELPDESK_WORKER_TOKEN': 'SECRET_SENTINEL'}):
            api = HTTPBusinessClient('https://fixture.invalid')
        class Response(io.BytesIO):
            status = 200
        seen = []
        def open_request(req, **kwargs):
            seen.append(req)
            return Response(b'{"command":null}')
        with patch.object(api._opener, 'open', side_effect=open_request):
            self.assertEqual(api.pull('worker-fixture'), {'command': None})
            api.command('worker-fixture', 'command-fixture')
        self.assertEqual(seen[0].full_url, 'https://fixture.invalid/api/worker/pull')
        self.assertEqual(json.loads(seen[0].data), {'worker_id': 'worker-fixture'})
        self.assertIn('/api/worker/command?', seen[1].full_url)
        with patch.object(api._opener, 'open', side_effect=OSError('SECRET_SENTINEL Cookie=private')):
            with self.assertRaises(BusinessAPIError) as raised:
                api.pull('worker-fixture')
        self.assertNotIn('SECRET_SENTINEL', str(raised.exception))
        self.assertNotIn('Cookie', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
