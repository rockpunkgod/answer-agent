"""Local environment policy. No browser launch, credentials or GUI probing here.

Observations must come from the trusted worker control detector, never page text
or a language model. The native detector remains an explicit integration seam.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from .locking import resource_lock
from .worker_contracts import HealthState, reference


def _unlinked_local_path(value):
    """Check the original path before resolve can hide a junction or symlink.

    Only filesystem metadata on the path and its parents is inspected. No
    browser contents or authentication files are opened.
    """
    if str(value).startswith(('\\\\', '//')):
        raise ValueError('LOCAL_DEDICATED_PROFILE_REQUIRED')
    original = Path(value).absolute()
    if any(part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction())
           for part in (original, *original.parents)):
        raise ValueError('PROFILE_PATH_CHANGED')
    return original.resolve()


def classify_health(observation):
    """Classify trusted control booleans; arbitrary text is neither read nor logged."""
    keys = {'trusted_controls', 'connected', 'interactive_desktop', 'desktop_unlocked',
            'login_control', 'verification_control', 'rate_limit_control',
            'access_denied_control', 'expected_page_controls'}
    if (not isinstance(observation, dict) or set(observation) != keys
            or any(type(observation[k]) is not bool for k in keys)
            or not observation['trusted_controls']):
        return HealthState.UNKNOWN
    if not all(observation[k] for k in ('connected', 'interactive_desktop', 'desktop_unlocked')):
        return HealthState.DESKTOP_UNAVAILABLE
    for key, state in [('access_denied_control', HealthState.ACCESS_DENIED),
                       ('verification_control', HealthState.VERIFICATION_REQUIRED),
                       ('login_control', HealthState.LOGIN_REQUIRED),
                       ('rate_limit_control', HealthState.RATE_LIMITED)]:
        if observation[key]:
            return state
    return HealthState.HEALTHY if observation['expected_page_controls'] else HealthState.PAGE_CHANGED


class ProfileManager:
    """Explicit dedicated local profiles; registry never contains authentication.

    Paths are local worker configuration, not command parameters or result fields.
    No existing profile is copied, scanned or returned to a server.
    """
    def __init__(self, dedicated_root):
        self.root = _unlinked_local_path(dedicated_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = self.root / 'profile-registry.json'
        self.lock = self.root / 'profile-registry.lock'

    def _read(self):
        return json.loads(self.registry.read_text(encoding='utf-8')) if self.registry.exists() else {}

    def register(self, profile_id, account_id, path):
        reference(profile_id)
        reference(account_id)
        candidate = _unlinked_local_path(path)
        # Dedicated root excludes the user's normal User Data tree, including
        # symlink/junction escape. Never inspect credentials inside a profile.
        if (candidate == self.root or not candidate.is_relative_to(self.root)
                or any(p.casefold() in {'default', 'user data', 'profile 1'} for p in candidate.parts)):
            raise ValueError('DEDICATED_PROFILE_REQUIRED')
        with resource_lock(self.lock):
            if _unlinked_local_path(path) != candidate:
                raise ValueError('PROFILE_PATH_CHANGED')
            entries = self._read()
            value = {'account_id': account_id, 'local_path': str(candidate)}
            if profile_id in entries and entries[profile_id] != value:
                raise ValueError('PROFILE_BINDING_CHANGED')
            if any(k != profile_id and (v['local_path'].casefold() == str(candidate).casefold()
                   or v['account_id'] == account_id) for k, v in entries.items()):
                raise ValueError('DUPLICATE_PROFILE_OR_ACCOUNT')
            candidate.mkdir(parents=True, exist_ok=True)
            entries[profile_id] = value
            temporary = self.registry.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                stream.write(json.dumps(entries))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.registry)
        return {'profile_id': profile_id, 'account_id': account_id, 'dedicated': True}

    @contextmanager
    def acquire(self, profile_id, account_id, *, timeout=1):
        reference(profile_id)
        reference(account_id)
        with resource_lock(self.lock, timeout=timeout):
            value = self._read().get(profile_id)
            if not value or value['account_id'] != account_id:
                raise ValueError('PROFILE_NOT_REGISTERED')
            path = _unlinked_local_path(value['local_path'])
            if not path.is_relative_to(self.root) or path == self.root:
                raise ValueError('PROFILE_PATH_CHANGED')
        lock_name = hashlib.sha256(str(path).casefold().encode()).hexdigest()
        with resource_lock(self.root / (lock_name + '.lock'), timeout=timeout):
            if _unlinked_local_path(value['local_path']) != path:
                raise ValueError('PROFILE_PATH_CHANGED')
            yield path  # Only trusted local browser factory consumes this path.


def minimal_state(health):
    """Allowlist instead of redacting arbitrary captures after recording them."""
    return {'worker_id': health.worker_id, 'account_id': health.account_id,
            'state': str(health.state), 'observed_at': health.observed_at,
            'native_call_pending': health.native_call_pending}
