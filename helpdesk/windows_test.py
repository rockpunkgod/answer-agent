"""One-session, WeCom-only visual smoke test. Never a production/student sender.

The user selected the test contact in WeCom. A process/window/avatar fingerprint
pins THAT observed session, not a claimed global platform ID. No name search or
automatic target reacquisition occurs here. Pins expire and must be re-inspected.
"""
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

from .delivery import PreflightFailure, NotSubmitted
from .test_routing import TestRecipient, TestRoutingPolicy


class Mouse(ctypes.Structure):
    _fields_ = [("dx",w.LONG),("dy",w.LONG),("mouseData",w.DWORD),("dwFlags",w.DWORD),("time",w.DWORD),("extra",ctypes.c_size_t)]


class Keyboard(ctypes.Structure):
    _fields_ = [("vk",w.WORD),("scan",w.WORD),("flags",w.DWORD),("time",w.DWORD),("extra",ctypes.c_size_t)]


class Data(ctypes.Union):
    _fields_ = [("mi",Mouse),("ki",Keyboard)]


class Input(ctypes.Structure):
    _fields_ = [("type",w.DWORD),("data",Data)]


class WeComVisualProbe:
    simulated = False
    test_only = True

    def __init__(self, workspace: Path, pin: dict):
        if sys.platform != "win32":
            raise ValueError("Windows only")
        self.root = workspace.resolve()
        self.pin = pin
        self.lock_path = str(self.root / "data/windows-interactive-desktop.lock")
        self.user = ctypes.windll.user32
        self.user.SetProcessDPIAware()
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowRect.argtypes = [w.HWND,ctypes.POINTER(w.RECT)]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND,ctypes.POINTER(w.DWORD)]
        self.image = self.root / "data/private/wecom-probe-frame.png"
        self.policy = TestRoutingPolicy(TestRecipient("wecom", pin["session_key"], "苇中鹤",
            "Operator-selected WeCom chat; header/sidebar OCR plus current-session avatar fingerprint; NOT a platform account ID",
            datetime.fromisoformat(pin["verified_at"])), enabled=True)

    def _ps(self, script, *args):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(self.root / "tools" / script), *map(str,args)],
            cwd=self.root, capture_output=True, encoding="utf-8-sig", errors="strict", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise PreflightFailure("READONLY_WINDOW_CHECK_FAILED")
        return json.loads(result.stdout.strip())

    def _geometry(self):
        if time.time() > self.pin["expires_at"]:
            raise PreflightFailure("TEST_SESSION_PIN_EXPIRED")
        handle = self.pin["handle"]
        if self.user.GetForegroundWindow() != handle:
            raise PreflightFailure("WECOM_NOT_FOREGROUND")
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value != self.pin["process_id"]:
            raise PreflightFailure("PROCESS_CHANGED")
        rect = w.RECT()
        if not self.user.GetWindowRect(handle, ctypes.byref(rect)) or (rect.right-rect.left,rect.bottom-rect.top)!=(2019,975):
            raise PreflightFailure("UNVERIFIED_WINDOW_GEOMETRY")
        return rect

    def _frame(self, expected=""):
        from PIL import Image
        self._geometry()
        captured = self._ps("probe_wecom.ps1", "-Output", "data/private/wecom-probe-frame.png", "-CaptureVisible")
        if captured["Handle"] != self.pin["handle"]:
            raise PreflightFailure("WINDOW_CHANGED")
        args = ["-ImagePath", self.image]
        if expected:
            args += ["-ExpectedText", expected]
        recognized = self._ps("ocr_window.ps1", *args)
        if not recognized["header_match"] or not recognized["sidebar_match"]:
            raise PreflightFailure("TEST_RECIPIENT_NOT_VISIBLE")
        with Image.open(self.image) as image:
            avatar = sha256(image.crop((114,110,174,174)).convert("RGB").tobytes()).hexdigest()
            if avatar != self.pin["avatar_hash"]:
                raise PreflightFailure("TEST_CONTACT_AVATAR_CHANGED")
            editor = image.crop((500,810,1450,899)).convert("RGB")
            recognized["editor_blank"] = sum(min(pixel)<245 for pixel in editor.getdata()) < 30
            # The operator-approved probe screenshot has text ending before x1203;
            # the blinking caret is at x1204. This fingerprint is only for that fixed draft.
            recognized["draft_pixels"] = sha256(image.crop((500,810,1203,848)).convert("RGB").tobytes()).hexdigest()
        self._geometry()
        return recognized

    def preflight(self, message):
        if message.outbox_id != self.pin.get("outbox_id"):
            raise PreflightFailure("PROBE_OUTBOX_NOT_PINNED")
        self.policy.resolve(message).require_selected_target("wecom", self.pin["session_key"])
        if (not message.body.startswith("TEST ") or len(message.body)>160
                or not message.body.isascii() or not message.body.isprintable()):
            raise PreflightFailure("ONLY_FIXED_NON_STUDENT_TEST_TEXT_ALLOWED")
        frame = self._frame(message.body)
        if self.pin.get("operator_confirmed_draft"):
            if not frame["editor_probe_id_match"] or frame["draft_pixels"] != self.pin.get("approved_draft_pixels"):
                raise PreflightFailure("OPERATOR_APPROVED_DRAFT_CHANGED")
        elif not frame["editor_blank"]:
            raise PreflightFailure("COMPOSE_AREA_NOT_EMPTY")

    def _click(self, x, y):
        rect = self._geometry()
        self.user.SetCursorPos(rect.left+x, rect.top+y)
        self.user.mouse_event(2,0,0,0,0)
        self.user.mouse_event(4,0,0,0,0)

    def _prepare_draft(self, message):
        self.preflight(message)
        if self.pin.get("operator_confirmed_draft"):
            return
        self._click(750,835)
        events=[]
        for ch in message.body:
            for flag in (4,6):
                events.append(Input(1,Data(ki=Keyboard(0,ord(ch),flag,0,0))))
        self._geometry()
        buffer=(Input*len(events))(*events)
        if self.user.SendInput(len(events),buffer,ctypes.sizeof(Input)) != len(events):
            raise NotSubmitted("COMPOSER_INPUT_INCOMPLETE")
        # No Enter or click until the exact probe appears inside the compose area.
        typed=self._frame(message.body)
        if not typed["editor_match"]:
            raise NotSubmitted("DRAFT_VERIFICATION_FAILED")

    def send(self, message):
        try:
            self._prepare_draft(message)
        except NotSubmitted:
            raise
        except Exception as exc:
            raise NotSubmitted("PRE_SUBMISSION_CHECK_FAILED") from exc
        # Do not catch failures below as NotSubmitted: even a failed mouse call
        # could have caused the external effect, and requires read-only recovery.
        self._click(1500,922)
        return self.reconcile(message)

    def reconcile(self, message):
        # Read-only bounded polling. Never types, focuses, clicks, or resubmits.
        deadline=time.monotonic()+8
        while True:
            try:
                frame=self._frame(message.body)
                if (frame["receipt_match"] or frame["receipt_probe_id_match"]) and not frame["editor_probe_id_match"]:
                    return {"confirmed":True,"simulated":False,"body_hash":message.body_hash,
                            "confirmed_at":datetime.now(timezone.utc).isoformat(),"target_platform":"wecom",
                            "target_name":"苇中鹤","session_key":self.pin["session_key"],
                            "evidence_sha256":sha256(self.image.read_bytes()).hexdigest(),"scope":"SINGLE_SESSION_TEST_ONLY",
                            "verification":"target header + avatar + unique probe ID outside composer; not a read receipt"}
            except PreflightFailure:
                break
            if time.monotonic()>=deadline:
                break
            time.sleep(.2)
        return {"confirmed":False,"simulated":False,"reason":"TEST_UI_RESULT_UNKNOWN"}
