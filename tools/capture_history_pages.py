"""Bounded, wheel-only capture of an inspected WeCom history panel.

Each image is original screen evidence. No text entry, copying, or sending.
Stops on focus, geometry or selected-chat header changes. Repeated frames mark
only the visible scroll boundary, not a claim of complete platform history.
"""
import argparse
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
from PIL import ImageGrab

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from helpdesk.locking import resource_lock


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--handle',type=int,required=True)
    p.add_argument('--size',type=int,nargs=2,required=True)
    p.add_argument('--group',required=True)
    p.add_argument('--pages',type=int,default=80)
    p.add_argument('--panel-only',action='store_true')
    args=p.parse_args()
    if not args.group.replace('-','').replace('_','').isalnum() or not 1<=args.pages<=200:
        raise ValueError('Simple observed group label and bounded pages required')
    u=ctypes.windll.user32;u.SetProcessDPIAware();u.GetForegroundWindow.restype=w.HWND
    u.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    u.SetForegroundWindow.argtypes=[w.HWND]
    h=args.handle
    root=Path('data/private/history')/args.group
    root.mkdir(parents=True,exist_ok=True)
    with resource_lock(Path('data/windows-interactive-desktop.lock').resolve()):
        u.SetForegroundWindow(h);time.sleep(.2)
        def bounds():
            r=w.RECT()
            if u.GetForegroundWindow()!=h or not u.GetWindowRect(h,ctypes.byref(r)) or [r.right-r.left,r.bottom-r.top]!=args.size:
                raise RuntimeError('Foreground or geometry changed; stopped')
            return r
        r=bounds()
        def header():
            return sha256(ImageGrab.grab(bbox=(r.left+485,r.top+35,r.left+1100,r.top+75),all_screens=True).tobytes()).hexdigest()
        original_header=header()
        def wheel(sign):
            bounds()
            if header()!=original_header:raise RuntimeError('Selected chat changed; stopped')
            u.SetCursorPos(r.right-430,r.top+640)
            for _ in range(8):
                if u.GetForegroundWindow()!=h:raise RuntimeError('Foreground changed; stopped')
                u.mouse_event(0x0800,0,0,ctypes.c_ulong(sign*120).value,0);time.sleep(.035)
            u.SetCursorPos(r.left+470,r.top+30)
            time.sleep(.3)
        existing=root/'manifest.json'
        records=json.loads(existing.read_text(encoding='utf-8'))['frames'] if existing.exists() else []
        offset=len(records);seen={};repeated=0
        # Caller opens the history pane at its most recent position.
        for i in range(args.pages):
            bounds()
            u.SetCursorPos(r.left+470,r.top+30)
            time.sleep(.15)
            if header()!=original_header:raise RuntimeError('Selected chat changed; stopped')
            panel=ImageGrab.grab(bbox=(r.right-450,r.top+360,r.right,r.bottom),all_screens=True)
            digest=sha256(panel.tobytes()).hexdigest()
            if digest in seen:
                repeated+=1
                if repeated>=2:break
            else:repeated=0
            stamp=datetime.now(timezone.utc).isoformat()
            image=root/f'{offset+i:04d}.png'
            if args.panel_only:panel.save(image)
            else:ImageGrab.grab(bbox=(r.left,r.top,r.right,r.bottom),all_screens=True).save(image)
            records.append({'file':str(image.resolve()),'captured_at':stamp,'sha256':sha256(image.read_bytes()).hexdigest(),'panel_hash':digest,'panel_only':args.panel_only})
            seen[digest]=i
            (root/'manifest.json').write_text(json.dumps({'observed_group':args.group,'from':'2026-09-17','sent':False,'complete':False,'frames':records},ensure_ascii=False,indent=2),encoding='utf-8')
            wheel(1)
        print(json.dumps({'group':args.group,'frames':len(records),'repeated_boundary':repeated>=2,'complete':False,'sent':False}))


if __name__=='__main__':main()
