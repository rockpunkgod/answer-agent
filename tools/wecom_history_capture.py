"""Read-only history navigation: no keyboard input, clipboard paste or send action.

Coordinates require the operator's immediately inspected window dimensions.
Captures contain private conversation data and stay in data/private/history.
"""
import argparse
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.locking import resource_lock


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--handle', type=int, required=True)
    p.add_argument('--size', type=int, nargs=2, required=True)
    p.add_argument('--click', type=int, nargs=2)
    p.add_argument('--scroll', type=int, help='Wheel ticks: positive toward older history')
    p.add_argument('--point', type=int, nargs=2)
    p.add_argument('--label', required=True)
    p.add_argument('--open-history', action='store_true')
    p.add_argument('--history-x',type=int,default=1098)
    args = p.parse_args()
    if not args.label.replace('-', '').replace('_', '').isalnum():
        p.error('Simple file label required')
    user = ctypes.windll.user32
    user.SetProcessDPIAware()
    user.GetForegroundWindow.restype = w.HWND
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    pid = w.DWORD()
    user.GetWindowThreadProcessId(args.handle, ctypes.byref(pid))
    process = subprocess.run(['powershell', '-NoProfile', '-Command',
                              f'(Get-Process -Id {pid.value}).ProcessName'], capture_output=True, text=True, check=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    if process.stdout.strip() != 'WXWork':
        raise RuntimeError('Only WeCom is eligible')
    def same_app_foreground():
        current_pid=w.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(),ctypes.byref(current_pid))
        return current_pid.value == pid.value
    with resource_lock(Path('data/windows-interactive-desktop.lock').resolve()):
        rect = w.RECT()
        if not user.GetWindowRect(args.handle, ctypes.byref(rect)) or [rect.right-rect.left,rect.bottom-rect.top] != args.size:
            raise RuntimeError('Geometry changed: inspect again')
        user.ShowWindowAsync.argtypes = [w.HWND, ctypes.c_int]
        if not same_app_foreground():
            user.ShowWindowAsync(args.handle, 9)
        time.sleep(.15)
        if True:
            if not same_app_foreground():
                user.SetForegroundWindow(args.handle)
            time.sleep(.2)
            if not same_app_foreground():
                raise RuntimeError('Wrong foreground window')
            if not user.GetWindowRect(args.handle, ctypes.byref(rect)) or [rect.right-rect.left,rect.bottom-rect.top] != args.size:
                raise RuntimeError('Geometry changed after focus')
            x, y = (args.history_x, 778) if args.open_history else args.click or args.point or (0, 0)
            if args.open_history:
                if args.size[1] not in (975,976) or not 1000 <= x <= 1120:
                    raise ValueError('Uninspected history toolbar geometry')
            elif args.click:
                safe = (95 <= x <= 455 and 100 <= y < args.size[1]-20) or (args.size[0]-450 <= x < args.size[0]-10 and 130 <= y <= 720)
                if not safe:
                    raise ValueError('Only observed conversation-list/history-filter controls allowed')
            elif args.scroll and not ((480 <= x < args.size[0]-15 or 100 <= x <= 440) and 140 <= y <= 720):
                raise ValueError('Scroll only in observed history region')
            if args.click or args.scroll or args.open_history:
                user.SetCursorPos(rect.left+x, rect.top+y)
            if args.click or args.open_history:
                user.mouse_event(2,0,0,0,0); user.mouse_event(4,0,0,0,0)
            elif args.scroll:
                if abs(args.scroll) > 10:
                    raise ValueError('Bounded scrolling only')
                for _ in range(abs(args.scroll)):
                    if not same_app_foreground():
                        raise RuntimeError('Foreground changed while scrolling')
                    user.mouse_event(0x0800,0,0,ctypes.c_ulong(120 if args.scroll > 0 else -120).value,0)
                    time.sleep(.035)
            time.sleep(.35)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output = Path('data/private/history') / f'{stamp}-{args.label}.png'
        if not same_app_foreground():
            raise RuntimeError('Foreground changed before capture')
        if not user.GetWindowRect(args.handle,ctypes.byref(rect)):
            raise RuntimeError('Window disappeared before capture')
        output.parent.mkdir(parents=True,exist_ok=True)
        ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom),all_screens=True).save(output)
        metadata = {'App':'WXWork','Handle':args.handle,'ImagePath':str(output.resolve()),'size':[rect.right-rect.left,rect.bottom-rect.top]}
        metadata.update(captured_at=stamp, screenshot_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                        sent=False, extraction_status='UNREVIEWED', scope_start='2026-09-17')
        output.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'image':str(output.resolve()),'sent':False},ensure_ascii=False))


if __name__ == '__main__':
    main()
