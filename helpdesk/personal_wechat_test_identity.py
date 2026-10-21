"""Isolated live identity guard for one reviewed personal-WeChat test room.

The ordinary collector is unchanged. Scoped Qt accessibility is explicit and
off by default; it temporarily changes a client flag and verifies restoration.
This guard never activates a window, invokes a control, types, sends or imports.
"""
from contextlib import contextmanager, nullcontext, redirect_stdout, redirect_stderr
import io
from pathlib import Path

from .personal_wechat_accessibility import (current_chat_rows, verify_test_chat_alignment,
                                           load_accessibility_helper, temporary_accessibility)
from .personal_wechat_replica import load_read_modules, strict_database, ReplicaSnapshotReader
from .personal_wechat_source import PersonalWeChatSource
from .test_routing import PersonalWeChatTestRecipient


@contextmanager
def personal_test_identity(config,*,source_message_ids,allow_temporary_accessibility=False):
    """Use only in a fresh isolated interpreter and under the existing UI lock.

    Yields the actual callback required by PersonalWeChatTestDesktop. All live
    facts are re-read on each invocation; previous evidence is never a permit.
    """
    import ctypes
    from ctypes import wintypes
    import psutil
    if type(allow_temporary_accessibility) is not bool:
        raise ValueError('EXPLICIT_ACCESSIBILITY_MODE_REQUIRED')
    rooms=config.get('rooms',{})
    test_rooms=config.get('test_room_ids',[])
    private=Path(config['private_root']).resolve(strict=True)
    approved=Path(__file__).resolve().parents[1]/'data/private'
    if (config.get('prototype_only') is not True or config.get('groups_reviewed') is not True
            or not private.is_relative_to(approved) or len(test_rooms)!=1
            or rooms.get(test_rooms[0])!='测试' or list(rooms.values()).count('测试')!=1
            or not isinstance(source_message_ids,(list,tuple)) or not source_message_ids):
        raise ValueError('REVIEWED_PERSONAL_TEST_SCOPE_REQUIRED')
    module,_=load_read_modules(config['upstream'],include_media=False)
    process=psutil.Process(config['process_id'])
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.IsWindow.argtypes=[wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    def process_guard():
        if (not process.is_running() or process.name().casefold()!='weixin.exe'
                or process.create_time()!=config['process_create_time']):
            raise ValueError('PERSONAL_TEST_PROCESS_CHANGED')
        owner=wintypes.DWORD()
        if not user.IsWindow(config['window_handle']):
            raise ValueError('PERSONAL_TEST_WINDOW_UNAVAILABLE')
        user.GetWindowThreadProcessId(config['window_handle'],ctypes.byref(owner))
        if owner.value!=process.pid:
            raise ValueError('PERSONAL_TEST_WINDOW_PROCESS_CHANGED')
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            account=module.extract_master_key_from_cfg(process.pid)
        if not account or account[2]!=config['account_id']:
            raise ValueError('PERSONAL_TEST_LIVE_ACCOUNT_CHANGED')
    process_guard()
    with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
        db=strict_database(module,db_root=config['db_root'],account=config['account_directory'],
                           cache=private/'db-cache',pid=process.pid)
    if db.wxid!=config['account_id']:
        raise ValueError('PERSONAL_TEST_ACCOUNT_DIRECTORY_MISMATCH')
    reader=ReplicaSnapshotReader(db,account_id=config['account_id'],rooms=rooms,guard=process_guard)
    room=test_rooms[0]
    source=PersonalWeChatSource(reader,account_id=config['account_id'],rooms=rooms,room_id=room,test_room_ids=test_rooms)
    if allow_temporary_accessibility:
        helper=load_accessibility_helper(config['upstream'],private)
        auto=helper.auto
    else:
        import uiautomation as auto
        auto.InitializeUIAutomationInCurrentThread()
    def guard(recipient):
        guard.last_evidence=None
        guard.last_attempt=None
        if (not isinstance(recipient,PersonalWeChatTestRecipient)
                or recipient.account_id!=config['account_id'] or recipient.room_id!=room):
            raise ValueError('PERSONAL_TEST_RECIPIENT_SCOPE_CHANGED')
        process_guard()
        contacts=[rel for rel,path,_ in db._db_files if Path(path).name=='contact.db']
        if len(contacts)!=1:
            raise ValueError('PERSONAL_TEST_CONTACT_DATABASE_NOT_UNIQUE')
        connection=db._open(contacts[0])
        try:
            connection.execute('PRAGMA query_only=ON')
            matches=[dict(row) for row in connection.execute(
                'SELECT username,nick_name FROM contact WHERE nick_name=? AND username LIKE ?',('测试','%@chatroom'))]
        finally:
            connection.close()
        snapshot=reader.snapshot(room,10000)
        sequence=[]
        for raw in sorted(snapshot['rows'],key=lambda r:(r['sort_seq'],r['shard'],r['local_id'],r['local_type'])):
            message=source._record(raw)[2]
            if message.message_type not in ('image','text'):
                continue
            text=message.normalized_text
            sender=message.raw_payload['sender_username']
            if sender and text.startswith(sender+':\n'):
                text=text[len(sender)+2:]
            sequence.append({'kind':message.message_type,'text':text.strip(),'message_id':message.message_id})
        scope=temporary_accessibility(helper,hwnd=config['window_handle'],pid=process.pid,
                                      guard=process_guard) if allow_temporary_accessibility else nullcontext(None)
        state=None
        try:
            with scope as state:
                rows=current_chat_rows(auto.ControlFromHandle(config['window_handle']),room_id=room,rooms=rooms)
                evidence=verify_test_chat_alignment(recipient,live_account_id=config['account_id'],
                    room_matches=matches,ui_rows=rows,db_rows=sequence,source_message_ids=source_message_ids)
                process_guard()
        finally:
            guard.last_attempt={'temporary_accessibility':allow_temporary_accessibility,
                'process_flag_changed':bool(state and state.get('process_flag_changed')),
                'process_flag_restored':state.get('restored') if state else None,
                'sending_authorized':False}
        if state is not None and state.get('restored') is not True:
            raise ValueError('PERSONAL_TEST_ACCESSIBILITY_RESTORE_UNVERIFIED')
        evidence.update(temporary_accessibility=allow_temporary_accessibility,
                        process_flag_changed=bool(state and state.get('process_flag_changed')),
                        process_flag_restored=state.get('restored') if state else None)
        guard.last_evidence=evidence
        return True
    guard.last_evidence=None
    guard.last_attempt=None
    try:
        yield guard
    finally:
        auto.UninitializeUIAutomationInCurrentThread()
