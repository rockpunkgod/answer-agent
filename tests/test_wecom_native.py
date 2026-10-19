"""Win32 boundary mocks only. Never instantiates WinDLL or manipulates a desktop."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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
            SetCursorPos=Mock(return_value=True),mouse_event=Mock())

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
