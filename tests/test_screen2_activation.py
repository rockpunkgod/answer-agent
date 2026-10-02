"""Offline checks for bounded screen-2 Edge and WeCom caption activation."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.mcp_window_probe import (FOREGROUND_COMMAND, parse_screen2_caption,
                                       screen2_caption_command, WECOM_SCREEN2_CAPTION_COMMAND,
                                       parse_wecom_caption_location, WECOM_SCREEN2_WINDOW_COMMAND,
                                       parse_wecom_window_location, verify_wecom_switch_snapshot,
                                       wecom_window_command)
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


def window_result(**changes):
    value = dict(device=r'\\.\DISPLAY2', process='WXWork', title='企业微信', handle=123,
        screen_left=0, screen_top=-1440, screen_right=2560, screen_bottom=0,
        window_left=0, window_top=-1440, window_right=1200, window_bottom=-400)
    value.update(changes)
    return {'tool': 'PowerShell', 'is_error': False, 'content': [
        {'type': 'text', 'text': 'Response: ' + json.dumps(value) + '\nStatus Code: 0'}]}


def switch_snapshot(rows='企业微信 2 Normal 1200 1040 123', focused='ChatGPT 1 Normal 1000 1000 8'):
    text = (r'Visible Displays: 1:\\.\DISPLAY2 (0,-1440,2560,0)' + '\n'
        'Selected Displays: 1\nScreenshot Region: (0,-1440,2560,0)\n'
        'Focused Window:\nName Depth Status Width Height Handle\n' + focused +
        '\nOpened Windows:\nName Depth Status Width Height Handle\n' + rows +
        '\nUI Tree:\nwindow "untrusted UI content"\n企业微信 99 Normal 999 999 99\n')
    return {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text': json.dumps([text])}]}


class Screen2ActivationTests(unittest.TestCase):
    def request(self, **changes):
        value = {'tool': 'ActivateEdgeOnScreen2', 'arguments': {'loc': [893, -1417]}}
        value.update(changes)
        return value

    def exercise(self, request=None, *, caption=None, desktop=None, click=None, after=None, app_snapshot=None):
        calls = []
        request = request or self.request()
        by_app = request['tool'] in {'ActivateWeComOnScreen2ByApp', 'ActivateWeComOnDisplayByApp'}
        target_process = 'WXWork' if by_app or request['tool'] == 'ActivateWeComOnScreen2' else 'msedge'
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
                            record = after or foreground_result(process=target_process, **(
                                dict(left=0, top=-1440, width=1200, height=1040) if by_app else {}))
                        else:
                            if isinstance(caption, Exception): raise caption
                            record = caption or (window_result() if by_app else caption_result(process=target_process))
                        return SimpleNamespace(is_error=record['is_error'],
                            content=[SimpleNamespace(**item) for item in record['content']])
                    if tool == 'Snapshot' and by_app:
                        self_outer.assertEqual(args, {'use_vision': False, 'use_dom': False,
                            'use_annotation': False, 'use_ui_tree': True,
                            'display': [request['arguments'].get('display_index', 1)]})
                        record = app_snapshot or switch_snapshot()
                        return SimpleNamespace(is_error=record['is_error'], content=[SimpleNamespace(**item) for item in record['content']])
                    self_outer.assertEqual(tool, 'App' if by_app else 'Click')
                    attempt = json.loads(next((root/'data/private/windows-mcp').glob('attempt-*.json')).read_bytes())
                    self_outer.assertEqual(attempt['screen2_activation_preflight']['status'],
                                           'SCREEN2_WECOM_WINDOW_VERIFIED' if by_app else 'SCREEN2_EDGE_CAPTION_VERIFIED' if target_process == 'msedge'
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

    def edge_on_primary(self):
        request = {'tool': 'ActivateEdgeOnDisplay', 'arguments': {
            'loc': [900, 150], 'display_device': r'\\.\DISPLAY1'}}
        target = caption_result(device=r'\\.\DISPLAY1', x=900, y=150,
            screen_top=0, screen_bottom=1600, window_top=100, window_bottom=1140)
        after = foreground_result(process='msedge', left=0, top=100, width=1200, height=1040)
        return request, target, after

    def test_edge_current_display_checks_native_caption_then_same_window(self):
        request, target, after = self.edge_on_primary()
        calls, attempts, output = self.exercise(request, caption=target, after=after)
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'PowerShell', 'Click', 'PowerShell'])
        self.assertEqual(calls[1][1]['command'], screen2_caption_command(
            [900, 150], display_device=r'\\.\DISPLAY1'))
        self.assertEqual(calls[2][1], {'loc': [900, 150], 'button': 'left', 'clicks': 1})
        self.assertEqual(attempts[0]['screen2_activation_preflight']['display_device'], r'\\.\DISPLAY1')
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertEqual(output[-1]['tool'], 'ActivateEdgeOnDisplay')
        self.assertNotIn('INPUT_ECHO', json.dumps(output))

    def test_edge_current_display_blocks_wrong_device_application_and_non_caption(self):
        request, target, after = self.edge_on_primary()
        for changes in ({'display_device': "\\\\.\\DISPLAY1'; Get-Clipboard #"},
                {'display_device': None}, {'loc': [True, 150]}, {'target_process': 'WXWork'}):
            with self.subTest(changes=changes):
                calls, attempts, _ = self.exercise(request | {'arguments': request['arguments'] | changes})
                self.assertEqual(calls, [])
                self.assertEqual(attempts, [])
        raw = json.loads(target['content'][0]['text'][len('Response: '):].rsplit('\nStatus Code:', 1)[0])
        for changes in ({'process': 'ChatGPT'}, {'device': r'\\.\DISPLAY2'}, {'hit_test': 1}, {'x': 899}):
            with self.subTest(changes=changes):
                calls, attempts, _ = self.exercise(request, caption=caption_result(**(raw | changes)), after=after)
                self.assertNotIn('Click', [tool for tool, _ in calls])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')

    def test_edge_current_display_unknown_click_or_moved_window_never_replays(self):
        request, target, after = self.edge_on_primary()
        for failure, postflight in ((TimeoutError('private error'), after),
                (None, foreground_result(process='msedge', left=1, top=100, width=1200, height=1040)),
                (None, foreground_result(process='msedge', handle=999, left=0, top=100, width=1200, height=1040))):
            calls, attempts, output = self.exercise(request, caption=target, click=failure, after=postflight)
            self.assertEqual(sum(tool == 'Click' for tool, _ in calls), 1)
            self.assertNotEqual(attempts[0]['status'], 'TOOL_RETURNED')
            self.assertFalse(attempts[0]['automatic_retry_allowed'])
            self.assertNotIn('private error', json.dumps(output))

    def test_app_switch_uses_exact_cached_screen2_window_without_a_caption_click(self):
        calls, attempts, output = self.exercise({'tool': 'ActivateWeComOnScreen2ByApp', 'arguments': {}})
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'Snapshot', 'PowerShell', 'App', 'PowerShell'])
        self.assertEqual(calls[2][1], {'command': WECOM_SCREEN2_WINDOW_COMMAND, 'timeout': 10})
        self.assertEqual(calls[3][1], {'mode': 'switch', 'name': '企业微信'})
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertEqual(attempts[0]['screen2_activation_postflight']['status'], 'TARGET_FOREGROUND_VERIFIED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])
        self.assertNotIn('untrusted UI content', json.dumps(attempts))
        self.assertEqual(output[-1]['content'], [])

    def primary_display(self):
        request = {'tool': 'ActivateWeComOnDisplayByApp', 'arguments': {
            'display_index': 0, 'display_device': r'\\.\DISPLAY1'}}
        target = window_result(device=r'\\.\DISPLAY1', screen_top=0, screen_bottom=1600,
            window_top=0, window_bottom=1040)
        snapshot = switch_snapshot()
        snapshot['content'][0]['text'] = snapshot['content'][0]['text'].replace(
            '1:', '0:').replace('DISPLAY2', 'DISPLAY1').replace(
            'Selected Displays: 1', 'Selected Displays: 0').replace('(0,-1440,2560,0)', '(0,0,2560,1600)')
        after = foreground_result(left=0, top=0, width=1200, height=1040)
        return request, target, snapshot, after

    def test_configured_display_uses_unique_native_window_and_cached_handle(self):
        request, target, snapshot, after = self.primary_display()
        calls, attempts, output = self.exercise(request, caption=target, app_snapshot=snapshot, after=after)
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'Snapshot', 'PowerShell', 'App', 'PowerShell'])
        self.assertEqual(calls[2][1]['command'], wecom_window_command(r'\\.\DISPLAY1'))
        self.assertEqual(calls[3][1], {'mode': 'switch', 'name': '企业微信'})
        proof = attempts[0]['screen2_activation_preflight']
        self.assertEqual((proof['display_index'], proof['display_device']), (0, r'\\.\DISPLAY1'))
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertEqual(output[-1]['tool'], 'ActivateWeComOnDisplayByApp')
        self.assertEqual(output[-1]['content'], [])
        self.assertNotIn('untrusted UI content', json.dumps(attempts))

    def test_configured_display_rejects_scope_and_cached_identity_changes(self):
        request, target, snapshot, after = self.primary_display()
        for changed in (switch_snapshot(), switch_snapshot(rows='企业微信 2 Normal 1200 1040 999'),
                {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text':
                    snapshot['content'][0]['text'].replace(' 123', ' 999')}]}):
            with self.subTest(snapshot=changed):
                calls, attempts, _ = self.exercise(request, caption=target, app_snapshot=changed, after=after)
                self.assertFalse(any(tool in ('App', 'Click') for tool, _ in calls))
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')
        calls, _, _ = self.exercise(request, caption=window_result(), app_snapshot=snapshot, after=after)
        self.assertNotIn('App', [tool for tool, _ in calls])

    def test_configured_display_parameters_cannot_become_native_code_or_other_targets(self):
        request, _, _, _ = self.primary_display()
        for changes in ({'display_index': True}, {'display_index': -1}, {'display_index': '0'},
                {'display_device': "\\\\.\\DISPLAY1'; Get-Clipboard #"}, {'display_device': None},
                {'display_device': r'\\.\DISPLAY0'}, {'name': '微信'}, {'mode': 'launch'}):
            with self.subTest(changes=changes):
                calls, attempts, _ = self.exercise(request | {'arguments': request['arguments'] | changes})
                self.assertEqual(calls, [])
                self.assertEqual(attempts, [])
        command = wecom_window_command(r'\\.\DISPLAY1')
        for forbidden in ('SetForegroundWindow', 'ShowWindow', 'SetWindowPos', 'SendKeys'):
            self.assertNotIn(forbidden, command)

    def test_configured_display_timeout_or_window_move_is_not_replayed(self):
        request, target, snapshot, after = self.primary_display()
        for failure, postflight in ((TimeoutError('private native error'), after),
                (None, foreground_result(left=1, top=0, width=1200, height=1040))):
            calls, attempts, output = self.exercise(request, caption=target, app_snapshot=snapshot,
                click=failure, after=postflight)
            self.assertEqual(sum(tool == 'App' for tool, _ in calls), 1)
            self.assertNotEqual(attempts[0]['status'], 'TOOL_RETURNED')
            self.assertFalse(attempts[0]['automatic_retry_allowed'])
            self.assertNotIn('private native error', json.dumps(output))

    def test_app_switch_rejects_missing_ambiguous_changed_or_other_screen_windows(self):
        for snapshot, target in ((switch_snapshot(rows='企业微信 2 Normal 1200 1040 124'), window_result()),
                (switch_snapshot(rows='企业微信 2 Normal 1200 1040 123\n企业微信 3 Normal 1200 1040 124'), window_result()),
                (switch_snapshot(rows='Not企业微信 2 Normal 1200 1040 123'), window_result()),
                (switch_snapshot(rows='No windows found', focused='企业微信 2 Normal 1200 1040 123'), window_result()),
                (switch_snapshot(rows='Chrome 2 Normal 1200 1040 456', focused='企业微信 2 Normal 1200 1040 123'), window_result()),
                (switch_snapshot(), window_result(window_top=-1430)),
                (switch_snapshot(), window_result(device=r'\\.\DISPLAY1')),
                (switch_snapshot(), window_result(window_left=-1))):
            with self.subTest(snapshot=snapshot, target=target):
                calls, attempts, _ = self.exercise({'tool': 'ActivateWeComOnScreen2ByApp', 'arguments': {}},
                    caption=target, app_snapshot=snapshot)
                self.assertFalse(any(tool in {'App', 'Click'} for tool, _ in calls))
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')

    def test_app_switch_forbids_arbitrary_target_arguments_and_locked_desktop(self):
        for arguments in ({'name': '微信'}, {'mode': 'launch'}, {'loc': [0, 0]}, []):
            calls, attempts, _ = self.exercise({'tool': 'ActivateWeComOnScreen2ByApp', 'arguments': arguments})
            self.assertEqual(calls, []); self.assertEqual(attempts, [])
        calls, attempts, _ = self.exercise({'tool': 'ActivateWeComOnScreen2ByApp', 'arguments': {}}, desktop={'session_flags': 0})
        self.assertEqual([tool for tool, _ in calls], ['PowerShell'])
        self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_SCREEN2_GUARD')

    def test_app_timeout_and_postflight_change_stop_without_another_action(self):
        for outcome, after in ((TimeoutError('private error'), None),
                (None, foreground_result(process='WXWork', handle=124)),
                (None, foreground_result(process='WXWork', left=0, top=0, width=1200, height=1040))):
            with self.subTest(outcome=type(outcome).__name__, after=after):
                calls, attempts, output = self.exercise({'tool': 'ActivateWeComOnScreen2ByApp', 'arguments': {}},
                    click=outcome, after=after)
                self.assertEqual(sum(tool == 'App' for tool, _ in calls), 1)
                self.assertNotEqual(attempts[0]['status'], 'TOOL_RETURNED')
                self.assertFalse(attempts[0]['automatic_retry_allowed'])
                self.assertNotIn('private error', json.dumps(output))
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
                self.assertEqual(attempts[0]['screen2_activation_postflight']['observed']['handle'], changes['handle'])
                self.assertEqual(attempts[0]['screen2_activation_postflight']['observed']['process'], changes['process'])
                self.assertEqual(attempts[0]['screen2_activation_postflight']['probe_record']['tool'], 'PowerShell')
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

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_caption_locator_filters_other_displays_hidden_minimized_and_spanning_windows(self):
        # Execute the production PowerShell against invented windows. This type
        # replaces every native call and contains no DllImport or desktop input.
        fixture = r'''
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class HelpdeskScreen2CaptionProbe {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct MonitorInfo {
  public int Size; public Rect Monitor,Work; public uint Flags;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string Device;
 }
 public static Dictionary<int,Rect> Rows=new Dictionary<int,Rect>();
 public static Dictionary<int,string> Titles=new Dictionary<int,string>();
 public static HashSet<int> Hidden=new HashSet<int>(),Minimized=new HashSet<int>();
 public static IntPtr SetThreadDpiAwarenessContext(IntPtr value) { return new IntPtr(99); }
 public static bool IsWindowVisible(IntPtr value) { return !Hidden.Contains(value.ToInt32()); }
 public static bool IsIconic(IntPtr value) { return Minimized.Contains(value.ToInt32()); }
 public static bool GetWindowRect(IntPtr value,out Rect rect) { return Rows.TryGetValue(value.ToInt32(),out rect); }
 public static string WindowTitle(IntPtr value) { return Titles[value.ToInt32()]; }
 public static string WindowClass(IntPtr value) { return "FixtureWeComWindow"; }
 public static IntPtr[] ApplicationRoots(uint[] ids) {
  var windows=new List<IntPtr>(); foreach(var key in Rows.Keys) {
   if (!Hidden.Contains(key) && !Minimized.Contains(key)) windows.Add(new IntPtr(key));
  } return windows.ToArray();
 }
 public static IntPtr MonitorFromPoint(Point value,uint flags) { return new IntPtr(value.Y<0?2:1); }
 public static bool GetMonitorInfo(IntPtr value,ref MonitorInfo info) {
  info.Device=value.ToInt32()==2?@"\\.\DISPLAY2":@"\\.\DISPLAY1";
  info.Monitor=new Rect {Left=0,Top=value.ToInt32()==2?-1440:0,Right=2560,Bottom=value.ToInt32()==2?0:1440};
  return true;
 }
 public static IntPtr WindowFromPoint(Point value) { return new IntPtr(1); }
 public static IntPtr GetAncestor(IntPtr value,uint flags) { return value; }
 public static IntPtr SendMessageTimeout(IntPtr window,uint message,UIntPtr wp,IntPtr lp,uint flags,uint timeout,out UIntPtr result) {
  result=new UIntPtr(2); return new IntPtr(1);
 }
}
'@
$script:FixtureApps=@()
foreach ($row in (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows.json') -Raw | ConvertFrom-Json)) {
 $rect=New-Object HelpdeskScreen2CaptionProbe+Rect
 $rect.Left=$row.rect[0]; $rect.Top=$row.rect[1]; $rect.Right=$row.rect[2]; $rect.Bottom=$row.rect[3]
 [HelpdeskScreen2CaptionProbe]::Rows.Add([int]$row.id,$rect)
 [HelpdeskScreen2CaptionProbe]::Titles.Add([int]$row.id, $(if ($row.title) {$row.title} else {'企业微信'}))
 if ($row.hidden) { [void][HelpdeskScreen2CaptionProbe]::Hidden.Add([int]$row.id) }
 if ($row.minimized) { [void][HelpdeskScreen2CaptionProbe]::Minimized.Add([int]$row.id) }
 $script:FixtureApps += [pscustomobject]@{Id=$row.id;MainWindowHandle=[IntPtr]99;MainWindowTitle='图片'}
}
function Get-Process { [CmdletBinding()] param([string]$Name) $script:FixtureApps }
'''
        selected = {'id': 1, 'rect': [0, -1440, 2560, -60]}
        excluded = [
            {'id': 2, 'rect': [0, 0, 2560, 1440]},
            {'id': 3, 'rect': [0, -1440, 1000, -800], 'hidden': True},
            {'id': 4, 'rect': [0, -1440, 1000, -800], 'minimized': True},
            {'id': 5, 'rect': [-1, -1440, 2560, 0]},
            {'id': 7, 'rect': [800, -1300, 1600, -100], 'title': '图片'},
        ]
        cases = [([selected, *excluded], None),
                 ([selected, *[{'id': index, 'rect': [0, -1440, 1000, -800], 'hidden': True}
                               for index in range(10, 35)]], None),
                 ([selected, {'id': 6, 'rect': [10, -1400, 1000, -800]}], 'WECOM_SCREEN2_MAIN_WINDOW_AMBIGUOUS'),
                 (excluded, 'VISIBLE_SCREEN2_WECOM_MAIN_WINDOW_UNAVAILABLE')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'fixture.ps1'
            script.write_text(fixture + '\ntry {\n' + WECOM_SCREEN2_CAPTION_COMMAND +
                '\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }', encoding='utf-8-sig')
            for rows, expected_error in cases:
                with self.subTest(expected_error=expected_error):
                    (root / 'windows.json').write_text(json.dumps(rows), encoding='utf-8-sig')
                    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script)],
                        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                    if expected_error:
                        self.assertEqual(result.returncode, 1)
                        self.assertIn(expected_error, result.stderr.decode('utf-8', errors='replace'))
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                        proof = json.loads(result.stdout)
                        self.assertEqual(proof['handle'], 1)
                        self.assertEqual(proof['device'], r'\\.\DISPLAY2')
                        self.assertEqual(proof['hit_test'], 2)


if __name__ == '__main__':
    unittest.main()
