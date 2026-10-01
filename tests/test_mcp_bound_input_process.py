"""Real transport wire serialization with an anonymous fake stdio child only."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.mcp_transport import MCPProcess, MCPTransportError, GUARDED_INPUTS
from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.mcp_test_delivery import MCPTestAnswerDesktop


class FakeInput(io.StringIO):
    def close(self):
        pass  # Keep anonymous wire bytes available for assertions after close.


class FakeChild:
    pid=12345
    def __init__(self, tools):
        self.stdin=FakeInput()
        self.stdout=io.StringIO(json.dumps({'ready':True})+'\n'+''.join(
            json.dumps({'tool':tool,'content':[],'is_error':False})+'\n' for tool in tools))
    def wait(self, timeout=None):
        return 0


class MCPBoundInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, tools=(), bound=None):
        child=FakeChild(tools)
        process=MCPProcess('anonymous-python',root=self.root,stderr_path=self.root/'stderr.log',
            process_factory=lambda *args,**kwargs:child,bound_input_process=bound)
        return process,child

    def requests(self, child):
        return [json.loads(line) for line in child.stdin.getvalue().splitlines() if line!='quit']

    def test_unbound_inputs_preserve_absence_for_wrapper_rejection(self):
        tools=sorted(GUARDED_INPUTS)
        process,child=self.transport(tools)
        with process:
            for tool in tools:process.call(tool,{})
        self.assertTrue(all('expected_foreground_process' not in r for r in self.requests(child)))

    def test_binding_injects_all_inputs_but_not_snapshot(self):
        tools=sorted(GUARDED_INPUTS)+['Snapshot']
        process,child=self.transport(tools,'msedge')
        with process:
            for tool in tools:process.call(tool,{})
        wire=self.requests(child)
        self.assertTrue(all(r['expected_foreground_process']=='msedge' for r in wire[:-1]))
        self.assertNotIn('expected_foreground_process',wire[-1])

    def test_explicit_valid_process_overrides_binding_and_invalid_never_writes(self):
        process,child=self.transport(['Click'],'msedge')
        with process:
            with self.assertRaisesRegex(MCPTransportError,'MCP_INPUT_PROCESS_INVALID'):
                process.call('Type',{},expected_foreground_process='private-sentinel')
            self.assertFalse(process.uncertain)
            process.call('Click',{},expected_foreground_process='WXWork')
        self.assertEqual(self.requests(child)[0]['expected_foreground_process'],'WXWork')
        self.assertEqual(len(self.requests(child)),1)
        with self.assertRaisesRegex(MCPTransportError,'MCP_INPUT_PROCESS_INVALID'):
            process.bound_input_process='arbitrary-browser'

    def test_deepseek_generator_initialization_binds_real_transport(self):
        process,child=self.transport(['Type'])
        PreparedDeepSeekGenerator(process,self.root/'prep.json',self.root/'evidence')
        with process:process.call('Type',{'text':'anonymous'})
        self.assertEqual(self.requests(child)[0]['expected_foreground_process'],'msedge')

    def test_preparer_initialization_binds_real_transport_with_anonymous_resources(self):
        from tests.test_mcp_preparation import PreparationTests, URL
        fixture=PreparationTests()
        fixture.setUp()
        try:
            process,child=self.transport(['Click'])
            with patch('helpdesk.session_isolation.claim_deepseek_chat'):
                DeepSeekSessionPreparer(process,fixture.snapshot,URL,fixture.evidence,
                                        fixture.controls | {'display_index': 1})
            with process:process.call('Click',{})
            self.assertEqual(self.requests(child)[0]['expected_foreground_process'],'msedge')
        finally:
            fixture.doCleanups()
            fixture.tearDown()

    def test_wecom_delivery_and_ack_share_wxwork_binding(self):
        from helpdesk.collector_test_ack import CollectorTestAckWorkflow
        process,child=self.transport(['Shortcut'])
        desktop=MCPTestAnswerDesktop(process,{},self.root)
        CollectorTestAckWorkflow(object(),object(),desktop)
        with process:process.call('Shortcut',{'shortcut':'enter'})
        self.assertEqual(self.requests(child)[0]['expected_foreground_process'],'WXWork')

    def test_mock_protocol_needs_no_transport_metadata(self):
        class MockAPI:
            def call(self,tool,args):return {'tool':tool,'content':[],'is_error':False}
        mock=MockAPI()
        PreparedDeepSeekGenerator(mock,self.root/'prep.json',self.root/'evidence')
        MCPTestAnswerDesktop(mock,{},self.root)
        self.assertFalse(hasattr(mock,'bound_input_process'))

    def test_untrusted_response_details_do_not_enter_error_messages(self):
        sentinel='anonymous-private-response-sentinel'
        for ready,response,code in (({'ready':False,'detail':sentinel},None,'MCP_SESSION_NOT_READY'),
                ({'ready':True},{'tool':sentinel,'detail':sentinel,'content':[]},'MCP_RESPONSE_TOOL_MISMATCH'),
                ({'ready':True},{'tool':'Snapshot','error':sentinel,'detail':sentinel},'MCP_TOOL_REPORTED_ERROR')):
            with self.subTest(code=code):
                process,child=self.transport()
                child.stdout=io.StringIO(json.dumps(ready)+'\n'+
                    (json.dumps(response)+'\n' if response else ''))
                with self.assertRaisesRegex(MCPTransportError,'^'+code+'$') as caught:
                    with process:process.call('Snapshot',{})
                self.assertNotIn(sentinel,str(caught.exception))


if __name__=='__main__':unittest.main()
