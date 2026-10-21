"""Anonymous Win32 boundary mocks only; no real account or desktop operations."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from helpdesk.personal_wechat_test_transport import NativePersonalWeChatTestTransport
from helpdesk.wecom_native import NativeWeComTransport
from tests import test_wecom_native


def reviewed_pin():
    return {'platform': 'personal_wechat_test', 'display_name': '测试',
            'target_key': 'anonymous-test@chatroom', 'reviewed': True,
            'verification_evidence': 'synthetic reviewed room binding',
            'expires_at': time.time() + 60, 'window_geometry_handle': [200, 100, 1]}


class PersonalTestNativeTests(unittest.TestCase):
    def setUp(self):
        fixture = test_wecom_native.NativeGuardTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.native = fixture.native
        self.native.__class__ = NativePersonalWeChatTestTransport
        self.native.pin = reviewed_pin()
        self.current = fixture.current
        self.current['process'] = 'Weixin'
        self.native.last_native = dict(self.current)

    def test_only_explicit_test_group_can_construct_and_pin_is_copied(self):
        pin = reviewed_pin()
        with patch.object(NativeWeComTransport, '__init__', return_value=None) as init:
            NativePersonalWeChatTestTransport(pin, Path('unused'), guard=lambda: None)
            passed = init.call_args.args[0]
            pin['target_key'] = 'changed@chatroom'
            self.assertEqual(passed['target_key'], 'anonymous-test@chatroom')
            init.reset_mock()
            for change in ({'platform': 'wecom'}, {'display_name': '答疑1群'},
                           {'target_key': '测试'}, {'reviewed': False},
                           {'verification_evidence': ''}, {'expires_at': float('nan')},
                           {'expires_at': True}, {'expires_at': time.time() - 1}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    NativePersonalWeChatTestTransport(dict(reviewed_pin(), **change),
                                                      Path('unused'), guard=lambda: None)
            with self.assertRaisesRegex(ValueError, 'TASK_AND_TARGET_GUARD'):
                NativePersonalWeChatTestTransport(reviewed_pin(), Path('unused'), guard=None)
            init.assert_not_called()

    def test_wecom_default_still_refuses_personal_wechat(self):
        self.native.__class__ = NativeWeComTransport
        with self.assertRaisesRegex(ValueError, 'FOREGROUND_OR_LAYOUT_CHANGED'):
            self.native.call('Click', {'loc': [150, 150]})
        self.native.user.SetCursorPos.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_personal_boundary_refuses_wecom_other_app_and_moved_window(self):
        for change in ({'process': 'WXWork'}, {'process': 'UserApp'}, {'left': 101}):
            old = dict(self.current)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.current.update(change)
                self.native.call('Click', {'loc': [150, 150]})
            self.current.update(old)
        self.native.user.SetCursorPos.assert_not_called()
        self.native.user.mouse_event.assert_not_called()

    def test_expiry_scope_change_and_pause_stop_before_clipboard_or_input(self):
        self.native._clipboard = Mock()
        for change in ({'expires_at': time.time() - 1}, {'display_name': 'Other'}):
            self.native.pin = dict(reviewed_pin(), **change)
            with self.assertRaises(ValueError):
                self.native.call('Clipboard', {'mode': 'set', 'text': 'anonymous body'})
        self.native.pin = reviewed_pin()
        self.native.guard.side_effect = ValueError('PAUSED_OR_VERSION_CHANGED')
        with self.assertRaisesRegex(ValueError, 'PAUSED_OR_VERSION_CHANGED'):
            self.native.call('Click', {'loc': [150, 150]})
        self.native._clipboard.assert_not_called()
        self.native.user.SetCursorPos.assert_not_called()

    def test_input_has_personal_evidence_not_wecom_receipt(self):
        result = self.native.call('Click', {'loc': [150, 150]})
        self.assertEqual(result['backend'], 'PERSONAL_WECHAT_TEST_NATIVE')
        self.assertNotIn('confirmed', result)
        journal = json.loads((self.native.directory / ('attempt-' + result['attempt_id'] + '.json')).read_text())
        self.assertEqual(journal['backend'], 'PERSONAL_WECHAT_TEST_NATIVE')
        self.assertFalse(journal['automatic_retry_allowed'])
        self.native.user.mouse_event.assert_any_call(2, 0, 0, 0, 0)

    def test_user_pointer_move_stops_personal_click(self):
        self.native.pointer = Mock(return_value=[999, 999])
        with self.assertRaisesRegex(ValueError, 'USER_POINTER_MOVED_INPUT_PAUSED'):
            self.native.call('Click', {'loc': [150, 150]})
        self.native.user.mouse_event.assert_not_called()

    def test_submission_exception_keeps_unknown_intent_without_retry(self):
        self.native._shortcut = Mock(side_effect=TimeoutError('synthetic interruption'))
        with self.assertRaises(TimeoutError):
            self.native.call('Shortcut', {'shortcut': 'enter'})
        self.native._shortcut.assert_called_once_with('enter')
        entries = list(self.native.directory.glob('attempt-*.json'))
        self.assertEqual(len(entries), 1)
        saved = json.loads(entries[0].read_text())
        self.assertEqual(saved['status'], 'OUTCOME_UNCONFIRMED')
        self.assertFalse(saved['automatic_retry_allowed'])

    def test_arbitrary_command_is_still_not_executable(self):
        with self.assertRaisesRegex(ValueError, 'PROBE_OUTSIDE_ALLOWLIST'):
            self.native.call('PowerShell', {'command': 'arbitrary', 'timeout': 10})
        self.native.user.SetCursorPos.assert_not_called()
