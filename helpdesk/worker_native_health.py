"""Fixed Windows-MCP metadata seam. No application identity is inferred here.

The business server must permit each observation. The native process is owned by
the node, never restarted or replayed after an uncertain response. This provider
does not take a second desktop lock: WorkerRuntime already holds it while
rechecking a call. It performs no focus, clipboard, page, or input operation.
"""
import threading
import time

from .worker_contracts import HealthState, WorkerHealth, instant, reference, utc_now
from .windows_worker_probe import (DESKTOP_STATUS_COMMAND, build_worker_health,
                                   parse_desktop_record)


def metadata_reads_allowed(value, worker_id):
    return (isinstance(value, dict) and value.get('worker_id') == worker_id
            and value.get('desktop_reads_allowed') is True)


class WindowsMCPHealthProvider:
    def __init__(self, mcp, api, *, worker_id, account_id, platform='wecom',
                 app_provider=None, clock=utc_now, monotonic=time.monotonic,
                 cache_seconds=2):
        if platform not in {'wecom', 'deepseek'}:
            raise ValueError('KNOWN_WORKER_PLATFORM_REQUIRED')
        if type(cache_seconds) not in (int, float) or not 0 <= cache_seconds <= 5:
            raise ValueError('BOUNDED_HEALTH_CACHE_REQUIRED')
        self.mcp, self.api = mcp, api
        self.worker_id, self.account_id = reference(worker_id), reference(account_id)
        self.platform, self.app_provider = platform, app_provider
        self.clock, self.monotonic, self.cache_seconds = clock, monotonic, cache_seconds
        self._mutex = threading.RLock()
        self._status = None
        self._cached_at = None
        self.last_reason = 'NOT_OBSERVED'
        self.native_metadata_connected = False
        self.last_attempt_id = None

    def _unavailable(self, *, connected, pending=False):
        return WorkerHealth(self.worker_id, self.account_id, connected, False, False,
            HealthState.UNKNOWN, None, None, None, self.clock().isoformat(), pending)

    def __call__(self):
        with self._mutex:
            # A cached health result never overrides human ownership or stop.
            try:
                allowed = metadata_reads_allowed(self.api.worker_state(self.worker_id), self.worker_id)
            except Exception:
                self.last_reason = 'SERVER_UNAVAILABLE'
                return self._unavailable(connected=False, pending=bool(self.mcp.uncertain))
            if not allowed:
                self.last_reason = 'DESKTOP_READS_NOT_AUTHORIZED'
                return self._unavailable(connected=True, pending=bool(self.mcp.uncertain))
            if self.mcp.uncertain:
                self.last_reason = 'ORIGINAL_NATIVE_RESPONSE_PENDING'
                return self._unavailable(connected=True, pending=True)
            now = self.clock()
            fresh = (self._status is not None and self._cached_at is not None
                and 0 <= self.monotonic() - self._cached_at <= self.cache_seconds
                and 0 <= (now - instant(self._status.observed_at)).total_seconds() <= 5)
            if not fresh:
                try:
                    record = self.mcp.call('PowerShell', {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10})
                    self.last_attempt_id = record.get('attempt_id')
                    self._status = parse_desktop_record(record, observed_after=now.isoformat(), now=self.clock())
                    self._cached_at = self.monotonic()
                    self.native_metadata_connected = True
                except Exception:
                    self._status = None
                    self.last_reason = 'DESKTOP_METADATA_UNCONFIRMED'
                    return self._unavailable(connected=True, pending=bool(self.mcp.uncertain))
            try:
                app = self.app_provider() if self.app_provider is not None else None
                value = build_worker_health(self._status, worker_id=self.worker_id,
                    account_id=self.account_id, app_observation=app,
                    expected_platform=self.platform, now=self.clock())
            except Exception:
                self.last_reason = 'APPLICATION_IDENTITY_UNCONFIRMED'
                return self._unavailable(connected=True)
            self.last_reason = ('APPLICATION_DETECTOR_NOT_CONNECTED' if app is None
                else 'HEALTH_OBSERVED' if value.gui_ready else 'APPLICATION_OR_DESKTOP_NOT_READY')
            return value
