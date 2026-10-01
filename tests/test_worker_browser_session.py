import json
import os
import subprocess
import tempfile
import threading
import unittest
import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpdesk.worker_browser_session import (
    DedicatedBrowserSession, DEEPSEEK_URL, PlaywrightEdgeExecutor,
    WindowsEdgeVerifier, WindowsProfileAcl,
)
from helpdesk.worker_environment import ProfileManager
from helpdesk.worker_browser_recovery import ProfileQuiescence, profile_path_digest


class FakeAcl:
    def __init__(self, error=False):
        self.calls = []
        self.error = error

    def protect(self, path, *, created):
        self.calls.append((path, created))
        if self.error:
            raise RuntimeError('PROFILE_ACL_UNVERIFIED')


class FakeVerifier:
    def __init__(self, approved=True):
        self.approved = approved

    def verify(self, path):
        return self.approved


class FakeHandle:
    def __init__(self):
        self.alive = True
        self.closes = 0
        self.broken = False

    def is_alive(self):
        if self.broken:
            raise RuntimeError('lost process handle with secret data')
        return self.alive

    def close(self):
        self.closes += 1
        if not self.broken:
            self.alive = False


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.handle = FakeHandle()

    def start(self, *args):
        self.calls.append(args)
        return self.handle


class StepClock:
    def __init__(self):
        self.value = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.value += timedelta(seconds=1)
        return self.value


class FakeRecoveryObserver:
    def __init__(self, clock, *, changes=None, on_call=None):
        self.clock = clock
        self.changes = changes or {}
        self.on_call = on_call
        self.calls = 0

    def __call__(self, profile_id, account_id, path, marker_sha):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        values = dict(profile_id=profile_id, account_id=account_id,
            profile_path_sha256=profile_path_digest(path), marker_sha256=marker_sha,
            observed_at=self.clock().isoformat(), session_id=1,
            matching_browser_processes=0, unknown_processes=0,
            other_executor_processes=0, scan_completed=True,
            scan_scope='MACHINE_CONFIGURED_EXECUTORS')
        values.update(self.changes)
        return ProfileQuiescence(**values)


class FakeControlGate:
    def __init__(self, decision=True):
        self.decision = decision
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.decision() if callable(self.decision) else self.decision


class DedicatedBrowserSessionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell module probe')
    def test_security_manifest_loads_with_isolated_invalid_module_path(self):
        # Execute only the fixed script prefix. Do not read any real ACL,
        # signature, profile directory, or browser state.
        acl_prefix = WindowsProfileAcl._SCRIPT.split('$p = ', 1)[0]
        signature_prefix = WindowsEdgeVerifier._SCRIPT.split('$p = ', 1)[0]
        self.assertEqual(acl_prefix, signature_prefix)
        probe = acl_prefix + r'''
foreach ($name in @('Microsoft.PowerShell.Security\Get-Acl', 'Microsoft.PowerShell.Security\Set-Acl', 'Microsoft.PowerShell.Security\Get-AuthenticodeSignature')) {
  $command = Get-Command -Name $name -ErrorAction Stop
  if ($command.CommandType -ne 'Cmdlet') { throw 'SECURITY_CMDLET_UNAVAILABLE' }
}
[Console]::Out.Write('SECURITY_MODULE_OK')
'''
        env = os.environ.copy()
        env['PSModulePath'] = str(self.base / 'nonexistent-modules')
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', probe],
            text=True, encoding='utf-8', errors='strict',
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, check=False, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, 'SECURITY_MODULE_OK')

    @unittest.skipUnless(os.name == 'nt', 'Windows subprocess contract')
    def test_acl_utf8_chinese_path_and_failure_closed(self):
        path = Path('C:/已批准/专用浏览器')
        acl = WindowsProfileAcl()
        with patch('helpdesk.worker_browser_session.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout='ACL_OK')) as run:
            acl.protect(path, created=True)
        options = run.call_args.kwargs
        self.assertEqual(options['input'], str(path))
        self.assertEqual(options['encoding'], 'utf-8')
        self.assertEqual(options['errors'], 'strict')
        self.assertIn('[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)', acl._SCRIPT)
        self.assertIn('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)', acl._SCRIPT)
        with patch('helpdesk.worker_browser_session.subprocess.run',
                   return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaisesRegex(RuntimeError, 'PROFILE_ACL_UNVERIFIED'):
                acl.protect(path, created=False)

    @unittest.skipUnless(os.name == 'nt', 'Windows subprocess contract')
    def test_edge_signature_utf8_chinese_path_and_failure_closed(self):
        import ctypes
        path = Path('C:/已批准/中文程序/msedge.exe')
        fixed_drive = SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=lambda *_: 3))
        with (patch.object(Path, 'is_file', return_value=True),
              patch.object(ctypes, 'windll', fixed_drive),
              patch('helpdesk.worker_browser_session.subprocess.run',
                    return_value=SimpleNamespace(returncode=0, stdout='EDGE_OK')) as run):
            self.assertTrue(WindowsEdgeVerifier().verify(path))
        options = run.call_args.kwargs
        self.assertEqual(options['input'], str(path))
        self.assertEqual(options['encoding'], 'utf-8')
        self.assertEqual(options['errors'], 'strict')
        self.assertIn('[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)', WindowsEdgeVerifier._SCRIPT)
        self.assertIn('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)', WindowsEdgeVerifier._SCRIPT)
        with (patch.object(Path, 'is_file', return_value=True),
              patch.object(ctypes, 'windll', fixed_drive),
              patch('helpdesk.worker_browser_session.subprocess.run',
                    return_value=SimpleNamespace(returncode=1, stdout=''))):
            self.assertFalse(WindowsEdgeVerifier().verify(path))

    def test_acl_script_requires_child_inheritance_on_readback(self):
        script = WindowsProfileAcl._SCRIPT
        self.assertIn('$acl.AreAccessRulesProtected', script)
        self.assertIn('$rule.IsInherited', script)
        self.assertIn('InheritanceFlags]::ContainerInherit', script)
        self.assertIn('InheritanceFlags]::ObjectInherit', script)
        self.assertIn('$rule.InheritanceFlags -ne $expectedInheritance', script)
        self.assertIn("$rule.PropagationFlags -ne 'None'", script)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.profiles = ProfileManager(self.base / 'dedicated')
        self.profiles.register('profile-a', 'account-a', self.profiles.root / 'browser-a')
        self.acl = FakeAcl()
        self.verifier = FakeVerifier()
        self.executor = FakeExecutor()

    def session(self, **changes):
        settings = dict(acl=self.acl, verifier=self.verifier, executor=self.executor)
        settings.update(changes)
        return DedicatedBrowserSession(self.profiles, 'profile-a', 'account-a',
                                       self.base / 'msedge.exe', **settings)

    def failed_preparation(self, session, *, restore_acl=True):
        acl = session.acl
        acl.error = True
        with self.assertRaisesRegex(RuntimeError, 'PROFILE_ACL_UNVERIFIED'):
            session.prepare()
        acl.calls.clear()
        if restore_acl:
            acl.error = False
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')

    def test_recover_unstarted_archives_unknown_and_proofs_before_closed(self):
        session = self.session()
        self.failed_preparation(session)
        original = session._state_path.read_bytes()
        recovering = self.session()  # New process/session instance sees durable provenance.
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        outcome = recovering.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock)
        self.assertEqual(outcome.status, 'CLOSED')
        self.assertEqual(outcome.reason_code, 'UNSTARTED_PROFILE_RECOVERED')
        self.assertEqual(observer.calls, 2)
        self.assertEqual([created for _, created in self.acl.calls], [True, False])
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'CLOSED')
        self.assertFalse(recovering._quarantined)
        profile_key = hashlib.sha256(b'profile-a').hexdigest()
        marker_sha = hashlib.sha256(original).hexdigest()
        attempt = self.profiles.root / ('browser-recovery-attempt-' + profile_key + '.json')
        evidence = self.profiles.root / ('browser-recovery-' + profile_key + '-' + marker_sha + '.json')
        self.assertEqual(base64.b64decode(json.loads(attempt.read_text())['original_marker_b64']), original)
        saved = json.loads(evidence.read_text())
        self.assertEqual(base64.b64decode(saved['original_marker_b64']), original)
        self.assertEqual(saved['marker_sha256'], marker_sha)
        self.assertEqual(saved['matching_browser_processes'], 0)
        self.assertEqual(saved['scan_scope'], 'MACHINE_CONFIGURED_EXECUTORS')
        self.assertNotIn(str(self.base), evidence.read_text())
        self.assertEqual(self.executor.calls, [])

    def test_recover_unstarted_rejects_invalid_first_proof_without_acl(self):
        variants = [
            {'matching_browser_processes': 1}, {'unknown_processes': 1},
            {'other_executor_processes': 1}, {'scan_completed': False},
            {'account_id': 'different'}, {'profile_path_sha256': '0' * 64},
            {'marker_sha256': '0' * 64},
            {'scan_scope': 'UNCONFIRMED'},
            {'scan_scope': 'CURRENT_SESSION_ONLY'},
            {'observed_at': '2026-09-30T00:00:00+00:00'},
        ]
        for index, changes in enumerate(variants):
            with self.subTest(changes=changes):
                root = self.base / f'recovery-{index}'
                profiles = ProfileManager(root)
                profiles.register('profile-a', 'account-a', root / 'browser-a')
                acl = FakeAcl()
                session = DedicatedBrowserSession(profiles, 'profile-a', 'account-a',
                    self.base / 'msedge.exe', acl=acl, verifier=self.verifier,
                    executor=self.executor)
                self.failed_preparation(session)
                clock = StepClock()
                observer = FakeRecoveryObserver(clock, changes=changes)
                self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status,
                                 'OUTCOME_UNKNOWN')
                self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
                self.assertEqual(acl.calls, [])
                self.assertEqual(observer.calls, 1)

    def test_recover_unstarted_rejects_corrupt_populated_and_owned(self):
        session = self.session()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        session._state_path.write_text('[]', encoding='utf-8')
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        session._state_path.unlink()
        self.failed_preparation(session)
        profile = self.profiles.root / 'browser-a'
        (profile / 'opaque-entry').write_bytes(b'')
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        (profile / 'opaque-entry').unlink()
        session._handle = FakeHandle()
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        session._handle = None
        session._occupancy = object()  # Native browser call may still be pending.
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        self.assertEqual(self.acl.calls, [])

    def test_recover_unstarted_second_proof_live_actor_keeps_unknown(self):
        session = self.session()
        self.failed_preparation(session)
        original = session._state_path.read_bytes()
        clock = StepClock()
        class SecondLiveObserver(FakeRecoveryObserver):
            def __call__(self, *args):
                proof = super().__call__(*args)
                return replace(proof, other_executor_processes=1) if self.calls == 2 else proof
        observer = SecondLiveObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 2)
        self.assertEqual([created for _, created in self.acl.calls], [True])
        self.assertEqual(session._state_path.read_bytes(), original)

    def test_recover_unstarted_changed_marker_after_acl_keeps_unknown(self):
        session = self.session()
        self.failed_preparation(session)
        clock = StepClock()
        def change_on_second(call):
            if call == 2:
                session._state_path.write_bytes(session._state_path.read_bytes() + b' ')
        observer = FakeRecoveryObserver(clock, on_call=change_on_second)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual([created for _, created in self.acl.calls], [True])
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
        self.assertEqual(self.executor.calls, [])

    def test_recover_unstarted_acl_failure_is_durable_one_shot(self):
        acl = FakeAcl(error=True)
        session = self.session(acl=acl)
        self.failed_preparation(session, restore_acl=False)
        original = session._state_path.read_bytes()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 1)
        self.assertEqual(len(acl.calls), 1)
        self.assertEqual(session._state_path.read_bytes(), original)

    def test_recover_unstarted_evidence_interruption_never_closes(self):
        session = self.session()
        self.failed_preparation(session)
        original = session._state_path.read_bytes()
        marker_sha = hashlib.sha256(original).hexdigest()
        profile_key = hashlib.sha256(b'profile-a').hexdigest()
        evidence = self.profiles.root / ('browser-recovery-' + profile_key + '-' + marker_sha + '.json')
        evidence.write_text('existing-evidence', encoding='utf-8')
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual([created for _, created in self.acl.calls], [True, False])
        self.assertEqual(session._state_path.read_bytes(), original)
        self.assertEqual(evidence.read_text(), 'existing-evidence')

    def test_legacy_unknown_without_preparation_source_is_not_recoverable(self):
        session = self.session()
        session._write_state('UNKNOWN')
        original = session._state_path.read_bytes()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(session._state_path.read_bytes(), original)
        self.assertEqual(observer.calls, 0)
        self.assertEqual(self.acl.calls, [])

    def test_failed_launch_attempt_blocks_new_instance_recovery(self):
        class ThrowingExecutor:
            calls = 0
            def start(self, *args):
                self.calls += 1
                raise RuntimeError('anonymous startup failure')
        executor = ThrowingExecutor()
        session = self.session(executor=executor)
        session.prepare()
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(executor.calls, 1)
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
        self.assertEqual(len(list(session._launch_attempts())), 1)
        # Simulate OS release after the launching process exits; provenance is
        # durable, so another instance still cannot infer "unstarted".
        session._occupancy.close()
        session._occupancy = None
        recovered = self.session(acl=FakeAcl(), executor=FakeExecutor())
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(recovered.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        self.assertEqual(recovered.acl.calls, [])
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')

    def test_launch_record_write_failure_never_calls_executor(self):
        session = self.session()
        session.prepare()
        def fail_record():
            raise OSError('anonymous disk failure')
        session._record_launch_attempt = fail_record
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
        self.addCleanup(lambda: session._occupancy.close() if session._occupancy is not None else None)

    def test_preparation_source_marker_mismatch_rejects_recovery(self):
        session = self.session()
        self.failed_preparation(session)
        session._state_path.write_bytes(session._state_path.read_bytes() + b' ')
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        self.assertEqual(self.acl.calls, [])

    def test_damaged_preparation_source_rejects_recovery(self):
        session = self.session()
        self.failed_preparation(session)
        source = self.profiles.root / (
            'browser-preparation-failed-' + hashlib.sha256(b'profile-a').hexdigest() + '.json')
        source.write_text('{}', encoding='utf-8')
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, control_gate=FakeControlGate(), clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 0)
        self.assertEqual(self.acl.calls, [])
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')

    def test_recovery_missing_or_false_entry_gate_has_no_observation_or_acl(self):
        session = self.session()
        self.failed_preparation(session)
        marker = session._state_path.read_bytes()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        self.assertEqual(session.recover_unstarted(observer, clock=clock).status,
                         'OUTCOME_UNKNOWN')
        gate = FakeControlGate(False)
        self.assertEqual(session.recover_unstarted(observer, control_gate=gate,
                                                   clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(gate.calls, 1)
        self.assertEqual(observer.calls, 0)
        self.assertEqual(self.acl.calls, [])
        self.assertEqual(session._state_path.read_bytes(), marker)
        self.assertFalse(list(self.profiles.root.glob('browser-recovery-attempt-*.json')))

    def test_recovery_gate_lost_after_second_proof_keeps_unknown(self):
        session = self.session()
        self.failed_preparation(session)
        marker = session._state_path.read_bytes()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        gate = FakeControlGate(lambda: observer.calls < 2)
        self.assertEqual(session.recover_unstarted(observer, control_gate=gate,
                                                   clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 2)
        self.assertEqual([created for _, created in self.acl.calls], [True])
        self.assertEqual(session._state_path.read_bytes(), marker)

    def test_recovery_gate_lost_after_evidence_keeps_unknown(self):
        session = self.session()
        self.failed_preparation(session)
        marker = session._state_path.read_bytes()
        clock = StepClock()
        observer = FakeRecoveryObserver(clock)
        evidence_prefix = 'browser-recovery-' + hashlib.sha256(b'profile-a').hexdigest() + '-'
        gate = FakeControlGate(lambda: not any(self.profiles.root.glob(evidence_prefix + '*.json')))
        self.assertEqual(session.recover_unstarted(observer, control_gate=gate,
                                                   clock=clock).status, 'OUTCOME_UNKNOWN')
        self.assertEqual(observer.calls, 2)
        self.assertEqual([created for _, created in self.acl.calls], [True, False])
        self.assertTrue(list(self.profiles.root.glob(evidence_prefix + '*.json')))
        self.assertEqual(session._state_path.read_bytes(), marker)

    def test_prepare_only_acl_no_browser_or_login_copy(self):
        session = self.session()
        result = session.prepare()
        self.assertEqual(result.status, 'PREPARED')
        self.assertEqual(self.acl.calls[0][1], True)
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(list((self.profiles.root / 'browser-a').iterdir()), [])
        self.assertFalse(session._state_path.exists())

    def test_acl_failure_blocks_launch(self):
        session = self.session(acl=FakeAcl(error=True))
        with self.assertRaisesRegex(RuntimeError, 'PROFILE_ACL_UNVERIFIED'):
            session.prepare()
        self.assertEqual(session.start().reason_code, 'PREVIOUS_BROWSER_UNRESOLVED')
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
        self.assertFalse(self.executor.calls)

    def test_existing_populated_profile_must_pass_acl_readback(self):
        profile = self.profiles.root / 'browser-a'
        (profile / 'History').write_text('opaque', encoding='utf-8')
        session = self.session()
        session.prepare()
        self.assertEqual(self.acl.calls[-1], (profile, False))

    def test_start_reuses_owned_session_and_blocks_second_owner(self):
        session = self.session()
        session.prepare()
        first = session.start()
        self.assertEqual(first.status, 'WAITING_HUMAN')
        self.assertEqual(first.reason_code, 'NORMAL_LOGIN_OR_SESSION_CHECK_REQUIRED')
        self.assertEqual(session.start().reason_code, 'BROWSER_REUSED')
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(self.executor.calls[0][2], DEEPSEEK_URL)
        competing = self.session(executor=FakeExecutor())
        self.assertEqual(competing.start().reason_code, 'PROFILE_OCCUPIED')
        self.assertEqual(session.close().status, 'CLOSED')
        self.assertEqual(self.executor.handle.closes, 1)

    def test_unresolved_process_is_never_restarted_or_blindly_cleared(self):
        session = self.session()
        session.prepare()
        session.start()
        self.executor.handle.broken = True
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(session.close().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')

    def test_stale_active_marker_never_restarts(self):
        session = self.session()
        session.prepare()
        session._write_state('RUNNING')
        self.assertEqual(session.start().reason_code, 'PREVIOUS_BROWSER_UNRESOLVED')
        self.assertEqual(self.executor.calls, [])

    def test_malformed_state_shapes_never_start_or_prepare(self):
        malformed = ['[]', 'null', '{}', '""', 'false', '0', '{',
                     '{"state":"CLOSED"}',
                     '{"state":"READY","profile_id":"profile-a","account_id":"account-a"}',
                     '{"state":"CLOSED","profile_id":"profile-a","account_id":"other"}',
                     '{"state":"CLOSED","profile_id":"profile-a","account_id":"account-a","extra":1}']
        for index, payload in enumerate(malformed):
            with self.subTest(payload=payload):
                other_root = self.base / f'dedicated-{index}'
                profiles = ProfileManager(other_root)
                profiles.register('profile-a', 'account-a', other_root / 'browser-a')
                acl = FakeAcl()
                executor = FakeExecutor()
                session = DedicatedBrowserSession(profiles, 'profile-a', 'account-a',
                    self.base / 'msedge.exe', acl=acl, verifier=FakeVerifier(), executor=executor)
                session._state_path.write_text(payload, encoding='utf-8')
                self.assertEqual(session.prepare().status, 'OUTCOME_UNKNOWN')
                self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
                self.assertEqual(acl.calls, [])
                self.assertEqual(executor.calls, [])

    def test_prepare_rejects_live_or_unknown_owner_without_acl_touch(self):
        session = self.session()
        session.prepare()
        session.start()
        self.acl.calls.clear()
        self.assertEqual(session.prepare().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(self.acl.calls, [])
        competitor = self.session(acl=FakeAcl())
        self.assertEqual(competitor.prepare().reason_code, 'PROFILE_OCCUPIED')
        self.assertEqual(competitor.acl.calls, [])
        self.executor.handle.broken = True
        self.assertEqual(session.close().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(competitor.prepare().reason_code, 'PROFILE_OCCUPIED')

    def test_prepare_holds_both_locks_through_acl_change(self):
        entered = threading.Event()
        release = threading.Event()
        class BlockingAcl(FakeAcl):
            def protect(self, path, *, created):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('TEST_TIMEOUT')
                super().protect(path, created=created)
        primary = self.session(acl=BlockingAcl())
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(primary.prepare()))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            competitor = self.session(acl=FakeAcl(), executor=FakeExecutor())
            self.assertEqual(competitor.prepare().reason_code, 'PROFILE_OCCUPIED')
            self.assertEqual(competitor.start().reason_code, 'PROFILE_OCCUPIED')
            self.assertEqual(competitor.acl.calls, [])
            self.assertEqual(competitor.executor.calls, [])
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes[0].status, 'PREPARED')

    def test_outer_profile_operation_lock_blocks_lifecycle_without_action(self):
        session = self.session()
        with self.profiles.acquire('profile-a', 'account-a', timeout=.1):
            self.assertEqual(session.prepare().reason_code, 'PROFILE_OCCUPIED')
            self.assertEqual(session.start().reason_code, 'PROFILE_OCCUPIED')
        self.assertEqual(self.acl.calls, [])
        self.assertEqual(self.executor.calls, [])

    def test_playwright_executor_uses_only_owned_context_and_fixed_url(self):
        events = []
        class Browser:
            connected = True
            def is_connected(self):
                return self.connected
        browser = Browser()
        class Page:
            def goto(self, *args, **kwargs):
                events.append(('goto', args, kwargs))
        class Context:
            def __init__(self):
                self.browser = browser
                self.pages = [Page()]
            def close(self):
                events.append(('context_close',))
                browser.connected = False
        context = Context()
        class Chromium:
            def launch_persistent_context(self, **kwargs):
                events.append(('launch', kwargs))
                return context
        class Driver:
            chromium = Chromium()
            def stop(self):
                events.append(('driver_stop',))
        class Launcher:
            def start(self):
                return Driver()
        executor = PlaywrightEdgeExecutor(
            playwright_factory=lambda: Launcher(),
            termination_proof=lambda *owned: events.append(('termination_proved',)) or True,
        )
        edge = self.base / 'msedge.exe'
        profile = self.profiles.root / 'browser-a'
        with self.assertRaises(ValueError):
            executor.start(edge, profile, 'https://example.com/')
        self.assertEqual(events, [])
        handle = executor.start(edge, profile, DEEPSEEK_URL)
        self.assertTrue(handle.is_alive())
        self.assertEqual(events[0], ('launch', dict(user_data_dir=str(profile),
            executable_path=str(edge), headless=False, timeout=30000)))
        self.assertEqual(events[1], ('goto', (DEEPSEEK_URL,),
            dict(wait_until='domcontentloaded', timeout=30000)))
        handle.close()
        self.assertFalse(handle.is_alive())
        self.assertEqual(events[-3:], [('context_close',), ('driver_stop',), ('termination_proved',)])

    def test_playwright_disconnect_without_process_proof_remains_unknown(self):
        events = []
        class Browser:
            connected = True
            def is_connected(self):
                return self.connected
        browser = Browser()
        class Context:
            def __init__(self):
                self.browser = browser
                self.pages = [self]
            def goto(self, *args, **kwargs):
                pass
            def close(self):
                browser.connected = False
                events.append('context_close')
        class Driver:
            def __init__(self):
                self.chromium = type('Chromium', (), {
                    'launch_persistent_context': lambda self, **kwargs: Context()})()
            def stop(self):
                events.append('driver_stop')
        driver = Driver()
        launcher = type('Launcher', (), {'start': lambda self: driver})()
        executor = PlaywrightEdgeExecutor(playwright_factory=lambda: launcher)
        handle = executor.start(self.base / 'msedge.exe', self.profiles.root / 'browser-a', DEEPSEEK_URL)
        with self.assertRaisesRegex(RuntimeError, 'BROWSER_TERMINATION_UNVERIFIED'):
            handle.close()
        self.assertEqual(events, ['context_close', 'driver_stop'])

    def test_playwright_executor_unknown_browser_ownership_does_not_stop_driver(self):
        events = []
        class Context:
            browser = None
            pages = []
            def new_page(self):
                return self
            def goto(self, *args, **kwargs):
                pass
            def close(self):
                events.append('context_close')
        class Driver:
            chromium = None
            def stop(self):
                events.append('driver_stop')
        driver = Driver()
        driver.chromium = type('Chromium', (), {
            'launch_persistent_context': lambda self, **kwargs: Context()})()
        launcher = type('Launcher', (), {'start': lambda self: driver})()
        executor = PlaywrightEdgeExecutor(playwright_factory=lambda: launcher)
        handle = executor.start(self.base / 'msedge.exe', self.profiles.root / 'browser-a', DEEPSEEK_URL)
        self.assertIsNone(handle.is_alive())
        with self.assertRaisesRegex(RuntimeError, 'BROWSER_OWNERSHIP_UNKNOWN'):
            handle.close()
        self.assertEqual(events, [])

    def test_unwritable_state_never_launches_and_quarantines_instance(self):
        session = self.session()
        session.prepare()
        session._state_path = self.base / 'missing-parent' / 'browser.json'
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(self.executor.calls, [])

    def test_post_launch_state_failure_keeps_unknown_and_blocks_relaunch(self):
        session = self.session()
        self.addCleanup(lambda: session._occupancy.close() if session._occupancy is not None else None)
        session.prepare()
        original = session._write_state
        def fail_running(state):
            if state == 'RUNNING':
                raise RuntimeError('BROWSER_STATE_UNAVAILABLE')
            original(state)
        session._write_state = fail_running
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(json.loads(session._state_path.read_text())['state'], 'UNKNOWN')
        self.assertEqual(session.start().status, 'OUTCOME_UNKNOWN')
        self.assertEqual(len(self.executor.calls), 1)

    def test_close_state_failure_does_not_release_occupancy(self):
        session = self.session()
        self.addCleanup(lambda: session._occupancy.close() if session._occupancy is not None else None)
        session.prepare()
        session.start()
        original = session._write_state
        def fail_closed(state):
            if state == 'CLOSED':
                session._quarantined = True
                raise RuntimeError('BROWSER_STATE_UNAVAILABLE')
            original(state)
        session._write_state = fail_closed
        self.assertEqual(session.close().status, 'OUTCOME_UNKNOWN')
        self.assertIsNotNone(session._occupancy)
        self.assertEqual(self.session(executor=FakeExecutor()).start().reason_code, 'PROFILE_OCCUPIED')

    def test_edge_validation_blocks_launch_and_releases_slot(self):
        session = self.session(verifier=FakeVerifier(False))
        session.prepare()
        self.assertEqual(session.start().reason_code, 'EDGE_NOT_VERIFIED')
        self.assertFalse(self.executor.calls)
        self.assertIsNone(session._occupancy)

    def test_untrusted_account_default_escape_and_symlink_rejected(self):
        with self.assertRaises(ValueError):
            DedicatedBrowserSession(self.profiles, 'profile-a', 'account-b', self.base / 'msedge.exe',
                                    acl=self.acl, verifier=self.verifier, executor=self.executor).prepare()
        for bad in (self.profiles.root / 'Default', self.profiles.root / 'User Data' / 'other',
                    self.base / 'outside'):
            with self.assertRaises(ValueError):
                self.profiles.register('bad', 'other', bad)
        original = self.profiles.root / 'browser-a'
        original.rmdir()
        try:
            original.symlink_to(self.base, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            self.session().prepare()

    def test_outcomes_and_journal_do_not_include_sensitive_fields(self):
        session = self.session()
        session.prepare()
        session.start()
        serialized = json.dumps(session.start().__dict__) + session._state_path.read_text()
        for forbidden in ('cookie', 'password', 'storage_state', 'token', 'msedge.exe', str(self.base)):
            self.assertNotIn(forbidden, serialized)
        session.close()


if __name__ == '__main__':
    unittest.main()
