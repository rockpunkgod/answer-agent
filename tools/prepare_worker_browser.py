"""Fixed dedicated Edge preparation. Actual invocation belongs to Luna only.

No business work, page reads, screenshot, authentication export, uploads, or
question submission occurs here. The visible browser is for normal human login.
The node startup phase precedes WorkerRuntime's per-task profile lock.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from helpdesk.locking import resource_lock
from helpdesk.worker_environment import ProfileManager
from helpdesk.worker_browser_session import DedicatedBrowserSession, PlaywrightEdgeExecutor
from helpdesk.worker_browser_recovery import WindowsProfileQuiescenceObserver
from helpdesk.worker_contracts import reference

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = 'deepseek-demo-dedicated'
ACCOUNT_ID = 'deepseek-login-unverified'
EDGE_CANDIDATES = (Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                   Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe'))
QUIESCENT_COMMAND_STATUSES = frozenset({
    'NOT_STARTED', 'SUCCEEDED_VERIFIED', 'FAILED_BEFORE_ACTION',
    'FAILED_AFTER_ACTION', 'STALE', 'CANCELLED',
})


def startup_allowed(value):
    return (isinstance(value, dict) and value.get('global_stop') is False
        and value.get('worker_boundary') is True
        and isinstance(value.get('accounts'), list) and isinstance(value.get('commands'), list)
        and all(isinstance(a, dict) and a.get('stop') is False
                and a.get('takeover') == 'AUTO_ACTIVE' and a.get('quarantined') is False
                and a.get('pause_reason') is None
                for a in value['accounts'])
        and all(isinstance(c, dict) and isinstance(c.get('status'), str)
                and c['status'] in QUIESCENT_COMMAND_STATUSES
                for c in value['commands']))


def recovery_allowed(value):
    # Global STOP is positive evidence even when no business account is yet
    # enrolled. Human-owned accounts alone cannot authorize unrelated profiles.
    # This admits preparation maintenance, never a GUI/browser startup.
    return (isinstance(value, dict) and value.get('global_stop') is True
        and value.get('worker_boundary') is True
        and isinstance(value.get('accounts'), list) and isinstance(value.get('commands'), list)
        and all(isinstance(a, dict) and a.get('stop') is True
                and a.get('quarantined') is False
                and isinstance(a.get('takeover'), str)
                and a.get('takeover') in {'AUTO_ACTIVE', 'OWNED'}
                for a in value['accounts'])
        and all(isinstance(c, dict) and isinstance(c.get('status'), str)
                and c['status'] in QUIESCENT_COMMAND_STATUSES
                for c in value['commands']))


def local_native_quiescent(root):
    """Read only local executor journals; uncertainty never clears quarantine."""
    state = Path(root) / 'data' / 'worker-runtime'
    try:
        for part in (state, *state.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                return False
        if not state.exists():
            return True
        if not state.is_dir():
            return False
        for index, entry in enumerate(state.iterdir()):
            if index >= 1000:
                return False
            if (entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction())
                    or entry.suffix == '.tmp' or entry.name.startswith(('quarantine-', 'account-pause-'))):
                return False
            if not entry.name.startswith('command-'):
                continue
            with entry.open('rb') as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                return False
            value = json.loads(raw)
            result = value.get('result') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or value.get('native_call_pending') is not False
                    or not isinstance(result, dict)
                    or result.get('native_call_pending') is not False
                    or result.get('status') not in QUIESCENT_COMMAND_STATUSES):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def control_snapshot():
    # Only the fixed local operator endpoint, no credentials or arbitrary URLs.
    with urlopen('http://127.0.0.1:8767/api/worker/control', timeout=5) as response:
        return json.loads(response.read(262145))


def _emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='仅准备专用Edge目录；显式open用于人工正常登录')
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--recover-preparation', action='store_true',
                        help='一次性恢复有明确准备失败来源的空目录；旧UNKNOWN不会被清除')
    args = parser.parse_args(argv)
    if args.open and args.recover_preparation:
        parser.error('恢复准备与人工登录必须分开；恢复不会自动取消停止状态')
    profile_id, account_id = reference(PROFILE_ID), reference(ACCOUNT_ID)
    edge = next((p for p in EDGE_CANDIDATES if p.is_file()), None)
    if edge is None:
        _emit({'status':'FAILED_BEFORE_ACTION','reason_code':'FIXED_EDGE_NOT_INSTALLED'})
        return 2
    try:
        # One desktop actor; never called within a Worker handler/profile lease.
        with resource_lock(ROOT/'data/windows-interactive-desktop.lock', timeout=1):
            def maintenance_allowed():
                gate = recovery_allowed if args.recover_preparation else startup_allowed
                return gate(control_snapshot()) and local_native_quiescent(ROOT)

            if not maintenance_allowed():
                _emit({'status':'FAILED_BEFORE_ACTION','reason_code':'STARTUP_CONTROL_NOT_AUTHORIZED'})
                return 2
            profile_root = ROOT/'data/worker-profiles'
            if args.recover_preparation and not profile_root.is_dir():
                _emit({'status':'OUTCOME_UNKNOWN','reason_code':'RECOVERY_PROFILE_MISSING'})
                return 2
            profiles = ProfileManager(profile_root)
            if not args.recover_preparation:
                profiles.register(profile_id,account_id,profiles.root/profile_id)
            session = DedicatedBrowserSession(profiles,profile_id,account_id,edge,
                executor=PlaywrightEdgeExecutor() if args.open else None)
            if args.recover_preparation:
                native_observer = WindowsProfileQuiescenceObserver()

                def guarded_observer(*bound):
                    # Both observations have a fresh server/local-state check.
                    # The enclosing global desktop lock stays owned throughout.
                    if not maintenance_allowed():
                        raise RuntimeError('RECOVERY_CONTROL_CHANGED')
                    return native_observer(*bound)

                outcome = session.recover_unstarted(guarded_observer,
                                                     control_gate=maintenance_allowed)
                _emit(dict(asdict(outcome), scope='FAILED_PREPARATION_MAINTENANCE',
                           account_identity_verified=False, gui_ready_verified=False))
                if outcome.status != 'CLOSED':
                    return 2
                return 0
            outcome = session.prepare()
            _emit(dict(asdict(outcome), scope='DEDICATED_BROWSER_PREPARATION',
                       account_identity_verified=False, gui_ready_verified=False))
            if outcome.status != 'PREPARED': return 2
            if not args.open: return 0
            # Recheck after directory and ACL preparation; startup never approves
            # a queued business action or assumes the user has logged in.
            if not maintenance_allowed():
                _emit({'status':'FAILED_BEFORE_ACTION','reason_code':'STARTUP_CONTROL_CHANGED'})
                return 2
            outcome = session.start()
            _emit(dict(asdict(outcome), scope='HUMAN_LOGIN_MAINTENANCE',
                       account_identity_verified=False, gui_ready_verified=False))
            if outcome.status != 'WAITING_HUMAN': return 2
        # Keep ownership, but release the desktop for the human. No periodic UI
        # read or input while login/verification is being handled manually.
        for line in sys.stdin:
            if line.strip() == 'status':
                _emit({'status':'WAITING_HUMAN','reason_code':'HUMAN_LOGIN_NOT_VERIFIED',
                       'profile_id':profile_id,'account_identity_verified':False})
            elif line.strip() == 'close':
                _emit(asdict(session.close()))
                return 0 if session._handle is None else 2
            else:
                _emit({'status':'WAITING_HUMAN','reason_code':'FIXED_MAINTENANCE_ACTION_REQUIRED'})
        return 2  # EOF cannot prove browser termination or authorize a restart.
    except Exception:
        _emit({'status':'OUTCOME_UNKNOWN' if args.open else 'FAILED_BEFORE_ACTION',
               'reason_code':'DEDICATED_BROWSER_ENVIRONMENT_UNCONFIRMED'})
        return 2


if __name__ == '__main__': raise SystemExit(main())
