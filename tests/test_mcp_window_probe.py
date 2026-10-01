import json
import tempfile
import unittest

from helpdesk.delivery import BoundMessage, PreflightFailure
from helpdesk.mcp_test_delivery import MCPTestAnswerDesktop
from helpdesk.mcp_window_probe import parse_foreground


def result(**changes):
    data=dict(process='WXWork',handle=123,width=400,height=500,left=10,top=20)
    data.update(changes)
    return {'tool':'PowerShell','is_error':False,'content':[
        {'type':'text','text':'Response: '+json.dumps(data)+'\nStatus Code: 0'}]}


class ForegroundProbeTests(unittest.TestCase):
    def test_requires_successful_native_wecom_window_identity(self):
        self.assertEqual(parse_foreground(result())['handle'],123)
        for record in (result(process='WeChat'),result(handle='123'),result(width=0),
                       {**result(),'is_error':True}):
            with self.assertRaises(ValueError):parse_foreground(record)

    def test_focus_change_during_capture_cannot_authorize_input(self):
        with tempfile.TemporaryDirectory() as directory:
            class Transport:
                def __init__(self):self.calls=[];self.probes=0
                def call(self, tool, args):
                    self.calls.append(tool)
                    if tool=='PowerShell':
                        self.probes+=1
                        return result(left=10 if self.probes==1 else 11)
                    return {'tool':'Screenshot','is_error':False,'content':[]}
            transport=Transport()
            message=BoundMessage('oid','binding','wecom','session-key','test')
            pin=dict(platform='wecom',display_name='苇中鹤',expires_at=9999999999,
                     outbox_id='oid',binding_id='binding',target_key='session-key',body_hash=message.body_hash,
                     observation_mode='fixed_foreground_probe')
            desktop=MCPTestAnswerDesktop(transport,pin,directory)
            with self.assertRaises(PreflightFailure):desktop.preflight(message)
            self.assertEqual(transport.calls,['PowerShell','Screenshot','PowerShell'])


if __name__=='__main__':unittest.main()
