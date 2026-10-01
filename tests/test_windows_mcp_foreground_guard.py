"""Offline request-level foreground guard; no desktop or server is started."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.mcp_window_probe import FOREGROUND_COMMAND, parse_foreground_process
from helpdesk.windows_worker_probe import DESKTOP_STATUS_COMMAND
from tests.test_mcp_window_probe import result


class ForegroundGuardTests(unittest.TestCase):
    def exercise(self, request, probe):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Client:
                def __init__(self, transport): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def call_tool(self, tool, args, **kwargs):
                    calls.append((tool, args))
                    if tool == 'PowerShell':
                        if isinstance(probe, Exception): raise probe
                        return SimpleNamespace(is_error=probe['is_error'],
                            content=[SimpleNamespace(**item) for item in probe['content']])
                    attempts = list((root / 'data/private/windows-mcp').glob('attempt-*.json'))
                    before = json.loads(attempts[0].read_text(encoding='utf-8'))
                    self_outer.assertEqual(before['foreground_preflight']['status'], 'PROCESS_MATCHED')
                    return SimpleNamespace(is_error=False, content=[])
            self_outer = self
            fake = ModuleType('fastmcp'); fake.Client = Client
            transport = ModuleType('fastmcp.client.transports')
            transport.StdioTransport = lambda **kwargs: kwargs
            with patch.dict(sys.modules, {'fastmcp': fake, 'fastmcp.client': ModuleType('fastmcp.client'),
                                          'fastmcp.client.transports': transport}):
                path = Path(__file__).resolve().parents[1] / 'tools/windows_mcp_session.py'
                spec = importlib.util.spec_from_file_location('guard_fixture', path)
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                module.ROOT = root
                stdout = io.StringIO()
                with patch('sys.stdin', io.StringIO(json.dumps(request) + '\nquit\n')), patch('sys.stdout', stdout):
                    asyncio.run(module.main())
            attempts = [json.loads(p.read_text(encoding='utf-8')) for p in
                        (root / 'data/private/windows-mcp').glob('attempt-*.json')]
            return calls, attempts, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def request(self, tool='Type', expected='msedge'):
        arguments = {'shortcut': 'ctrl+a'} if tool == 'Shortcut' else {'loc': [100, 100], 'text': 'private file path'}
        return {'tool': tool, 'arguments': arguments,
                'expected_foreground_process': expected}

    def test_wrong_foreground_blocks_all_input_tools_and_records_probe(self):
        for tool in ('Type', 'Click', 'Shortcut', 'Scroll', 'Move'):
            with self.subTest(tool=tool):
                calls, attempts, output = self.exercise(self.request(tool), result(process='WXWork'))
                self.assertEqual(calls, [('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10})])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_FOREGROUND_GUARD')
                self.assertEqual(attempts[0]['foreground_preflight']['observed_foreground']['process'], 'WXWork')
                self.assertFalse(attempts[0]['automatic_retry_allowed'])
                self.assertEqual(output[-1]['detail'], 'FOREGROUND_GUARD_REJECTED')

    def test_matching_foreground_probes_immediately_before_input(self):
        for expected, observed in (('msedge', 'msedge'), ('WXWork', 'WXWork'), ('MSEDGE', 'msedge')):
            calls, attempts, _ = self.exercise(self.request(expected=expected), result(process=observed))
            self.assertEqual([t for t, _ in calls], ['PowerShell', 'Type'])
            self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
            self.assertEqual(attempts[0]['foreground_preflight']['status'], 'PROCESS_MATCHED')

    def test_restored_wecom_window_blocks_stale_pointer_before_native_input(self):
        for tool in ('Click', 'Move', 'Scroll', 'Type'):
            with self.subTest(tool=tool):
                request = self.request(tool=tool, expected='WXWork')
                request['arguments'] = {'loc': [504, -1298]}
                calls, attempts, output = self.exercise(request, result(
                    left=900, top=-1400, width=1400, height=800))
                self.assertEqual([name for name, _ in calls], ['PowerShell'])
                self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_FOREGROUND_GUARD')
                self.assertFalse(attempts[0]['foreground_preflight']['point_in_current_window'])
                self.assertFalse(attempts[0]['automatic_retry_allowed'])
                self.assertEqual(output[-1]['detail'], 'FOREGROUND_GUARD_REJECTED')

    def test_pointer_within_current_wecom_window_remains_available(self):
        for tool in ('Click', 'Move', 'Scroll', 'Type'):
            with self.subTest(tool=tool):
                request = self.request(tool=tool, expected='WXWork')
                request['arguments'] = {'loc': [1000, -1300]}
                calls, attempts, _ = self.exercise(request, result(
                    left=900, top=-1400, width=1400, height=800))
                self.assertEqual([name for name, _ in calls], ['PowerShell', tool])
                self.assertTrue(attempts[0]['foreground_preflight']['point_in_current_window'])
                self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')

    def test_wecom_pointer_requires_explicit_unambiguous_coordinates(self):
        for loc in (None, [True, -100], [1.5, -100], [100], 'private'):
            with self.subTest(loc=loc):
                request = self.request(tool='Click', expected='WXWork')
                request['arguments'] = {'loc': loc}
                calls, attempts, _ = self.exercise(request, result())
                self.assertEqual(calls, [])
                self.assertEqual(attempts, [])

    def test_invalid_guard_has_no_probe_or_input(self):
        for value in (None, False, 1, [], '', ' ', ' msedge', 'msedge\n'):
            calls, attempts, _ = self.exercise(self.request(expected=value), result())
            self.assertEqual(calls, []); self.assertEqual(attempts, [])
        calls, attempts, _ = self.exercise(self.request(tool='Snapshot'), result())
        self.assertEqual(calls, []); self.assertEqual(attempts, [])

    def test_missing_or_unapproved_business_process_cannot_bypass_guard(self):
        request = self.request()
        request.pop('expected_foreground_process')
        for candidate in (request, self.request(expected='chrome'), self.request(expected='powershell')):
            calls, attempts, _ = self.exercise(candidate, result())
            self.assertEqual(calls, [])
            self.assertEqual(attempts, [])

    def test_unknown_probe_outcome_never_inputs_or_retries(self):
        for probe in (TimeoutError('probe uncertain'), {**result(), 'is_error': True},
                      result(handle=0), result(process=''),
                      {'is_error': False, 'content': [{'type': 'text', 'text': 'unexpected'}]}):
            calls, attempts, _ = self.exercise(self.request(), probe)
            self.assertEqual([t for t, _ in calls], ['PowerShell'])
            self.assertEqual(attempts[0]['status'], 'INPUT_BLOCKED_BY_FOREGROUND_GUARD')
            self.assertFalse(attempts[0]['automatic_retry_allowed'])

    def test_general_parser_preserves_actual_process(self):
        self.assertEqual(parse_foreground_process(result(process='msedge'))['process'], 'msedge')

    def test_malformed_shortcut_is_rejected_before_any_native_call_or_intent(self):
        for arguments in ({'keys': ['CTRL', 'A']}, {}, {'shortcut': ['CTRL', 'A']},
                          {'shortcut': ''}, {'shortcut': 'ctrl+a', 'keys': ['A']},
                          {'shortcut': 'ctrl+a\nenter'}):
            with self.subTest(arguments=arguments):
                request = self.request(tool='Shortcut', expected='WXWork')
                request['arguments'] = arguments
                calls, attempts, output = self.exercise(request, result())
                self.assertEqual(calls, [])
                self.assertEqual(attempts, [])
                self.assertEqual(output[-1]['detail'], 'INVALID_SHORTCUT_ARGUMENTS')

    def test_official_shortcut_contract_retains_fresh_foreground_guard(self):
        request = self.request(tool='Shortcut', expected='WXWork')
        calls, attempts, _ = self.exercise(request, result())
        self.assertEqual([tool for tool, _ in calls], ['PowerShell', 'Shortcut'])
        self.assertEqual(calls[-1][1], {'shortcut': 'ctrl+a'})
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])

    def test_reviewed_desktop_probe_is_allowed_without_input_or_auth_capture(self):
        args = {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10}
        calls, attempts, output = self.exercise({'tool': 'PowerShell', 'arguments': args}, result())
        self.assertEqual(calls, [('PowerShell', args)])
        self.assertEqual(attempts[0]['status'], 'TOOL_RETURNED')
        self.assertFalse(attempts[0]['automatic_retry_allowed'])
        self.assertEqual(output[-1]['tool'], 'PowerShell')

    def test_probe_does_not_authorize_free_script_or_extended_parameters(self):
        for args in ({'command': DESKTOP_STATUS_COMMAND + '\nGet-Clipboard', 'timeout': 10},
                     {'command': DESKTOP_STATUS_COMMAND, 'timeout': True},
                     {'command': DESKTOP_STATUS_COMMAND, 'timeout': 20},
                     {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10, 'path': 'private-auth'},
                     {'command': ['Get-Clipboard'], 'timeout': 10}):
            with self.subTest(args=args):
                calls, attempts, _ = self.exercise({'tool': 'PowerShell', 'arguments': args}, result())
                self.assertEqual(calls, [])
                self.assertEqual(attempts, [])


if __name__ == '__main__':
    unittest.main()
