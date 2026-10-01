"""Offline MCP audit tests. No server process or desktop interaction is started."""
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
from tests.test_mcp_window_probe import result as foreground_result


class WindowsMcpAuditTests(unittest.TestCase):
    def exercise(self, outcome, request=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class Client:
                def __init__(self, transport):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def call_tool(self, *args, **kwargs):
                    attempts = list((root / 'data/private/windows-mcp').glob('attempt-*.json'))
                    assert len(attempts) == 1
                    before = json.loads(attempts[0].read_text(encoding='utf-8'))
                    assert before['status'] == 'OUTCOME_UNCONFIRMED'
                    assert before['automatic_retry_allowed'] is False
                    if args[0] == 'PowerShell':
                        probe = foreground_result(process='WXWork')
                        return SimpleNamespace(is_error=False, content=[SimpleNamespace(**x) for x in probe['content']])
                    if isinstance(outcome, Exception):
                        raise outcome
                    if isinstance(outcome, dict):
                        return SimpleNamespace(is_error=outcome['is_error'], content=[SimpleNamespace(type='text',text=outcome['text'])])
                    return SimpleNamespace(content=[], is_error=outcome)

            fake = ModuleType('fastmcp')
            fake.Client = Client
            transport = ModuleType('fastmcp.client.transports')
            transport.StdioTransport = lambda **kwargs: kwargs
            with patch.dict(sys.modules, {'fastmcp': fake, 'fastmcp.client': ModuleType('fastmcp.client'),
                                          'fastmcp.client.transports': transport}):
                source = Path(__file__).resolve().parents[1] / 'tools/windows_mcp_session.py'
                spec = importlib.util.spec_from_file_location('mcp_audit_fixture', source)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.ROOT = root
                request = (request or '{"tool":"Click","arguments":{"loc":[100,100]},"expected_foreground_process":"WXWork"}') + '\nquit\n'
                stdout = io.StringIO()
                with patch('sys.stdin', io.StringIO(request)), patch('sys.stdout', stdout):
                    asyncio.run(module.main())
                self.last_stdout = stdout.getvalue()
            attempt_file = next((root / 'data/private/windows-mcp').glob('attempt-*.json'), None)
            if attempt_file is None:
                return None
            attempt = json.loads(attempt_file.read_text(encoding='utf-8'))
            if 'result_path' in attempt:
                self.assertTrue(Path(attempt['result_path']).is_file())
                self.last_native_record = json.loads(Path(attempt['result_path']).read_text(encoding='utf-8'))
            return attempt

    def test_timeout_retains_uncertain_intent_without_retry(self):
        self.assertEqual(self.exercise(TimeoutError('unknown outcome'))['status'], 'OUTCOME_UNCONFIRMED')

    def test_multiline_chat_input_rejected_before_any_mcp_call(self):
        request = json.dumps({'tool': 'Type', 'arguments': {'loc': [1, 2], 'text': 'part1\npart2'}})
        self.assertIsNone(self.exercise(False, request))

    def test_tool_error_does_not_become_success(self):
        self.assertEqual(self.exercise(True)['status'], 'TOOL_ERROR_UNCONFIRMED')

    def test_return_is_linked_to_evidence_not_delivery_receipt(self):
        result = self.exercise(False)
        self.assertEqual(result['status'], 'TOOL_RETURNED')
        self.assertFalse(result['automatic_retry_allowed'])

    def test_input_journal_contains_hash_and_length_instead_of_original_input(self):
        request = json.dumps({'tool':'Type','arguments':{'loc':[100,100],'text':'PRIVATE_STUDENT_OR_AUTH_SENTINEL'},'expected_foreground_process':'WXWork'})
        result = self.exercise(False, request)
        self.assertEqual(result['status'], 'TOOL_RETURNED')
        self.assertEqual(result['arguments_storage'], 'INTEGRITY_METADATA_ONLY')
        self.assertNotIn('PRIVATE_STUDENT_OR_AUTH_SENTINEL', json.dumps(result))
        self.assertEqual(len(result['arguments']['sha256']), 64)

    def test_clipboard_read_journal_preserves_existing_raw_record_integrity_contract(self):
        result = self.exercise(False, json.dumps({'tool':'Clipboard','arguments':{'mode':'get'}}))
        self.assertEqual(result['arguments'], {'mode':'get'})

    def test_input_echo_and_native_error_detail_are_not_logged_or_returned(self):
        request = json.dumps({'tool':'Type','arguments':{'loc':[100,100],'text':'PRIVATE_INPUT'},'expected_foreground_process':'WXWork'})
        result = self.exercise({'is_error':False,'text':'PRIVATE_INPUT_ECHO'}, request)
        self.assertEqual(result['status'], 'TOOL_RETURNED')
        self.assertEqual(self.last_native_record['content'], [])
        self.assertNotIn('PRIVATE_INPUT', self.last_stdout)
        self.exercise(RuntimeError('PRIVATE_CREDENTIAL_ERROR'), request)
        self.assertNotIn('PRIVATE_CREDENTIAL_ERROR', self.last_stdout)
        self.assertIn('REQUEST_OR_NATIVE_RESULT_UNCONFIRMED', self.last_stdout)

