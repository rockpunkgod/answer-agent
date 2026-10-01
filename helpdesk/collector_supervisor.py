"""Server-owned, controllable LIVE polling; never sends or invokes teaching.

Configuration and source factory are supplied by the local server, never HTTP.
Stopping waits for the current bounded source call; only committed cursors count.
"""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from pathlib import Path
from .collector_cli import load_source, run_live_iteration
from .collector_storage import CollectorStore
from .message_sources import CursorExpired
from .wecom_archive import ArchiveNotConfigured, ArchiveProtocolError


def stamp():
    return datetime.now(timezone.utc).isoformat()


def failure_health(error, config):
    auth = (config or {}).get("archive", {})
    authorized = all(auth.get(key) is True for key in
        ("enabled", "admin_authorized", "member_scope_verified", "consent_verified")) and bool(auth.get("evidence_reference"))
    if isinstance(error, ArchiveNotConfigured) and not authorized:
        return "NEEDS_ADMIN_CONFIGURATION"
    if isinstance(error, CursorExpired):
        return "NEEDS_REVIEW"
    return "SOURCE_UNAVAILABLE"


class CollectorSupervisor:
    def __init__(self, config, *, source_factory=None, runner=None):
        self.config = deepcopy(config) if config is not None else None
        self.factory = source_factory or load_source
        self.runner = runner or run_live_iteration
        self.lock = Lock()
        self.stop_event = Event()
        self.thread = None
        self.state = "IDLE"
        self.health = "NEVER_SYNCED"
        self.last_result = None
        self.last_success_at = None
        self.error_type = None
        self.logs = deque(maxlen=30)

    def _snapshot(self):
        alive = bool(self.thread and self.thread.is_alive())
        return {"state": self.state, "health": self.health,
            "running": alive and self.state == "RUNNING", "worker_alive": alive,
            "last_success_at": self.last_success_at, "last_result": deepcopy(self.last_result),
            "error_type": self.error_type, "logs": list(self.logs),
            "processing_mode": "ACK_ONLY", "sync_mode": "LIVE", "student_send_enabled": False}

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _log(self, event, **known):
        self.logs.append({"at": stamp(), "event": event, **known})

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {**self._snapshot(), "resubmitted": False}
            if not self.config or self.config.get("collector", {}).get("processing_mode") != "ACK_ONLY":
                self.state, self.health, self.error_type = "BLOCKED", "NEEDS_ADMIN_CONFIGURATION", "ExplicitAckOnlyConfigurationRequired"
                self._log("START_BLOCKED", health=self.health, error_type=self.error_type)
                return self._snapshot()
            settings = self.config["collector"]
            interval = settings.get("poll_seconds", 10)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 3600:
                self.state, self.health, self.error_type = "BLOCKED", "SOURCE_UNAVAILABLE", "InvalidPollInterval"
                self._log("START_BLOCKED", health=self.health, error_type=self.error_type)
                return self._snapshot()
            try:
                source = self.factory(self.config)
            except Exception as error:
                self.state, self.health, self.error_type = "BLOCKED", failure_health(error, self.config), type(error).__name__
                self._log("START_BLOCKED", health=self.health, error_type=self.error_type)
                return self._snapshot()
            self.stop_event.clear()
            self.state, self.health, self.error_type = "STARTING", "WAITING_FOR_FIRST_SYNC", None
            self._log("LOOP_STARTING")
            tick_settings = deepcopy(settings)
            tick_settings["max_pages"] = 1
            self.thread = Thread(target=self._work, args=(source, tick_settings, interval), daemon=True,
                name="ack-only-message-collector")
            self.thread.start()
            return self._snapshot()

    def _work(self, source, settings, interval):
        try:
            store = CollectorStore(Path(settings.get("database", "data/messages.db")))
            while not self.stop_event.is_set():
                try:
                    result = self.runner(store, source, settings)
                    with self.lock:
                        self.last_result = result
                        self.last_success_at = stamp()
                        self.state = "STOPPING" if self.stop_event.is_set() else "RUNNING"
                        self.health, self.error_type = "HEALTHY", None
                        self._log("LIVE_SYNC_AND_ACK_DRAIN_COMMITTED", inserted_count=result["sync"]["inserted_count"],
                            events_processed=result["events_processed"])
                except Exception as error:
                    fatal = isinstance(error, (ArchiveNotConfigured, ArchiveProtocolError, CursorExpired))
                    with self.lock:
                        self.state = "BLOCKED" if fatal else "DEGRADED"
                        self.health = failure_health(error, self.config) if fatal else "FAILED"
                        self.error_type = type(error).__name__
                        self._log("SYNC_OR_DRAIN_FAILED", health=self.health, error_type=self.error_type)
                    if fatal:
                        break
                # Continue a paged backlog immediately, but always pass through
                # the stop check after one committed page and its short drain.
                wait_seconds = 0 if self.last_result and self.last_result.get("sync", {}).get("has_more") and self.state == "RUNNING" else interval
                if self.stop_event.wait(wait_seconds):
                    break
        except Exception as error:
            with self.lock:
                self.state, self.health, self.error_type = "BLOCKED", "SOURCE_UNAVAILABLE", type(error).__name__
                self._log("LOOP_FAILED", health=self.health, error_type=self.error_type)
        finally:
            close = getattr(source, "close", None)
            if not callable(close):
                close = getattr(getattr(source, "transport", None), "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    with self.lock:
                        self._log("CLOSE_FAILED", error_type=type(error).__name__)
            with self.lock:
                if self.state != "BLOCKED":
                    self.state = "STOPPED"
                self._log("LOOP_ENDED", cursor_preserved=True)

    def stop(self, *, wait_seconds=0.2):
        with self.lock:
            self.stop_event.set()
            if self.thread and self.thread.is_alive():
                self.state = "STOPPING"
            elif self.state != "BLOCKED":
                self.state = "STOPPED"
            worker = self.thread
        if worker:
            worker.join(timeout=wait_seconds)
        return self.snapshot()
