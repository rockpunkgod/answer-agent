"""Prepare, then send ONE user-authorized WeCom test probe through durable Outbox.

Not a chat bot. Only the currently inspected WeCom 苇中鹤 session is eligible.
The session fingerprint is NOT a permanent/platform contact ID; no auto lookup.
"""
import argparse
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helpdesk.domain import new_id
from helpdesk.locking import resource_lock
from helpdesk.service import Helpdesk
from helpdesk.storage import Store, encode, now
from helpdesk.windows_test import WeComVisualProbe
from helpdesk.workflow import Workflow

PIN=ROOT/"data/private/wecom-test-pin.json"
DB=ROOT/"data/wecom-test.db"


def ps_file(script,*args):
    result=subprocess.run(["powershell.exe","-NoProfile","-File",str(ROOT/"tools"/script),*map(str,args)],
        capture_output=True,encoding="utf-8-sig",timeout=20,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Window check failed; no send")
    return json.loads(result.stdout)


def prepare():
    if PIN.exists():
        raise RuntimeError("A test pin already exists. Inspect the existing Outbox; do not manufacture a retry.")
    native=ctypes.windll.user32
    native.SetProcessDPIAware()
    native.GetForegroundWindow.restype=w.HWND
    raw=subprocess.run(["powershell.exe","-NoProfile","-Command",
        "Get-Process -Name WXWork | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1 | ForEach-Object { [pscustomobject]@{pid=$_.Id;handle=$_.MainWindowHandle.ToInt64();start=$_.StartTime.ToUniversalTime().Ticks} } | ConvertTo-Json -Compress"],
        capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    process=json.loads(raw.stdout)
    with resource_lock(ROOT/"data/windows-interactive-desktop.lock"):
        native.ShowWindow(process["handle"],9)
        native.SetForegroundWindow(process["handle"])
        deadline=time.monotonic()+2
        while native.GetForegroundWindow()!=process["handle"] and time.monotonic()<deadline:
            time.sleep(.05)
        image=ROOT/"data/private/wecom-pin-frame.png"
        ps_file("probe_wecom.ps1","-Output","data/private/wecom-pin-frame.png","-CaptureVisible")
        ocr=ps_file("ocr_window.ps1","-ImagePath",image)
        if not ocr["header_match"] or not ocr["sidebar_match"] or ocr["image_size"]!={"width":2019,"height":975}:
            raise RuntimeError("Current WeCom chat is not the verified target layout; no send")
        from PIL import Image
        with Image.open(image) as frame:
            avatar=sha256(frame.crop((114,110,174,174)).convert("RGB").tobytes()).hexdigest()
        pin={"handle":process["handle"],"process_id":process["pid"],"process_start":process["start"],
             "avatar_hash":avatar,"verified_at":now(),"expires_at":time.time()+900,
             "session_key":"wecom-observed-session:"+sha256(encode([process,avatar]).encode()).hexdigest(),
             "scope":"USER_SELECTED_SINGLE_SESSION_NOT_PLATFORM_ACCOUNT_ID","evidence_sha256":sha256(image.read_bytes()).hexdigest()}
        desktop=WeComVisualProbe(ROOT,pin)
        db=Store(DB)
        try:
            service=Helpdesk(db)
            binding=service.bind("OPERATOR_TEST_ONLY",pin["session_key"],"苇中鹤",verified=True)
            with db.transaction():
                mid,oid=new_id(),new_id()
                body="TEST 20260929 001. Helpdesk demo. Test contact only. No student delivery."
                db.execute("""INSERT INTO messages(id,binding_id,source,platform_id,observed_at,raw_text,attachments,
                    fingerprint,intent,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (mid,binding,"operator_test","wecom-test-probe-001",now(),"User-authorized WeCom test probe", "[]",
                     sha256(body.encode()).hexdigest(),"IRRELEVANT","PROCESSED",now()))
                db.execute("""INSERT INTO outbox(id,message_id,binding_id,purpose,body,idempotency_key,state,created_at,
                    review_status,simulated) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (oid,mid,binding,"PROGRESS",body,"wecom-test-probe-001","PENDING",now(),"OPERATOR_AUTHORIZED",0))
                service._audit("REAL_TEST_PREPARED",details={"outbox_id":oid,"target":"苇中鹤","platform":"wecom",
                    "session_key":pin["session_key"],"evidence_sha256":pin["evidence_sha256"],"scope":pin["scope"]})
                pin["outbox_id"]=oid
                desktop.preflight(Workflow(db,desktop=desktop)._bound(db.one("SELECT * FROM outbox WHERE id=?",(oid,))))
            PIN.write_text(encode(pin),encoding="utf-8")
            print(encode({"state":"PREPARED_NOT_SENT","outbox_id":oid,"target":"企业微信/苇中鹤","body":body}))
        finally:
            db.close()


def dispatch(recovery=False, inspect=False):
    pin=json.loads(PIN.read_text(encoding="utf-8"))
    db=Store(DB)
    try:
        flow=Workflow(db,desktop=WeComVisualProbe(ROOT,pin))
        if inspect:
            result=flow.inspect_unknown(pin["outbox_id"])
        else:
            result=flow.recover() if recovery else flow.dispatch(pin["outbox_id"])
        print(encode({"result":result,"target":"企业微信/苇中鹤","simulated":False}))
    finally:
        db.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["prepare","dispatch","recover","inspect"])
    command=parser.parse_args().action
    if command=="prepare":
        prepare()
    else:
        dispatch(command=="recover",command=="inspect")
