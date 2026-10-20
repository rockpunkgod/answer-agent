"""Win32 boundary mocks only. Never instantiates WinDLL or manipulates a desktop."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from helpdesk.wecom_native import NativeWeComTransport
from helpdesk.mcp_window_probe import FOREGROUND_COMMAND


class NativeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.native=NativeWeComTransport.__new__(NativeWeComTransport)
        self.native.directory=Path(self.temp.name)
        self.native.guard=Mock()
        self.current={'process':'WXWork','handle':1,'left':100,'top':100,'width':200,'height':100}
        self.native.last_native=dict(self.current)
        self.native.foreground=Mock(side_effect=lambda:dict(self.current))
        def name(handle,kind,buffer,size,needed):buffer.value='Default';return True
        self.native.user=SimpleNamespace(OpenInputDesktop=Mock(return_value=1),
            GetUserObjectInformationW=Mock(side_effect=name),CloseDesktop=Mock(),
            GetAsyncKeyState=Mock(return_value=0),
            SetCursorPos=Mock(return_value=True),mouse_event=Mock())

    def test_user_held_modifier_pauses_without_releasing_or_clicking(self):
        for held in (0x10,0x11,0x12,0x5b,0x5c):
            with self.subTest(held=held):
                self.native.user.GetAsyncKeyState.side_effect=lambda key:0x8000 if key==held else 0
                with self.assertRaisesRegex(ValueError,'USER_MODIFIER_HELD'):
                    self.native.call('Click',{'loc':[150,150]})
        self.native.user.SetCursorPos.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_failed_key_release_still_releases_other_keys_pressed_by_us(self):
        calls=[]
        def key(code,up=False):
            calls.append((code,up))
            if code==0x43 and up:raise ValueError('injected failure')
        self.native._key=key
        with self.assertRaisesRegex(ValueError,'KEY_RELEASE_UNCONFIRMED'):
            self.native._shortcut('ctrl+c')
        self.assertEqual(calls,[(0x11,False),(0x43,False),(0x43,True),(0x11,True)])

    def test_failed_key_down_releases_only_previously_inserted_keys(self):
        calls=[]
        def key(code,up=False):
            calls.append((code,up))
            if code==0x56 and not up:raise ValueError('KEY_INPUT_UNCONFIRMED')
        self.native._key=key
        with self.assertRaisesRegex(ValueError,'KEY_INPUT_UNCONFIRMED'):
            self.native._shortcut('ctrl+v')
        self.assertEqual(calls,[(0x11,False),(0x56,False),(0x11,True)])

    def test_stopped_input_never_touches_native_desktop(self):
        self.native.guard.side_effect=ValueError('STOPPED')
        with self.assertRaisesRegex(ValueError,'STOPPED'):
            self.native.call('Click',{'loc':[150,150]})
        self.native.user.OpenInputDesktop.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_lock_window_change_and_outside_point_stop_before_click(self):
        for kind in ('locked','moved','other_process','outside'):
            with self.subTest(kind=kind):
                self.native.user.OpenInputDesktop.return_value=0 if kind=='locked' else 1
                self.current.update(process='OtherApp' if kind=='other_process' else 'WXWork',left=101 if kind=='moved' else 100)
                point=[900,900] if kind=='outside' else [150,150]
                with self.assertRaises(ValueError):self.native.call('Click',{'loc':point})
        self.native.user.SetCursorPos.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_history_scroll_is_one_upward_gesture_and_rechecks_focus(self):
        result=self.native.call('Scroll',{'loc':[150,150],'direction':'up'})
        self.assertEqual(result['backend'],'WECOM_NATIVE')
        self.native.user.mouse_event.assert_called_once_with(0x0800,0,0,360,0)
        self.assertEqual(self.native.foreground.call_count,2)

    def test_scroll_rejects_arbitrary_direction_and_extra_steps(self):
        for args in ({'loc':[150,150],'direction':'down'},
                     {'loc':[150,150],'direction':'up','steps':100}):
            with self.assertRaisesRegex(ValueError,'HISTORY_SCROLL_OUTSIDE_ALLOWLIST'):
                self.native.call('Scroll',args)
        self.native.user.mouse_event.assert_not_called()

    def test_guarded_pointer_move_has_no_click_or_keyboard(self):
        self.native.call('Move',{'loc':[150,150]})
        self.native.user.SetCursorPos.assert_called_once_with(150,150)
        self.native.user.mouse_event.assert_not_called()
        with self.assertRaisesRegex(ValueError,'INVALID_POINTER_MOVE'):
            self.native.call('Move',{'loc':[150,150],'clicks':1})

    def test_pointer_read_returns_actual_coordinates_or_explicitly_fails(self):
        def position(pointer):
            pointer._obj.x=-40;pointer._obj.y=70
            return True
        self.native.user.GetCursorPos=Mock(side_effect=position)
        self.assertEqual(self.native.pointer(),[-40,70])
        self.native.user.GetCursorPos.side_effect=None
        self.native.user.GetCursorPos.return_value=False
        with self.assertRaisesRegex(ValueError,'NATIVE_POINTER_UNAVAILABLE'):
            self.native.pointer()
        self.native.user.SetCursorPos.assert_not_called()

    def test_native_capture_retains_actual_pointer_before_and_after_image(self):
        from PIL import Image
        self.native.pin={'display_bounds':[100,100,300,200],'image_size':[200,100],'display_index':0}
        self.native.user.MonitorFromRect=Mock(return_value=1)
        def monitor(handle,pointer):
            info=pointer._obj
            info.monitor.left=100;info.monitor.top=100;info.monitor.right=300;info.monitor.bottom=200
            info.device='DISPLAY1'
            return True
        self.native.user.GetMonitorInfoW=Mock(side_effect=monitor)
        self.native.pointer=Mock(side_effect=[[150,150],[151,150]])
        with patch('PIL.ImageGrab.grab',return_value=Image.new('RGB',(200,100),'white')):
            result=self.native.call('Screenshot',{})
        saved=json.loads((self.native.directory/(result['attempt_id']+'.json')).read_text())
        self.assertEqual(saved['pointer_before'],[150,150])
        self.assertEqual(saved['pointer_after'],[151,150])
        self.native.user.SetCursorPos.assert_not_called()

    def test_verified_click_records_native_backend_not_windows_mcp(self):
        result=self.native.call('Click',{'loc':[150,150]})
        self.assertEqual(result['backend'],'WECOM_NATIVE')
        self.assertEqual(self.native.user.mouse_event.call_count,2)
        journal=json.loads((self.native.directory/('attempt-'+result['attempt_id']+'.json')).read_text())
        self.assertEqual(journal['backend'],'WECOM_NATIVE')
        self.assertEqual(journal['status'],'TOOL_RETURNED')

    def test_arbitrary_command_shortcut_and_newline_are_not_executable(self):
        for tool,args in [('PowerShell',{'command':'arbitrary command','timeout':10}),
                          ('Shortcut',{'shortcut':'alt+f4'}),
                          ('Type',{'loc':[150,150],'text':'line1\nline2','press_enter':False}),
                          ('OtherTool',{})]:
            with self.subTest(tool=tool),self.assertRaises(ValueError):self.native.call(tool,args)
        self.native.user.SetCursorPos.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_fixed_probe_returns_metadata_without_powershell_or_mcp(self):
        result=self.native.call('PowerShell',{'command':FOREGROUND_COMMAND,'timeout':10})
        self.assertEqual(result['backend'],'WECOM_NATIVE')
        self.assertIn('WXWork',result['content'][0]['text'])
        self.native.user.OpenInputDesktop.assert_not_called()
