"""Offline checks for bounded screen-2 Edge and WeCom caption activation."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.mcp_window_probe import (FOREGROUND_COMMAND, parse_screen2_caption,
                                       screen2_caption_command, WECOM_SCREEN2_CAPTION_COMMAND,
                                       parse_wecom_caption_location)
from helpdesk.windows_worker_probe import DESKTOP_STATUS_COMMAND
from tests.test_mcp_window_probe import result as foreground_result


def caption_result(**changes):
    value = dict(device=r'\\.\DISPLAY2', x=893, y=-1417, process='msedge', handle=123,
                 hit_test=2, screen_left=0, screen_top=-1440, screen_right=2560,
                 screen_bottom=0, window_left=0, window_top=-1440,
                 window_right=1200, window_bottom=-400)
    value.update(changes)
    return {'tool': 'PowerShell', 'is_error': False, 'content': [
        {'type': 'text', 'text': 'Response: ' + json.dumps(value) + '\nStatus Code: 0'}]}


class Screen2ActivationTests(unittest.TestCase):
    def request(self, **changes):
        value = {'tool': 'ActivateEdgeOnScreen2', 'arguments': {'loc': [893, -1417]}}
        value.update(changes)
        return value

    def exercise(self, request=None, *, caption=None, desktop=None, click=None, after=None):
        calls = []
        request = request or self.request()
        target_process = 'WXWork' if request['tool'] == 'ActivateWeComOnScreen2' else 'msedge'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Client:
                def __init__(self, transport): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def call_tool(self, tool, args, **kwargs):
                    calls.append((tool, args))
                    if tool == 'PowerShell':
                        if args['command'] == DESKTOP_STATUS_COMMAND:
                            value = dict(schema_version=1,
                                observed_at=datetime.now(timezone.utc).isoformat(),
                                session_id=1, wts_state=0, session_flags=1, remote=False,
                                input_desktop='DEFAULT', input_accessible=True,
                                windows={'wxwork': 1, 'edge': 1})
                            value.update(desktop or {})
                            record = {'is_error': False, 'content': [{'type': 'text',
                                'text': 'Response: '+json.dumps(value)+'\nStatus Code: 0'}]}
                        elif args['command'] == FOREGROUND_COMMAND:
                            record = after or foreground_result(process=target_process)
                        else:
                            if isinstance(caption, Exception): raise caption
                            record = caption or caption_result(process=target_process)
                        return SimpleNamespace(is_error=record['is_error'],
                            content=[SimpleNamespace(**item) for item in record['content']])
                    self_outer.assertEqual(tool, 'Click')
                    attempt = json.loads(next((root/'data/private/windows-mcp').glob('attempt-*.json')).read_bytes())
                    self_outer.assertEqual(attempt['screen2_activation_preflight']['status'],
                                           'SCREEN2_EDGE_CAPTION_VERIFIED' if target_process == 'msedge'
                                           else 'SCREEN2_WECOM_CAPTION_VERIFIED')
                    self_outer.assertEqual(attempt['status'], 'OUTCOME_UNCONFIRMED')
                    if isinstance(click, Exception): raise click
                    return SimpleNamespace(is_error=False, content=[SimpleNamespace(type='text', text='INPUT_ECHO')])
            self_outer = self
            fake = ModuleType('fastmcp'); fake.Client = Client
            transport = ModuleType('fastmcp.client.transports')
            transport.StdioTransport = lambda **kwargs: kwargs
            with patch.dict(sys.modules, {'fastmcp': fake, 'fastmcp.client': ModuleType('fastmcp.client'),
                                          'fastmcp.client.transports': transport}):
                path = Path(__file__).resolve().parents[1]/'tools/windows_mcp_session.py'
                spec = importlib.util.spec_from_file_location('screen2_fixture', path)
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                module.ROOT = root
                stdout = io.StringIO()
                with patch('sys.stdin', io.StringIO(json.dumps(request)+'\nquit\n')), patch('sys.stdout', stdout):
                    asyncio.run(module.main())
            attempts = [json.loads(p.read_bytes()) for p in (root/'data/private/windows-mcp').glob('attempt-*.json')]
            return calls, attempts, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_one_native_caption_click_and_exact_foreground_handle_verified(self):
        calls, attempts, output = self.exercise()
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell', 'Click', 'PowerShell'])
        self.assertEqual(calls[2][1], {'loc': [893, -1417], 'button': 'left', 'clicks': 1})
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertEqual(attempts[0]['screen2_activation_postflight']['status'], 'TARGET_FOREGROUND_VERIFIED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])
        self.assertEqual(output[-1]['content'], [])
        self.assertNotIn('INPUT_ECHO', json.dumps(output))

    def test_wecom_activates_directly_without_an_edge_window_or_business_input(self):
        calls, attempts, output = self.exercise(self.request(tool='ActivateWeComOnScreen2'))
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell', 'Click', 'PowerShell'])
        self.assertEqual(calls[2][1], {'loc': [893, -1417], 'button': 'left', 'clicks': 1})
        self.assertEqual(attempts[0]['screen2_activation_preflight']['target']['process'], 'WXWork')
        self.assertEqual(attempts[0]['screen2_activation_postflight']['observed']['process'], 'WXWork')
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])
        self.assertEqual(output[-1]['tool'], 'ActivateWeComOnScreen2')
        self.assertEqual(output[-1]['content'], [])

    def test_wecom_activation_rejects_wrong_app_screen_and_changed_handle(self):
        request = self.request(tool='ActivateWeComOnScreen2')
        for changes in ({'process': 'msedge'}, {'process': 'ChatGPT'},
                        {'device': r'\\.\DISPLAY1'}, {'hit_test': 1}):
            with self.subTest(changes=changes):
                calls, attempts, _ = self.exercise(request, caption=caption_result(**changes))
                self.assertNotIn('Click', [tool for tool, _ in calls])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')
        calls, attempts, output = self.exercise(request, after=foreground_result(handle=456))
        self.assertEqual([tool for tool, _ in calls].count('Click'), 1)
        self.assertEqual(attempts[0]['status'], 'ACTIVATION_RESULT_UNCONFIRMED')
        self.assertEqual(output[-1]['detail'], 'SCREEN2_ACTIVATION_UNCONFIRMED')

    def test_target_process_cannot_be_supplied_as_free_code_or_request_argument(self):
        for process in ('ChatGPT', 'WXWork;Get-Clipboard', None, ['WXWork']):
            with self.subTest(process=process), self.assertRaises(ValueError):
                screen2_caption_command([893, -1417], target_process=process)
        calls, attempts, _ = self.exercise(self.request(tool='ActivateWeComOnScreen2',
            arguments={'loc': [893, -1417], 'target_process': 'ChatGPT'}))
        self.assertEqual(calls, [])
        self.assertEqual(attempts, [])

    def test_screen1_other_app_and_non_caption_never_receive_click(self):
        for changes in ({'device': r'\\.\DISPLAY1'}, {'process': 'ChatGPT'}, {'hit_test': 1},
                        {'handle': 0}, {'x': 1000}, {'screen_bottom': -1420},
                        {'window_left': 1000}, {'handle': True}):
            with self.subTest(changes=changes):
                calls, attempts, output = self.exercise(caption=caption_result(**changes))
                self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell'])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')
                self.assertEqual(output[-1]['detail'], 'SCREEN2_ACTIVATION_REJECTED')

    def test_locked_remote_and_unavailable_desktop_block_before_target_probe(self):
        for changes in ({'session_flags': 0}, {'remote': True}, {'input_accessible': False,
                        'input_desktop': None}, {'wts_state': 4}):
            with self.subTest(changes=changes):
                calls, attempts, _ = self.exercise(desktop=changes)
                self.assertEqual([tool for tool, _ in calls], ['PowerShell'])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')

    def test_unknown_target_probe_does_not_click_or_retry(self):
        calls, attempts, _ = self.exercise(caption=TimeoutError('PRIVATE_NATIVE_ERROR'))
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell'])
        self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])
        self.assertNotIn('PRIVATE_NATIVE_ERROR', json.dumps(attempts))

    def test_click_timeout_retains_unknown_result_and_never_clicks_again(self):
        calls, attempts, _ = self.exercise(click=TimeoutError('unknown click'))
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell', 'Click'])
        self.assertEqual(attempts[0]['status'], 'OUTCOME_UNCONFIRMED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])

    def test_different_edge_handle_or_app_after_click_is_not_success(self):
        for changes in ({'handle': 456, 'process': 'msedge'}, {'handle': 123, 'process': 'ChatGPT'}):
            with self.subTest(changes=changes):
                calls, attempts, output = self.exercise(after=foreground_result(**changes))
                self.assertEqual([tool for tool, _ in calls].count('Click'), 1)
                self.assertEqual(attempts[0]['status'], 'ACTIVATION_RESULT_UNCONFIRMED')
                self.assertTrue(attempts[0]['native_click_returned'])
                self.assertFalse(attempts[0]['automatic_retry_allowed'])
                self.assertEqual(output[-1]['detail'], 'SCREEN2_ACTIVATION_UNCONFIRMED')

    def test_invalid_coordinates_and_extra_input_parameters_make_no_native_calls(self):
        for args in ({'loc': [True, -1417]}, {'loc': [1.5, -1417]}, {'loc': ['1;Get-Clipboard', 2]},
                     {'loc': [32768, 2]}, {'loc': [1]}, {'loc': [1, 2], 'text': 'private'},
                     {'loc': [1, 2], 'clicks': 2}, {'loc': [1, 2], 'button': 'right'}):
            with self.subTest(args=args):
                calls, attempts, _ = self.exercise(self.request(arguments=args))
                self.assertEqual(calls, []); self.assertEqual(attempts, [])
        calls, attempts, _ = self.exercise(self.request(expected_foreground_process='ChatGPT'))
        self.assertEqual(calls, []); self.assertEqual(attempts, [])

    def test_internal_caption_probe_is_not_an_arbitrary_powershell_entry(self):
        calls, attempts, _ = self.exercise({'tool': 'PowerShell',
            'arguments': {'command': screen2_caption_command([893, -1417]), 'timeout': 10}})
        self.assertEqual(calls, []); self.assertEqual(attempts, [])

    def test_title_bar_proof_requires_original_native_record_and_exact_point(self):
        good = caption_result()
        self.assertEqual(parse_screen2_caption(good, [893, -1417])['handle'], 123)
        for bad in ({**good, 'is_error': True}, {**good, 'tool': 'Snapshot'},
                    {**good, 'content': good['content']*2},
                    {'tool': 'PowerShell', 'is_error': False, 'content': [{'type': 'text', 'text': 'guess'}]}):
            with self.subTest(record=bad), self.assertRaises(ValueError):
                parse_screen2_caption(bad, [893, -1417])

    def test_native_wecom_caption_locator_only_reads_and_does_not_activate(self):
        calls, attempts, output = self.exercise({'tool': 'PowerShell', 'arguments': {
            'command': WECOM_SCREEN2_CAPTION_COMMAND, 'timeout': 10}},
            caption=caption_result(process='WXWork'))
        self.assertEqual([tool for tool, _ in calls], ['PowerShell'])
        self.assertEqual(attempts[0]['arguments']['probe'], 'WECOM_SCREEN2_CAPTION')
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertEqual(parse_wecom_caption_location(output[-1])['handle'], 123)
        for forbidden in ('SetForegroundWindow', 'ShowWindow', 'SetWindowPos', 'mouse_event', 'SendKeys'):
            self.assertNotIn(forbidden, WECOM_SCREEN2_CAPTION_COMMAND)

    def test_caption_locator_rejects_wrong_process_display_and_spanning_window(self):
        self.assertEqual(parse_wecom_caption_location(caption_result(process='WXWork'))['x'], 893)
        for changes in ({'process': 'ChatGPT'}, {'device': r'\\.\DISPLAY1'}, {'hit_test': 1},
                        {'window_left': -1}, {'window_top': -1441}, {'window_right': 2561},
                        {'window_bottom': 1}, {'x': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_wecom_caption_location(caption_result(process='WXWork', **changes)
                    if 'process' not in changes else caption_result(**changes))

    def test_caption_locator_command_is_literal_and_cannot_add_code(self):
        calls, attempts, _ = self.exercise({'tool': 'PowerShell', 'arguments': {
            'command': WECOM_SCREEN2_CAPTION_COMMAND + '\nGet-Clipboard', 'timeout': 10}})
        self.assertEqual(calls, []); self.assertEqual(attempts, [])


if __name__ == '__main__':
    unittest.main()
