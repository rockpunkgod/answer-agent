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
from .unread_wakeup import UnreadWakeMonitor
from .personal_wechat_polling import PersonalWeChatPoller,run_personal_collection


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
        self.collection_only = (self.config or {}).get('source',{}).get('kind')=='personal_wechat_local'
        self.factory = source_factory or (PersonalWeChatPoller if self.collection_only else load_source)
        self.runner = runner or (run_personal_collection if self.collection_only else run_live_iteration)
        self.lock = Lock()
        self.stop_event = Event()
        self.wake_event = Event()
        self.thread = None
        self.unread_monitor = None
        self.state = "IDLE"
        self.health = "NEVER_SYNCED"
        self.last_result = None
        self.last_success_at = None
        self.error_type = None
        self.logs = deque(maxlen=30)

    def _snapshot(self):
        alive = bool(self.thread and self.thread.is_alive())
        return {"state": self.state, "health": self.health,
            "running": alive and self.state in ("RUNNING", "WAITING"), "worker_alive": alive,
            "last_success_at": self.last_success_at, "last_result": deepcopy(self.last_result),
            "error_type": self.error_type, "logs": list(self.logs),
            "processing_mode": "ACK_ONLY", "sync_mode": "LIVE", "student_send_enabled": False,
            "collection_only": self.collection_only,
            "trigger_mode": (self.config or {}).get("collector", {}).get("trigger_mode", "POLL"),
            "wake_pending": self.wake_event.is_set() and not self.stop_event.is_set(),
            "unread_wakeup": self.unread_monitor.snapshot() if self.unread_monitor else None,
            "notification_listener_connected": False}

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
            trigger_mode = settings.get("trigger_mode", "POLL")
            if trigger_mode not in ("POLL", "SIGNAL"):
                self.state, self.health, self.error_type = "BLOCKED", "SOURCE_UNAVAILABLE", "InvalidTriggerMode"
                return self._snapshot()
            interval = settings.get("poll_seconds", 10)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 3600:
                self.state, self.health, self.error_type = "BLOCKED", "SOURCE_UNAVAILABLE", "InvalidPollInterval"
                self._log("START_BLOCKED", health=self.health, error_type=self.error_type)
                return self._snapshot()
            wake_config = settings.get("unread_wakeup")
            try:
                if wake_config is not None:
                    UnreadWakeMonitor.validate_config(wake_config)
                    if wake_config["enabled"] and trigger_mode != "SIGNAL":
                        raise ValueError("Unread hints require SIGNAL mode")
                source = self.factory(self.config)
            except Exception as error:
                self.state, self.health, self.error_type = "BLOCKED", failure_health(error, self.config), type(error).__name__
                self._log("START_BLOCKED", health=self.health, error_type=self.error_type)
                return self._snapshot()
            self.unread_monitor = (UnreadWakeMonitor(wake_config, self.wake_event.set)
                if wake_config and wake_config["enabled"] else None)
            self.stop_event.clear()
            self.wake_event.clear()
            self.state, self.health, self.error_type = "STARTING", "WAITING_FOR_FIRST_SYNC", None
            self._log("LOOP_STARTING")
            tick_settings = deepcopy(settings)
            tick_settings["max_pages"] = 1
            self.thread = Thread(target=self._work, args=(source, tick_settings, interval), daemon=True,
                name="ack-only-message-collector")
            self.thread.start()
            return self._snapshot()

    def request_sync(self):
        """Coalesce hints only; caller cannot supply messages, targets or commands.

        A wake does not assert a new message exists or authorize desktop input.
        The configured source and existing ACK gates remain authoritative.
        """
        with self.lock:
            if not self.thread or not self.thread.is_alive() or self.stop_event.is_set():
                return {**self._snapshot(), "wake_accepted": False}
            self.wake_event.set()
            return {**self._snapshot(), "wake_accepted": True}

    def _work(self, source, settings, interval):
        try:
            store = CollectorStore(Path(settings.get("database", "data/messages.db")))
            signal_mode = settings.get("trigger_mode", "POLL") == "SIGNAL"
            if signal_mode:
                with self.lock:
                    self.state, self.health = "WAITING", "WAITING_FOR_SIGNAL"
            if self.unread_monitor:
                self.unread_monitor.start()
            while not self.stop_event.is_set():
                if signal_mode:
                    self.wake_event.wait()
                    if self.stop_event.is_set():
                        break
                # Clear before work, so a signal arriving during the bounded
                # source call is retained for a subsequent iteration.
                self.wake_event.clear()
                failed = False
                try:
                    result = self.runner(store, source, settings)
                    with self.lock:
                        self.last_result = result
                        self.last_success_at = stamp()
                        self.state = "STOPPING" if self.stop_event.is_set() else "RUNNING"
                        self.health, self.error_type = "HEALTHY", None
                        self._log("LIVE_COLLECTION_COMMITTED" if self.collection_only else "LIVE_SYNC_AND_ACK_DRAIN_COMMITTED", inserted_count=result["sync"]["inserted_count"],
                            events_processed=result["events_processed"])
                except Exception as error:
                    failed = True
                    fatal = self.collection_only or isinstance(error, (ArchiveNotConfigured, ArchiveProtocolError, CursorExpired))
                    with self.lock:
                        self.state = "BLOCKED" if fatal else "DEGRADED"
                        self.health = failure_health(error, self.config) if fatal else "FAILED"
                        self.error_type = type(error).__name__
                        self._log("SYNC_OR_DRAIN_FAILED", health=self.health, error_type=self.error_type)
                    if fatal:
                        break
                # Continue a paged backlog immediately, but always pass through
                # the stop check after one committed page and its short drain.
                has_more = not failed and bool(self.last_result and self.last_result.get("sync", {}).get("has_more"))
                if signal_mode:
                    if has_more:
                        self.wake_event.set()
                    if failed and self.stop_event.wait(interval):
                        break  # Bound retry rate even when hints are noisy.
                    with self.lock:
                        if not failed and not self.stop_event.is_set():
                            self.state = "WAITING"
                    continue
                if not has_more:
                    self.wake_event.wait(interval)

        except Exception as error:
            with self.lock:
                self.state, self.health, self.error_type = "BLOCKED", "SOURCE_UNAVAILABLE", type(error).__name__
                self._log("LOOP_FAILED", health=self.health, error_type=self.error_type)
        finally:
            if self.unread_monitor:
                self.unread_monitor.stop()
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
            self.wake_event.set()
            if self.thread and self.thread.is_alive():
                self.state = "STOPPING"
            elif self.state != "BLOCKED":
                self.state = "STOPPED"
            worker = self.thread
        if worker:
            worker.join(timeout=wait_seconds)
        return self.snapshot()
