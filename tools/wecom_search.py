"""Operator-driven read-only navigation to the user-authorized test contact.

Does not type into a chat editor or send any message. Coordinates are relative to
the observed WXWork window and restricted to the verified search-box rectangle.
"""
import ctypes
from ctypes import wintypes as w
import subprocess
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.locking import resource_lock


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", UNION)]


def main():
    result = subprocess.run(["powershell", "-NoProfile", "-Command",
        "Get-Process -Name WXWork | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1 | ForEach-Object { $_.MainWindowHandle.ToInt64() }"], capture_output=True, text=True, check=True)
    handle = int(result.stdout.strip())
    user = ctypes.windll.user32
    user.SetProcessDPIAware()
    user.GetForegroundWindow.restype = w.HWND
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    with resource_lock(Path(__file__).resolve().parents[1] / "data/windows-interactive-desktop.lock"):
        user.ShowWindow(handle, 9)
        user.SetForegroundWindow(handle)
        deadline = time.monotonic() + 2
        while user.GetForegroundWindow() != handle and time.monotonic() < deadline:
            time.sleep(.05)
        rect = w.RECT()
        if not user.GetWindowRect(handle, ctypes.byref(rect)) or (rect.right-rect.left, rect.bottom-rect.top) != (1569,975):
            raise RuntimeError("Window geometry changed: re-inspect instead of guessing")
        if user.GetForegroundWindow() != handle:
            raise RuntimeError("WXWork focus could not be verified")
        user.SetCursorPos(rect.left + 240, rect.top + 56)
        user.mouse_event(2, 0, 0, 0, 0)
        user.mouse_event(4, 0, 0, 0, 0)
        user.keybd_event(0x11, 0, 0, 0)
        user.keybd_event(0x41, 0, 0, 0)
        user.keybd_event(0x41, 0, 2, 0)
        user.keybd_event(0x11, 0, 2, 0)
        events = []
        for character in "苇中鹤":
            for flag in (4,6):
                event = INPUT(type=1)
                event.ki = KEYBDINPUT(0, ord(character), flag, 0, 0)
                events.append(event)
        buffer = (INPUT * len(events))(*events)
        if user.SendInput(len(events), buffer, ctypes.sizeof(INPUT)) != len(events):
            raise RuntimeError("Search text input incomplete")
    print(json.dumps({"app":"WXWork", "action":"search_only", "query":"苇中鹤", "sent":False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
