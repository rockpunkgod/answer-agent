"""Local Win32 transport for reviewed WeCom controls; no Windows-MCP process.

Only fixed foreground metadata, bounded screenshots, clipboard and a small
input vocabulary are implemented. Business MCP never exposes these primitives.
"""
import ctypes
from ctypes import wintypes as w
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .mcp_window_probe import FOREGROUND_COMMAND


class NativeWeComTransport:
    native_backend=True
    def __init__(self,pin,root,*,guard):
        if os.name!='nt':raise ValueError('WINDOWS_REQUIRED')
        self.pin=pin;self.guard=guard
        self.directory=Path(root)/'data/private/wecom-native';self.directory.mkdir(parents=True,exist_ok=True)
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.user.GetForegroundWindow.restype=w.HWND
        self.user.GetAsyncKeyState.argtypes=[ctypes.c_int]
        self.user.GetAsyncKeyState.restype=w.SHORT
        self.user.SetForegroundWindow.argtypes=[w.HWND]
        self.user.BringWindowToTop.argtypes=[w.HWND]
        self.user.AttachThreadInput.argtypes=[w.DWORD,w.DWORD,w.BOOL]
        self.user.IsWindow.argtypes=[w.HWND]
        self.user.IsIconic.argtypes=[w.HWND]
        self.user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
        self.user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
        self.user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.user.OpenInputDesktop.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.user.OpenInputDesktop.restype=w.HANDLE
        self.user.GetUserObjectInformationW.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
        self.user.CloseDesktop.argtypes=[w.HANDLE]
        self.user.SetProcessDPIAware()
        self.last_native=None

    def __enter__(self):return self
    def __exit__(self,*args):pass

    def foreground(self,handle=None):
        handle=handle or self.user.GetForegroundWindow();pid=w.DWORD()
        self.user.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        process=self.kernel.OpenProcess(0x1000,False,pid.value)
        if not process:raise ValueError('NATIVE_PROCESS_UNAVAILABLE')
        try:
            name=ctypes.create_unicode_buffer(32768);length=w.DWORD(len(name))
            if not self.kernel.QueryFullProcessImageNameW(process,0,name,ctypes.byref(length)):
                raise ValueError('NATIVE_PROCESS_UNAVAILABLE')
        finally:self.kernel.CloseHandle(process)
        rect=w.RECT()
        if not self.user.GetWindowRect(handle,ctypes.byref(rect)):raise ValueError('NATIVE_WINDOW_UNAVAILABLE')
        return {'process':Path(name.value).stem,'handle':int(handle),'left':rect.left,'top':rect.top,
                'width':rect.right-rect.left,'height':rect.bottom-rect.top}

    def activate(self):
        self.guard()
        self._check_modifiers()
        self._interactive_desktop()
        handle=self.pin['window_geometry_handle'][2]
        if not self.user.IsWindow(handle) or self.user.IsIconic(handle):
            raise ValueError('REVIEWED_MAIN_WINDOW_UNAVAILABLE')
        native=self.foreground(handle)
        title=ctypes.create_unicode_buffer(256);self.user.GetWindowTextW(handle,title,len(title))
        if native['process']!='WXWork' or title.value!='企业微信':
            raise ValueError('REVIEWED_MAIN_WINDOW_IDENTITY_CHANGED')
        def is_foreground():
            try:
                return self.foreground()==native
            except ValueError:
                return False
        self.user.SetForegroundWindow(handle)
        if not is_foreground():
            # One bounded documented Win32 focus attempt. It does not type,
            # activate an arbitrary process, or keep input queues attached.
            foreground=self.user.GetForegroundWindow()
            thread=self.user.GetWindowThreadProcessId(foreground,None)
            own=self.kernel.GetCurrentThreadId()
            attached=thread!=own and self.user.AttachThreadInput(own,thread,True)
            try:
                if attached:
                    self.user.BringWindowToTop(handle);self.user.SetForegroundWindow(handle)
            finally:
                if attached:self.user.AttachThreadInput(own,thread,False)
        deadline=time.monotonic()+1
        while not is_foreground() and time.monotonic()<deadline:time.sleep(.05)
        if not is_foreground():raise ValueError('WECOM_ACTIVATION_UNCONFIRMED')
        self.last_native=native
        return {'status':'WINDOW_ACTIVATED_CHAT_NOT_YET_VERIFIED'}

    def _interactive_desktop(self):
        desktop=self.user.OpenInputDesktop(0,False,1)
        if not desktop:raise ValueError('INTERACTIVE_DESKTOP_UNAVAILABLE')
        try:
            name=ctypes.create_unicode_buffer(256);needed=w.DWORD()
            if not self.user.GetUserObjectInformationW(desktop,2,name,ctypes.sizeof(name),ctypes.byref(needed)) or name.value.casefold()!='default':
                raise ValueError('INTERACTIVE_DESKTOP_UNAVAILABLE')
        finally:self.user.CloseDesktop(desktop)

    def _check_input(self,point=None):
        self.guard()
        self._check_modifiers()
        self._interactive_desktop()
        current=self.foreground()
        if current['process']!='WXWork' or current!=self.last_native:
            raise ValueError('NATIVE_FOREGROUND_OR_LAYOUT_CHANGED')
        if point is not None:
            x,y=point
            if not(current['left']<=x<current['left']+current['width'] and current['top']<=y<current['top']+current['height']):
                raise ValueError('POINT_OUTSIDE_VERIFIED_WINDOW')

    def _check_modifiers(self):
        # Do not combine our literal paste/click with a user's held shortcut.
        # Never release keys that this transport did not press itself.
        if any(self.user.GetAsyncKeyState(key)&0x8000 for key in (0x10,0x11,0x12,0x5b,0x5c)):
            raise ValueError('USER_MODIFIER_HELD_INPUT_PAUSED')

    def _clipboard(self,mode,text=None):
        self.user.OpenClipboard.argtypes=[w.HWND]
        self.user.GetClipboardData.argtypes=[w.UINT];self.user.GetClipboardData.restype=w.HANDLE
        self.user.SetClipboardData.argtypes=[w.UINT,w.HANDLE];self.user.SetClipboardData.restype=w.HANDLE
        self.kernel.GlobalAlloc.argtypes=[w.UINT,ctypes.c_size_t];self.kernel.GlobalAlloc.restype=w.HANDLE
        self.kernel.GlobalLock.argtypes=[w.HANDLE];self.kernel.GlobalLock.restype=ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes=[w.HANDLE];self.kernel.GlobalFree.argtypes=[w.HANDLE]
        opened=False
        for _ in range(10):
            if self.user.OpenClipboard(None):opened=True;break
            time.sleep(.02)
        if not opened:raise ValueError('CLIPBOARD_BUSY')
        try:
            if mode=='get':
                handle=self.user.GetClipboardData(13)
                if not handle:return ''
                pointer=self.kernel.GlobalLock(handle)
                if not pointer:raise ValueError('CLIPBOARD_UNAVAILABLE')
                try:return ctypes.wstring_at(pointer)
                finally:self.kernel.GlobalUnlock(handle)
            if mode!='set' or not isinstance(text,str) or len(text)>100000:raise ValueError('INVALID_CLIPBOARD_OPERATION')
            raw=(text+'\0').encode('utf-16-le');handle=self.kernel.GlobalAlloc(2,len(raw))
            if not handle:raise ValueError('CLIPBOARD_UNAVAILABLE')
            pointer=self.kernel.GlobalLock(handle)
            if not pointer:self.kernel.GlobalFree(handle);raise ValueError('CLIPBOARD_UNAVAILABLE')
            try:ctypes.memmove(pointer,raw,len(raw))
            finally:self.kernel.GlobalUnlock(handle)
            if not self.user.EmptyClipboard() or not self.user.SetClipboardData(13,handle):
                self.kernel.GlobalFree(handle);raise ValueError('CLIPBOARD_WRITE_FAILED')
            return text
        finally:self.user.CloseClipboard()

    def _key(self,key,up=False):
        class Mouse(ctypes.Structure):
            _fields_=[('dx',w.LONG),('dy',w.LONG),('data',w.DWORD),('flags',w.DWORD),('time',w.DWORD),('extra',ctypes.c_size_t)]
        class Keyboard(ctypes.Structure):
            _fields_=[('vk',w.WORD),('scan',w.WORD),('flags',w.DWORD),('time',w.DWORD),('extra',ctypes.c_size_t)]
        class Data(ctypes.Union):_fields_=[('mouse',Mouse),('key',Keyboard)]
        class Input(ctypes.Structure):_fields_=[('kind',w.DWORD),('data',Data)]
        event=Input(kind=1,data=Data(key=Keyboard(key,0,2 if up else 0,0,0)))
        if self.user.SendInput(1,ctypes.byref(event),ctypes.sizeof(event))!=1:raise ValueError('KEY_INPUT_UNCONFIRMED')

    def _shortcut(self,shortcut):
        keys={'ctrl+a':[0x11,0x41],'ctrl+c':[0x11,0x43],'ctrl+v':[0x11,0x56],
              'right':[0x27],'escape':[0x1b],'esc':[0x1b]}.get(shortcut)
        if not keys:raise ValueError('SHORTCUT_OUTSIDE_ALLOWLIST')
        pressed=[]
        try:
            for key in keys:self._key(key);pressed.append(key)
        finally:
            release_failed=False
            for key in reversed(pressed):
                try:self._key(key,True)
                except Exception:release_failed=True
            if release_failed:raise ValueError('KEY_RELEASE_UNCONFIRMED') from None

    def call(self,tool,arguments):
        attempt=uuid4().hex;started=datetime.now(timezone.utc).isoformat()
        journal={'attempt_id':attempt,'tool':tool,'backend':'WECOM_NATIVE','started_at':started,
                 'status':'OUTCOME_UNCONFIRMED','automatic_retry_allowed':False}
        path=self.directory/('attempt-'+attempt+'.json')
        path.write_text(json.dumps(journal),encoding='utf-8')
        if tool=='PowerShell':
            if arguments!={'command':FOREGROUND_COMMAND,'timeout':10}:raise ValueError('NATIVE_PROBE_OUTSIDE_ALLOWLIST')
            native=self.foreground();self.last_native=native
            content=[{'type':'text','text':'Response: '+json.dumps(native)+'\nStatus Code: 0'}]
        elif tool=='Screenshot':
            from PIL import ImageGrab,Image
            region=self.pin.get('display_bounds')
            if not isinstance(region,list) or len(region)!=4 or region[2]<=region[0] or region[3]<=region[1]:raise ValueError('DISPLAY_BOUNDS_REQUIRED')
            class MonitorInfo(ctypes.Structure):
                _fields_=[('size',w.DWORD),('monitor',w.RECT),('work',w.RECT),('flags',w.DWORD),('device',w.WCHAR*32)]
            self.user.MonitorFromRect.argtypes=[ctypes.POINTER(w.RECT),w.DWORD];self.user.MonitorFromRect.restype=w.HANDLE
            self.user.GetMonitorInfoW.argtypes=[w.HANDLE,ctypes.POINTER(MonitorInfo)]
            rect=w.RECT(*region);monitor=self.user.MonitorFromRect(ctypes.byref(rect),0)
            info=MonitorInfo();info.size=ctypes.sizeof(info)
            if not monitor or not self.user.GetMonitorInfoW(monitor,ctypes.byref(info)) or [info.monitor.left,info.monitor.top,info.monitor.right,info.monitor.bottom]!=region:
                raise ValueError('DISPLAY_LAYOUT_CHANGED')
            image=ImageGrab.grab(bbox=tuple(region),all_screens=True)
            size=self.pin['image_size']
            if list(image.size)!=size:image=image.resize(tuple(size),Image.Resampling.LANCZOS)
            output=self.directory/(attempt+'.png');image.save(output)
            bounds=','.join(map(str,region));index=self.pin['display_index']
            text=f'Selected Displays: {index}\nScreenshot Region: ({bounds})\nVisible Displays: {index}:{info.device} ({bounds})\nCapture Backend: WECOM_NATIVE'
            content=[{'type':'text','text':text},{'type':'image','path':str(output)}]
        elif tool in ('Click','Clipboard','Shortcut','Type','Scroll','Move'):
            self._check_input(arguments.get('loc'))
            if tool=='Move':
                if set(arguments)!={'loc'}:raise ValueError('INVALID_POINTER_MOVE')
                if not self.user.SetCursorPos(*arguments['loc']):raise ValueError('POINTER_MOVE_FAILED')
                content=[]
            elif tool=='Scroll':
                if set(arguments)!={'loc','direction'} or arguments['direction']!='up':
                    raise ValueError('HISTORY_SCROLL_OUTSIDE_ALLOWLIST')
                if not self.user.SetCursorPos(*arguments['loc']):raise ValueError('POINTER_MOVE_FAILED')
                self._check_input(arguments['loc'])
                self.user.mouse_event(0x0800,0,0,360,0)
                content=[]
            elif tool=='Click':
                button=arguments.get('button','left');clicks=arguments.get('clicks',1)
                if button not in ('left','right') or type(clicks) is not int or clicks not in (1,2):raise ValueError('INVALID_CLICK')
                if not self.user.SetCursorPos(*arguments['loc']):raise ValueError('POINTER_MOVE_FAILED')
                for _ in range(clicks):
                    self._check_input(arguments['loc']);down,up=(2,4) if button=='left' else (8,16)
                    self.user.mouse_event(down,0,0,0,0);self.user.mouse_event(up,0,0,0,0)
                content=[]
            elif tool=='Clipboard':content=[{'type':'text','text':'Clipboard content:\n'+self._clipboard(arguments['mode'],arguments.get('text'))}]
            elif tool=='Shortcut':self._shortcut(arguments['shortcut']);content=[]
            else:
                text=arguments.get('text')
                if arguments.get('press_enter') or not isinstance(text,str) or '\n' in text or '\r' in text:raise ValueError('UNSAFE_TYPE')
                if not self.user.SetCursorPos(*arguments['loc']):raise ValueError('POINTER_MOVE_FAILED')
                self.user.mouse_event(2,0,0,0,0);self.user.mouse_event(4,0,0,0,0)
                self._clipboard('set',text);self._shortcut('ctrl+v');content=[]
        else:raise ValueError('NATIVE_OPERATION_OUTSIDE_ALLOWLIST')
        result={'tool':tool,'backend':'WECOM_NATIVE','is_error':False,'attempt_id':attempt,'content':content}
        result_path=self.directory/(attempt+'.json');result_path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        journal.update(status='TOOL_RETURNED',result_path=str(result_path));path.write_text(json.dumps(journal),encoding='utf-8')
        return result
