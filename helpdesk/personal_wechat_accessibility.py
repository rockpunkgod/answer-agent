"""Retired diagnostic; not connected to any current collection entrypoint.

The ordinary database/media worker still forbids interactive SDK imports. This
separate diagnostic loads only the audited accessibility helper, temporarily
opens Qt's accessibility gate and restores every byte it changed on exit.
"""
from contextlib import contextmanager
from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import re
import time


ACCESSIBILITY_HASH='5a62382dd82c2916fdf50928a4a5dc4ec7f688ec5f212b077c48e2a7c4f35d60'


def verify_test_chat_alignment(recipient,*,live_account_id,room_matches,ui_rows,db_rows,source_message_ids):
    """Bind an actual test chat to account/room facts and unique original messages.

    Inputs must come from fresh local reads by the caller. This function does
    not read a process, enable accessibility, import messages or authorize a
    send. UI rows are the result of current_chat_rows' two-anchor name check.
    """
    from .test_routing import PersonalWeChatTestRecipient
    if not isinstance(recipient,PersonalWeChatTestRecipient) or live_account_id!=recipient.account_id:
        raise ValueError('PERSONAL_TEST_LIVE_ACCOUNT_CHANGED')
    if (not isinstance(room_matches,list) or len(room_matches)!=1 or not isinstance(room_matches[0],dict)
            or room_matches[0].get('username')!=recipient.room_id
            or room_matches[0].get('nick_name')!=recipient.display_name):
        raise ValueError('PERSONAL_TEST_ROOM_ID_NOT_UNIQUE')
    if (not isinstance(source_message_ids,(list,tuple)) or not source_message_ids
            or any(not isinstance(v,str) or not v for v in source_message_ids)
            or len(set(source_message_ids))!=len(source_message_ids)
            or not isinstance(ui_rows,list) or not ui_rows or len(ui_rows)>100
            or not isinstance(db_rows,list) or not db_rows or len(db_rows)>10000):
        raise ValueError('PERSONAL_TEST_MESSAGE_ANCHORS_REQUIRED')
    if (any(not isinstance(row,dict) or row.get('kind') not in ('text','image') or not isinstance(row.get('text'),str) for row in ui_rows)
            or any(not isinstance(row,dict) or row.get('kind') not in ('text','image') or not isinstance(row.get('text'),str)
                   or not isinstance(row.get('message_id'),str) or not row['message_id'] for row in db_rows)
            or len({row['message_id'] for row in db_rows})!=len(db_rows)):
        raise ValueError('PERSONAL_TEST_MESSAGE_SEQUENCE_INVALID')
    # Image-only sequences cannot prove which material is on screen.
    if not any(row['kind']=='text' and row['text'].strip() for row in ui_rows):
        raise ValueError('PERSONAL_TEST_TEXT_ANCHOR_REQUIRED')
    width=len(ui_rows)
    offsets=[index for index in range(len(db_rows)-width+1)
             if all(ui['kind']==db['kind'] and (ui['kind']=='image' or ui['text']==db['text'])
                    for ui,db in zip(ui_rows,db_rows[index:index+width]))]
    if len(offsets)!=1:
        raise ValueError('PERSONAL_TEST_MESSAGE_ALIGNMENT_NOT_UNIQUE')
    matched=[row['message_id'] for row in db_rows[offsets[0]:offsets[0]+width]]
    if not set(source_message_ids).issubset(matched):
        raise ValueError('PERSONAL_TEST_ORIGINAL_MESSAGE_NOT_VISIBLE')
    return {'status':'LIVE_PERSONAL_TEST_CHAT_BOUND','target_key':recipient.stable_key,
            'source_message_ids':list(source_message_ids),'matched_message_ids':matched,
            'sending_authorized':False,'formal_performance_eligible':False}


def current_chat_rows(root,*,room_id,rooms):
    """Read only the current authorized chat, never traverse the session list."""
    if room_id not in rooms:raise ValueError('ACCESSIBILITY_ROOM_NOT_AUTHORIZED')
    wanted=rooms[room_id]
    if list(rooms.values()).count(wanted)!=1:raise ValueError('ACCESSIBILITY_ROOM_NAME_AMBIGUOUS')
    pending=[(root,0)];anchors={};seen=0;started=time.monotonic()
    while pending:
        if seen>=500 or time.monotonic()-started>8:raise ValueError('ACCESSIBILITY_TREE_INCOMPLETE')
        control,depth=pending.pop(0);seen+=1
        aid=control.AutomationId or '';leaf=aid.split('.')[-1]
        if leaf in ('chat_input_field','current_chat_name_label','chat_message_list'):
            anchors.setdefault(leaf,[]).append(control)
        if leaf in ('session_list','chat_message_list'):continue
        children=control.GetChildren()
        if depth>=30 and children:raise ValueError('ACCESSIBILITY_TREE_INCOMPLETE')
        pending.extend((child,depth+1) for child in children)
    if any(len(anchors.get(key,[]))!=1 for key in ('chat_input_field','current_chat_name_label','chat_message_list')):
        raise ValueError('ACCESSIBILITY_CHAT_ANCHORS_NOT_UNIQUE')
    field=anchors['chat_input_field'][0];title=anchors['current_chat_name_label'][0]
    def check():
        name=re.sub(r'(?:(?:长按|按住)鼠标\s*)?语音输入(?:文字)?$','',field.Name or '').strip()
        if name!=wanted or (title.Name or '').strip()!=wanted:
            raise ValueError('ACCESSIBILITY_CURRENT_CHAT_CHANGED')
    check();rows=[]
    for control in anchors['chat_message_list'][0].GetChildren():
        kind=control.ClassName;name=control.Name or ''
        # This client exposes the English caption even with Chinese chat text.
        # The same class also represents quoted cards, so class alone is unsafe.
        if kind=='mmui::ChatBubbleReferItemView' and name in ('图片','Image'):
            rows.append({'kind':'image','text':'','control':control})
        elif kind=='mmui::ChatTextItemView' and name.strip():
            rows.append({'kind':'text','text':name.strip(),'control':control})
    check()
    return rows


def locate_image_in_sequence(ui_rows,db_rows,message_id):
    """Exact unique contiguous alignment; no fuzzy text or image-count fallback."""
    if not ui_rows or not db_rows:raise ValueError('ACCESSIBILITY_IMAGE_NOT_IN_CURRENT_VIEW')
    matches=[];width=len(ui_rows)
    for offset in range(len(db_rows)-width+1):
        window=db_rows[offset:offset+width]
        if all(u['kind']==d['kind'] and (u['kind']=='image' or u['text']==d['text'])
               for u,d in zip(ui_rows,window)):
            matches.append(offset)
    if len(matches)!=1:raise ValueError('ACCESSIBILITY_MESSAGE_ALIGNMENT_NOT_UNIQUE')
    offset=matches[0]
    targets=[i for i,u in enumerate(ui_rows) if u['kind']=='image'
             and db_rows[offset+i]['message_id']==message_id]
    if len(targets)!=1:raise ValueError('ACCESSIBILITY_IMAGE_NOT_IN_CURRENT_VIEW')
    return targets[0]


def preview_windows(auto,pid):
    """Only this pinned process; recognize preview class, not translated titles."""
    result=[]
    for top in auto.GetRootControl().GetChildren():
        if top.ProcessId!=pid:continue
        if top.ClassName=='mmui::PreviewWindow':
            result.append((top.NativeWindowHandle,top));continue
        if (top.ClassName or '').startswith('Qt'):
            for child in top.GetChildren():
                if child.ClassName=='mmui::PreviewWindow':
                    result.append((top.NativeWindowHandle,child))
    return result


def load_accessibility_helper(upstream,private_directory):
    path=Path(upstream).resolve(strict=True)/'wechatauto/accessibility.py'
    if sha256(path.read_bytes()).hexdigest()!=ACCESSIBILITY_HASH:
        raise ValueError('UPSTREAM_ACCESSIBILITY_CONTENT_CHANGED')
    if 'wechatauto.logger' not in sys.modules:
        raise ValueError('ISOLATED_READ_MODULES_REQUIRED_FIRST')
    spec=importlib.util.spec_from_file_location('answer_agent_replica_accessibility',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.GATE_CACHE_FILE=str(Path(private_directory).resolve(strict=True)/'uia-gate-cache.local.json')
    module.auto.InitializeUIAutomationInCurrentThread()
    return module


class GateMemory:
    def __init__(self,pid,gate):
        import ctypes
        from ctypes import wintypes
        self.gate=gate;self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.OpenProcess(0x0400|0x0010|0x0020|0x0008,False,pid)
        if not self.handle:raise RuntimeError('ACCESSIBILITY_PROCESS_ACCESS_UNAVAILABLE')
    def read(self,address):return self.gate._read_process_byte(self.handle,address)
    def write(self,address,value):return self.gate._write_process_byte(self.handle,address,value)
    def close(self):self.kernel.CloseHandle(self.handle)


@contextmanager
def temporary_accessibility(module,*,hwnd,pid,guard,memory_factory=GateMemory):
    """One candidate, pinned process/DLL bounds, rollback independently verified.

    No login, foreground activation, screen-reader system setting, Qt GUI
    driver or send module is invoked. Abnormal process termination remains an
    explicit diagnostic risk; this helper is not enabled as a production reader.
    """
    guard();gate=module.AccessibilityGate
    if gate._pid_from_hwnd(hwnd)!=pid:raise ValueError('ACCESSIBILITY_WINDOW_PROCESS_CHANGED')
    dll=gate._weixin_dll_module(pid)
    if not dll:raise ValueError('ACCESSIBILITY_DLL_NOT_VERIFIED')
    base,size,_=dll
    if type(base) is not int or type(size) is not int or base<=0 or size<=0:
        raise ValueError('ACCESSIBILITY_DLL_NOT_VERIFIED')
    memory=memory_factory(pid,gate);changed={}
    result={'materialized':False,'process_flag_changed':False,'restored':False,
            'activation_performed':False,'input_performed':False,'system_settings_changed':False}
    class ScopedGate(gate):
        @staticmethod
        def _write_process_byte(handle,address,value):
            guard()
            if not base<=address<base+size or value not in (0,1):
                raise ValueError('ACCESSIBILITY_WRITE_OUTSIDE_GATE_SCOPE')
            original=memory.read(address)
            if original not in (0,1):raise ValueError('ACCESSIBILITY_BYTE_NOT_BOOLEAN')
            changed.setdefault(address,original)
            return memory.write(address,value)
    try:
        result['materialized']=ScopedGate._hot_activate_accessibility(hwnd,verify=True,max_candidates=1) is True
        result['process_flag_changed']=bool(changed)
        guard()
        yield result
    finally:
        restored=True
        try:
            for address,original in changed.items():
                current=memory.read(address)
                if current!=original:
                    if current not in (0,1) or not memory.write(address,original):
                        restored=False;continue
                if memory.read(address)!=original:restored=False
            result['restored']=restored
        finally:memory.close()
        if not restored:raise RuntimeError('ACCESSIBILITY_GATE_RESTORE_UNVERIFIED')
