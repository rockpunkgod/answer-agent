"""Dedicated Edge profile lifecycle. No desktop action occurs at construction.

The caller supplies a trusted browser executor. This module never reads browser
storage, cookies, passwords, or page content. All public outcomes are fixed codes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading

from .locking import resource_lock
from .worker_contracts import instant, reference, utc_now
from .worker_browser_recovery import ProfileQuiescence


DEEPSEEK_URL = 'https://chat.deepseek.com/'
_FORBIDDEN = {'default', 'user data', 'profile 1'}
_ACTIVE = {'STARTING', 'RUNNING', 'UNKNOWN'}
_STATES = _ACTIVE | {'CLOSED'}


@dataclass(frozen=True)
class BrowserOutcome:
    status: str
    reason_code: str
    profile_id: str
    account_id: str


def _private_path(path: Path, root: Path) -> Path:
    raw = str(path)
    if raw.startswith(('\\\\', '//')) or not path.is_absolute():
        raise ValueError('LOCAL_DEDICATED_PROFILE_REQUIRED')
    if any(part.casefold() in _FORBIDDEN for part in path.parts):
        raise ValueError('DEDICATED_PROFILE_REQUIRED')
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('PROFILE_PATH_CHANGED')
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        raise ValueError('PROFILE_PATH_CHANGED') from None
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError('PROFILE_PATH_CHANGED')
    if os.name == 'nt':
        import ctypes
        drive = resolved.drive + '\\'
        if not drive or ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:
            raise ValueError('LOCAL_FIXED_DRIVE_REQUIRED')
    return resolved


class WindowsProfileAcl:
    """Apply a minimal DACL to a newly created empty directory; verify old ACLs.

    Existing directories are never silently rewritten. PowerShell receives a
    fixed script and the directory on stdin; subprocess uses an argument array.
    """

    _SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$securityManifest = [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Security.psd1')
Import-Module -Name $securityManifest -ErrorAction Stop
$p = [Console]::In.ReadToEnd().Trim()
if (-not [Environment]::UserInteractive) { throw 'INTERACTIVE_USER_REQUIRED' }
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sys = 'S-1-5-18'
$admin = 'S-1-5-32-544'
$wanted = @($me, $sys, $admin)
$acl = Microsoft.PowerShell.Security\Get-Acl -LiteralPath $p
$owner = $acl.Owner
try { $owner = (New-Object System.Security.Principal.NTAccount($owner)).Translate([System.Security.Principal.SecurityIdentifier]).Value } catch {}
if ($owner -ne $me) { throw 'OWNER_MISMATCH' }
if ($env:CODEX_PROFILE_ACL_CREATE -eq '1') {
  if (@(Get-ChildItem -LiteralPath $p -Force).Count -ne 0) { throw 'PROFILE_NOT_EMPTY' }
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
  foreach ($sid in $wanted) {
    $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
  }
  Microsoft.PowerShell.Security\Set-Acl -LiteralPath $p -AclObject $acl
  $acl = Microsoft.PowerShell.Security\Get-Acl -LiteralPath $p
}
if (-not $acl.AreAccessRulesProtected) { throw 'ACL_INHERITANCE_ENABLED' }
$seen = @{}
$expectedInheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
foreach ($rule in $acl.Access) {
  try { $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { throw 'ACL_IDENTITY_UNKNOWN' }
  if ($sid -notin $wanted -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne 'FullControl' -or $rule.IsInherited -or $rule.InheritanceFlags -ne $expectedInheritance -or $rule.PropagationFlags -ne 'None') { throw 'ACL_RULE_UNSAFE' }
  $seen[$sid] = $true
}
foreach ($sid in $wanted) { if (-not $seen.ContainsKey($sid)) { throw 'ACL_RULE_MISSING' } }
[Console]::Out.Write('ACL_OK')
'''

    def protect(self, path: Path, *, created: bool) -> None:
        if os.name != 'nt':
            raise RuntimeError('WINDOWS_ACL_REQUIRED')
        env = os.environ.copy()
        env['CODEX_PROFILE_ACL_CREATE'] = '1' if created else '0'
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', self._SCRIPT],
            input=str(path), text=True, encoding='utf-8', errors='strict',
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=20, check=False, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if completed.returncode != 0 or completed.stdout != 'ACL_OK':
            raise RuntimeError('PROFILE_ACL_UNVERIFIED')


class WindowsEdgeVerifier:
    """Require a local Microsoft-signed msedge.exe before handing it to executor."""

    _SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$securityManifest = [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Security.psd1')
Import-Module -Name $securityManifest -ErrorAction Stop
$p = [Console]::In.ReadToEnd().Trim()
$s = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $p
if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch '(^|,)\s*O=Microsoft Corporation(,|$)') { throw 'EDGE_SIGNATURE_INVALID' }
[Console]::Out.Write('EDGE_OK')
'''

    def verify(self, path: Path) -> bool:
        if (os.name != 'nt' or not path.is_absolute()
                or path.name.casefold() != 'msedge.exe' or not path.is_file()):
            return False
        if str(path).startswith(('\\\\', '//')) or any(
                part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction())
                for part in (path, *path.parents)):
            return False
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(path.drive + '\\') != 3:
            return False
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', self._SCRIPT],
            input=str(path), text=True, encoding='utf-8', errors='strict',
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=20, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return completed.returncode == 0 and completed.stdout == 'EDGE_OK'


class _OwnedPlaywrightHandle:
    """Only the persistent context returned by our own launch may be closed."""

    def __init__(self, driver, context, termination_proof):
        self._driver = driver
        self._context = context
        self._browser = context.browser
        self._termination_proof = termination_proof
        self._closed_confirmed = False

    def is_alive(self):
        if self._closed_confirmed:
            return False
        if self._browser is None:
            return None  # Cannot prove ownership or disconnect.
        try:
            return self._browser.is_connected() is True
        except Exception:
            return None

    def close(self):
        if self._closed_confirmed:
            return
        if self._browser is None:
            raise RuntimeError('BROWSER_OWNERSHIP_UNKNOWN')
        self._context.close()
        if self._browser.is_connected() is not False:
            raise RuntimeError('BROWSER_DISCONNECT_UNVERIFIED')
        self._driver.stop()
        if (self._termination_proof is None or
                self._termination_proof(self._driver, self._context, self._browser) is not True):
            raise RuntimeError('BROWSER_TERMINATION_UNVERIFIED')
        self._closed_confirmed = True


class PlaywrightEdgeExecutor:
    """Explicit optional launcher; importing this module never starts Playwright.

    Use only through DedicatedBrowserSession, which approves the profile path,
    ACL, and Edge signature. A failed startup retains its driver reference and
    leaves the session journal UNKNOWN; no process-name/PID cleanup is done.
    """

    def __init__(self, *, playwright_factory=None, termination_proof=None):
        self._factory = playwright_factory
        self._termination_proof = termination_proof
        self._drivers = []

    def start(self, edge_path: Path, profile_path: Path, url: str):
        if (url != DEEPSEEK_URL or not edge_path.is_absolute()
                or not profile_path.is_absolute()):
            raise ValueError('DEDICATED_BROWSER_ARGUMENTS_REQUIRED')
        if self._factory is None:
            # Fixed optional import, performed only for an explicit start.
            from playwright.sync_api import sync_playwright
            launcher = sync_playwright()
        else:
            launcher = self._factory()
        driver = launcher.start()
        self._drivers.append(driver)
        context = driver.chromium.launch_persistent_context(
            user_data_dir=str(profile_path), executable_path=str(edge_path),
            headless=False, timeout=30000,
        )
        # A dedicated profile may retain an existing tab after normal login.
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(DEEPSEEK_URL, wait_until='domcontentloaded', timeout=30000)
        return _OwnedPlaywrightHandle(driver, context, self._termination_proof)


class DedicatedBrowserSession:
    """One trusted profile/account pair and at most one owned live browser.

    executor.start(edge, profile, url) returns an owned handle with is_alive()
    and close(). A caller must provide this executor explicitly. A lost or
    ambiguous handle is quarantined until an operator resolves it out of band.
    """

    def __init__(self, profiles, profile_id: str, account_id: str, edge_exe,
                 *, acl=None, verifier=None, executor=None):
        self.profiles = profiles
        self.profile_id = reference(profile_id)
        self.account_id = reference(account_id)
        self.edge_exe = Path(edge_exe)
        self.acl = acl if acl is not None else WindowsProfileAcl()
        self.verifier = verifier if verifier is not None else WindowsEdgeVerifier()
        self.executor = executor
        self._handle = None
        self._occupancy = None
        self._quarantined = False
        self._mutex = threading.RLock()
        key = hashlib.sha256(self.profile_id.encode()).hexdigest()
        self._occupancy_path = None  # Derived from the approved canonical path.
        self._state_path = self.profiles.root / ('browser-' + key + '.json')

    def _outcome(self, status, reason):
        return BrowserOutcome(status, reason, self.profile_id, self.account_id)

    def _path(self):
        with self.profiles.acquire(self.profile_id, self.account_id, timeout=.1) as path:
            return _private_path(Path(path), self.profiles.root)

    def _lock_path(self, path):
        # Browser occupancy is a long-lived path lock. ProfileManager.acquire
        # remains a short operation lock, including the complete prepare edit.
        key = hashlib.sha256(str(path).casefold().encode()).hexdigest()
        return self.profiles.root / ('browser-path-' + key + '.lock')

    def _profile_key(self):
        return hashlib.sha256(self.profile_id.encode()).hexdigest()

    def _launch_attempts(self):
        return self.profiles.root.glob('browser-launch-attempt-' + self._profile_key() + '-*.json')

    @staticmethod
    def _write_immutable(path, value):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())

    def _record_launch_attempt(self):
        epoch = secrets.token_hex(16)
        path = self.profiles.root / ('browser-launch-attempt-' + self._profile_key() + '-' + epoch + '.json')
        self._write_immutable(path, {
            'kind': 'BROWSER_LAUNCH_ATTEMPT', 'profile_id': self.profile_id,
            'account_id': self.account_id, 'epoch': epoch,
            'requested_at': utc_now().isoformat(),
        })

    def _record_preparation_failure(self):
        # Provenance binds only a successfully written UNKNOWN marker.
        try:
            marker = self._unknown_marker_bytes()
            path = self.profiles.root / ('browser-preparation-failed-' + self._profile_key() + '.json')
            self._write_immutable(path, {
                'kind': 'PREPARATION_FAILED_BEFORE_LAUNCH',
                'profile_id': self.profile_id, 'account_id': self.account_id,
                'marker_sha256': hashlib.sha256(marker).hexdigest(),
                'recorded_at': utc_now().isoformat(),
            })
        except Exception:
            pass  # UNKNOWN stays isolated when provenance cannot be saved.

    def _validate_preparation_failure(self, marker):
        if any(self._launch_attempts()):
            raise ValueError('PROFILE_LAUNCH_ATTEMPT_EXISTS')
        path = self.profiles.root / ('browser-preparation-failed-' + self._profile_key() + '.json')
        if path.is_symlink():
            raise ValueError('PROFILE_RECOVERY_SOURCE_INVALID')
        with path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('PROFILE_RECOVERY_SOURCE_INVALID')
        try:
            value = json.loads(raw.decode('utf-8'))
            if (type(value) is not dict or set(value) != {
                    'kind', 'profile_id', 'account_id', 'marker_sha256', 'recorded_at'}
                    or value['kind'] != 'PREPARATION_FAILED_BEFORE_LAUNCH'
                    or value['profile_id'] != self.profile_id
                    or value['account_id'] != self.account_id
                    or value['marker_sha256'] != hashlib.sha256(marker).hexdigest()):
                raise ValueError()
            instant(value['recorded_at'])
        except (TypeError, UnicodeError, ValueError):
            raise ValueError('PROFILE_RECOVERY_SOURCE_INVALID') from None
        return value

    def _unknown_state(self):
        return {'state': 'UNKNOWN', 'profile_id': self.profile_id,
                'account_id': self.account_id}

    def _state(self):
        if self._quarantined or self._state_path.with_suffix('.tmp').exists():
            return self._unknown_state()
        try:
            value = json.loads(self._state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return self._unknown_state()
        if (type(value) is not dict or set(value) != {'state', 'profile_id', 'account_id'}
                or type(value['state']) is not str or value['state'] not in _STATES
                or value['profile_id'] != self.profile_id
                or value['account_id'] != self.account_id):
            return self._unknown_state()
        return value

    def _write_state(self, state):
        value = {'state': state, 'profile_id': self.profile_id,
                 'account_id': self.account_id}
        temporary = self._state_path.with_suffix('.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self._state_path)
        except OSError:
            self._quarantined = True
            raise RuntimeError('BROWSER_STATE_UNAVAILABLE') from None

    def _mark_unknown(self):
        self._quarantined = True
        try:
            self._write_state('UNKNOWN')
        except RuntimeError:
            pass

    def _unknown_marker_bytes(self):
        """Read only the small lifecycle marker, never browser profile content."""
        if (self._state_path.is_symlink() or self._state_path.with_suffix('.tmp').exists()
                or self._state_path.with_suffix('.tmp').is_symlink()):
            raise ValueError('PROFILE_RECOVERY_MARKER_INVALID')
        with self._state_path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('PROFILE_RECOVERY_MARKER_INVALID')
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError):
            raise ValueError('PROFILE_RECOVERY_MARKER_INVALID') from None
        if (type(value) is not dict or set(value) != {'state', 'profile_id', 'account_id'}
                or value['state'] != 'UNKNOWN' or value['profile_id'] != self.profile_id
                or value['account_id'] != self.account_id):
            raise ValueError('PROFILE_RECOVERY_MARKER_INVALID')
        return raw

    @staticmethod
    def _empty_directory(path):
        return path.is_dir() and not any(path.iterdir())

    def recover_unstarted(self, observer, *, control_gate=None, clock=utc_now):
        """Recover only an empty, proven-quiescent profile after failed prepare.

        Caller must already hold the global desktop lock and verify server
        stop/takeover plus local native quarantine. This never launches Edge.
        """
        with self._mutex:
            if (self._handle is not None or self._occupancy is not None
                    or not callable(observer) or not callable(control_gate)):
                return self._outcome('OUTCOME_UNKNOWN', 'PROFILE_RECOVERY_UNAVAILABLE')
            def require_gate():
                if control_gate() is not True:
                    raise ValueError('PROFILE_RECOVERY_CONTROL_STALE')
            try:
                require_gate()
                path = self._path()
                with resource_lock(self._lock_path(path), timeout=.1):
                    with self.profiles.acquire(self.profile_id, self.account_id, timeout=.1) as approved:
                        if (_private_path(Path(approved), self.profiles.root) != path
                                or not self._empty_directory(path)):
                            raise ValueError('PROFILE_RECOVERY_PATH_UNSAFE')
                        marker = self._unknown_marker_bytes()
                        source = self._validate_preparation_failure(marker)
                    marker_sha = hashlib.sha256(marker).hexdigest()
                    profile_key = hashlib.sha256(self.profile_id.encode()).hexdigest()
                    attempt_path = self.profiles.root / ('browser-recovery-attempt-' + profile_key + '.json')
                    # A durable one-shot record exists before any observation
                    # or ACL edit. Failure never licenses an automatic retry.
                    with attempt_path.open('x', encoding='utf-8') as stream:
                        json.dump({'profile_id': self.profile_id, 'account_id': self.account_id,
                                   'marker_sha256': marker_sha,
                                   'original_marker_b64': base64.b64encode(marker).decode('ascii'),
                                   'requested_at': clock().isoformat()}, stream, sort_keys=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    first_after = clock().isoformat()
                    require_gate()
                    first = observer(self.profile_id, self.account_id, path, marker_sha)
                    if not isinstance(first, ProfileQuiescence):
                        raise ValueError('PROFILE_RECOVERY_PROOF_INVALID')
                    first.validate(self.profile_id, self.account_id, path, marker_sha,
                                   after=first_after, now=clock())
                    require_gate()
                    # No ACL write is allowed before the first native proof.
                    with self.profiles.acquire(self.profile_id, self.account_id, timeout=.1) as approved:
                        if (_private_path(Path(approved), self.profiles.root) != path
                                or not self._empty_directory(path)
                                or self._unknown_marker_bytes() != marker):
                            raise ValueError('PROFILE_RECOVERY_CHANGED')
                        require_gate()
                        self.acl.protect(path, created=True)
                        require_gate()
                    second_after = clock().isoformat()
                    require_gate()
                    second = observer(self.profile_id, self.account_id, path, marker_sha)
                    if not isinstance(second, ProfileQuiescence):
                        raise ValueError('PROFILE_RECOVERY_PROOF_INVALID')
                    second.validate(self.profile_id, self.account_id, path, marker_sha,
                                    after=second_after, now=clock())
                    require_gate()
                    if (instant(second.observed_at) <= instant(first.observed_at)
                            or second.session_id != first.session_id):
                        raise ValueError('PROFILE_RECOVERY_PROOF_STALE')
                    with self.profiles.acquire(self.profile_id, self.account_id, timeout=.1) as approved:
                        if (_private_path(Path(approved), self.profiles.root) != path
                                or not self._empty_directory(path)
                                or self._unknown_marker_bytes() != marker):
                            raise ValueError('PROFILE_RECOVERY_CHANGED')
                        # Readback of the exact DACL is separate from the edit.
                        require_gate()
                        self.acl.protect(path, created=False)
                        require_gate()
                        evidence_path = self.profiles.root / (
                            'browser-recovery-' + profile_key + '-' + marker_sha + '.json')
                        evidence = {
                            'profile_id': self.profile_id, 'account_id': self.account_id,
                            'marker_sha256': marker_sha,
                            'preparation_failed_at': source['recorded_at'],
                            'original_marker_b64': base64.b64encode(marker).decode('ascii'),
                            'profile_path_sha256': first.profile_path_sha256,
                            'first_observed_at': first.observed_at,
                            'second_observed_at': second.observed_at,
                            'session_id': first.session_id,
                            'scan_scope': second.scan_scope,
                            'matching_browser_processes': 0,
                            'unknown_processes': 0,
                            'other_executor_processes': 0,
                            'recovered_at': clock().isoformat(),
                        }
                        # Exclusive create preserves old evidence if a prior
                        # recovery was interrupted before CLOSED was written.
                        with evidence_path.open('x', encoding='utf-8') as stream:
                            json.dump(evidence, stream, sort_keys=True)
                            stream.flush()
                            os.fsync(stream.fileno())
                        require_gate()  # Fresh gate immediately before CLOSED.
                        self._write_state('CLOSED')
                        self._quarantined = False
                    return self._outcome('CLOSED', 'UNSTARTED_PROFILE_RECOVERED')
            except Exception:
                # Keep the original UNKNOWN marker and any evidence artifact.
                # No automatic retry, process termination, or replacement path.
                return self._outcome('OUTCOME_UNKNOWN', 'PROFILE_RECOVERY_UNAVAILABLE')

    def prepare(self):
        """Only create an empty dedicated directory and verify its ACL."""
        with self._mutex:
            if self._handle is not None or self._quarantined:
                return self._outcome('OUTCOME_UNKNOWN', 'PREVIOUS_BROWSER_UNRESOLVED')
            try:
                path = self._path()
            except TimeoutError:
                return self._outcome('FAILED_BEFORE_ACTION', 'PROFILE_OCCUPIED')
            try:
                with resource_lock(self._lock_path(path), timeout=.1):
                    state = self._state()
                    if self._handle is not None or state is not None and state['state'] in _ACTIVE:
                        return self._outcome('OUTCOME_UNKNOWN', 'PREVIOUS_BROWSER_UNRESOLVED')
                    # The short ProfileManager lock covers the whole ACL edit.
                    with self.profiles.acquire(self.profile_id, self.account_id, timeout=.1) as approved:
                        if _private_path(Path(approved), self.profiles.root) != path:
                            return self._outcome('OUTCOME_UNKNOWN', 'PROFILE_PATH_CHANGED')
                        try:
                            missing = not path.exists()
                            if missing:
                                path.mkdir(parents=False, exist_ok=False)
                            if not path.is_dir():
                                raise ValueError('DEDICATED_PROFILE_REQUIRED')
                            # ProfileManager.register pre-creates an empty directory.
                            empty = not any(path.iterdir())
                        except OSError:
                            self._mark_unknown()
                            raise RuntimeError('PROFILE_PREPARE_FAILED') from None
                        try:
                            self.acl.protect(path, created=missing or empty)
                        except Exception:
                            self._mark_unknown()
                            self._record_preparation_failure()
                            raise
                    return self._outcome('PREPARED', 'PROFILE_READY')
            except TimeoutError:
                return self._outcome('FAILED_BEFORE_ACTION', 'PROFILE_OCCUPIED')

    def start(self):
        """Open once; login/verification is a human action in this browser."""
        with self._mutex:
            if self._handle is not None:
                try:
                    state = self._state()
                    if (not self._quarantined and state is not None
                            and state['state'] == 'RUNNING'
                            and self._handle.is_alive() is True):
                        return self._outcome('WAITING_HUMAN', 'BROWSER_REUSED')
                except Exception:
                    pass
                self._mark_unknown()
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_OWNERSHIP_UNKNOWN')
            if self._quarantined or self._occupancy is not None:
                return self._outcome('OUTCOME_UNKNOWN', 'PREVIOUS_BROWSER_UNRESOLVED')
            if self.executor is None:
                return self._outcome('FAILED_BEFORE_ACTION', 'BROWSER_EXECUTOR_REQUIRED')
            try:
                path = self._path()
            except TimeoutError:
                return self._outcome('FAILED_BEFORE_ACTION', 'PROFILE_OCCUPIED')
            self._occupancy_path = self._lock_path(path)
            try:
                self._occupancy = ExitStack()
                self._occupancy.enter_context(resource_lock(self._occupancy_path, timeout=.1))
            except TimeoutError:
                self._occupancy = None
                return self._outcome('FAILED_BEFORE_ACTION', 'PROFILE_OCCUPIED')
            try:
                state = self._state()
                if state is not None and state['state'] != 'CLOSED':
                    return self._outcome('OUTCOME_UNKNOWN', 'PREVIOUS_BROWSER_UNRESOLVED')
                if self._path() != path:
                    return self._outcome('OUTCOME_UNKNOWN', 'PROFILE_PATH_CHANGED')
                if not path.is_dir():
                    return self._outcome('FAILED_BEFORE_ACTION', 'PROFILE_NOT_PREPARED')
                self.acl.protect(path, created=False)
                if not self.verifier.verify(self.edge_exe):
                    return self._outcome('FAILED_BEFORE_ACTION', 'EDGE_NOT_VERIFIED')
                self._write_state('STARTING')
                # No executor call is possible without a durable attempt record.
                self._record_launch_attempt()
                try:
                    handle = self.executor.start(self.edge_exe, path, DEEPSEEK_URL)
                except Exception:
                    self._write_state('UNKNOWN')
                    return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_START_UNKNOWN')
                self._handle = handle
                try:
                    alive = handle.is_alive() is True
                except Exception:
                    alive = False
                if not alive:
                    self._write_state('UNKNOWN')
                    return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_OWNERSHIP_UNKNOWN')
                self._write_state('RUNNING')
                return self._outcome('WAITING_HUMAN', 'NORMAL_LOGIN_OR_SESSION_CHECK_REQUIRED')
            except Exception:
                # Any unexpected failure after taking occupancy is unresolved.
                # STARTING remains durable if the browser may have launched.
                self._mark_unknown()
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_START_UNKNOWN')
            finally:
                state = self._state()
                if self._handle is None and (state is None or state.get('state') not in _ACTIVE):
                    self._occupancy.close()
                    self._occupancy = None

    def close(self):
        """Close only an owned handle; uncertain termination keeps quarantine."""
        with self._mutex:
            if self._quarantined:
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_OWNERSHIP_UNKNOWN')
            if self._handle is None:
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_OWNERSHIP_UNKNOWN')
            try:
                self._handle.close()
                terminated = self._handle.is_alive() is False
            except Exception:
                terminated = False
            if not terminated:
                self._mark_unknown()
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_TERMINATION_UNKNOWN')
            try:
                self._write_state('CLOSED')
            except RuntimeError:
                return self._outcome('OUTCOME_UNKNOWN', 'BROWSER_STATE_UNAVAILABLE')
            self._handle = None
            if self._occupancy is not None:
                self._occupancy.close()
                self._occupancy = None
            return self._outcome('CLOSED', 'OWNED_BROWSER_CLOSED')
