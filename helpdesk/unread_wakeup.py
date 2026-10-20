"""Background unread hints, never messages, identity, time evidence or send authority."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from threading import Event, Lock, Thread
import time
from uuid import uuid4


class UnreadWakeMonitor:
    @staticmethod
    def validate_config(config):
        if not isinstance(config, dict) or type(config.get("enabled")) is not bool:
            raise ValueError("Explicit unread wake configuration required")
        if not config["enabled"]:
            return
        if not all(isinstance(config.get(k), str) and config[k].strip()
                   for k in ("reviewer", "verification_evidence")):
            raise ValueError("Reviewed unread indicator required")
        size, box = config.get("image_size"), config.get("indicator_box")
        if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                or not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]
                or (box[2]-box[0])*(box[3]-box[1]) > 20000):
            raise ValueError("Bounded window-relative unread indicator required")
        for key, default, low, high in (("poll_seconds", 5, 2, 60), ("repeat_seconds", 60, 30, 900),
                                      ("timeout_seconds", 8, 2, 15), ("min_red_pixels", 8, 1, 20000)):
            value = config.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid bounded unread monitor setting")
        if config.get("min_red_pixels", 8) > (box[2]-box[0])*(box[3]-box[1]):
            raise ValueError("Unread threshold exceeds indicator area")

    def __init__(self, config, wake, *, root=None, probe=None, clock=time.monotonic):
        self.validate_config(config)
        self.config = deepcopy(config)
        self.wake = wake
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.output = self.root / "data/private/unread-wakeup" / uuid4().hex / "current.png"
        self.probe = probe or self._probe
        self.clock = clock
        self.lock = Lock()
        self.stop_event = Event()
        self.thread = None
        self.last_signature = None
        self.last_wake = None
        self.recheck = False
        self.status = {"state": "NEVER_PROBED", "unread_hint": None, "error_type": None,
                       "wake_count": 0, "capture_at": None, "freshness_verified": False,
                       "message_source_verified": False, "can_send": False}

    def _probe(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(self.root/"tools/probe_wecom.ps1"),
                                 "-MainWindowOnly", "-Output", str(self.output)], cwd=self.root,
                                capture_output=True, shell=False, timeout=self.config.get("timeout_seconds", 8),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Unread capture unavailable")
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if (value.get("App") != "WXWork" or value.get("MainWindowOnly") is not True
                or value.get("CaptureBackend") != "PRINT_WINDOW" or value.get("CaptureResult") is not True
                or value.get("Minimized") is not False or Path(value.get("ImagePath", "")).resolve() != self.output):
            raise ValueError("Unread capture not verified")
        geometry = value.get("Geometry")
        if not isinstance(geometry, list) or len(geometry) != 4 or geometry[2:] != self.config["image_size"]:
            raise ValueError("Unread window layout changed")
        from PIL import Image
        with Image.open(self.output) as raw:
            if list(raw.size) != self.config["image_size"]:
                raise ValueError("Unread capture dimensions changed")
            image = raw.convert("RGB")
            if all(high-low <= 2 for low, high in image.getextrema()):
                raise ValueError("Unread capture blank")
            return self.indicator(image, self.config)

    @staticmethod
    def indicator(image, config):
        """Red mask only: no OCR, messages, recipients or timestamp inference."""
        if list(image.size) != config["image_size"]:
            raise ValueError("Unread indicator layout changed")
        pixels = image.crop(config["indicator_box"]).convert("RGB").tobytes()
        mask = bytes(int(r >= 180 and g <= 120 and b <= 140 and r >= g*1.4 and r >= b*1.3)
                     for r, g, b in zip(pixels[0::3], pixels[1::3], pixels[2::3]))
        return {"unread": sum(mask) >= config.get("min_red_pixels", 8), "signature": sha256(mask).hexdigest()}

    def sample_once(self):
        if not self.config["enabled"] or self.stop_event.is_set():
            return
        try:
            result = self.probe()
            if (not isinstance(result, dict) or type(result.get("unread")) is not bool
                    or not isinstance(result.get("signature"), str) or not result["signature"]):
                raise ValueError("Invalid unread probe result")
            if self.stop_event.is_set():
                return
            instant = self.clock()
            changed = result["signature"] != self.last_signature
            due = self.last_wake is None or instant-self.last_wake >= self.config.get("poll_seconds", 5)
            repeat = self.last_wake is not None and instant-self.last_wake >= self.config.get("repeat_seconds", 60)
            fire = result["unread"] and due and (changed or repeat or self.recheck)
            if fire:
                self.wake()  # Only an Event hint; consumer still verifies its own message source.
                self.last_wake = instant
            self.last_signature = result["signature"]
            self.recheck = bool(result["unread"] and not fire and (changed or self.recheck))
            with self.lock:
                self.status.update(state="HINT_CAPTURED", unread_hint=result["unread"], error_type=None,
                                   capture_at=datetime.now(timezone.utc).isoformat())
                self.status["wake_count"] += int(fire)
        except Exception as error:
            self.recheck = True
            with self.lock:
                self.status.update(state="UNAVAILABLE", unread_hint=None, error_type=type(error).__name__)

    def snapshot(self):
        with self.lock:
            return dict(self.status, worker_alive=bool(self.thread and self.thread.is_alive()))

    def start(self):
        if not self.config["enabled"] or self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.recheck = True
        self.last_wake = None
        with self.lock:
            self.status.update(state="STARTING", unread_hint=None)
        self.thread = Thread(target=self._work, daemon=True, name="wecom-unread-hints")
        self.thread.start()

    def _work(self):
        try:
            while not self.stop_event.is_set():
                self.sample_once()
                self.stop_event.wait(self.config.get("poll_seconds", 5))
        finally:
            with self.lock:
                self.status["state"] = "STOPPED"

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.status["state"] = "STOPPING"
        if self.thread:
            self.thread.join(timeout=self.config.get("timeout_seconds", 8)+2)
        if not self.thread or not self.thread.is_alive():
            with self.lock:
                self.status["state"] = "STOPPED"
