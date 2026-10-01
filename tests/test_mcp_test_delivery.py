from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from helpdesk.delivery import BoundMessage, NotSubmitted, PreflightFailure
from helpdesk.mcp_test_delivery import MCPTestAnswerDesktop, pixel_hash, verify_frame


class TestDeliveryContractTests(unittest.TestCase):
    def test_frame_requires_pinned_window_and_visual_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frame.png'
            image = Image.new('RGB', (100,100), 'white')
            image.paste('red',(0,0,10,10)); image.paste('blue',(10,0,20,10))
            image.save(path)
            pin = dict(window_geometry_handle=[2019,975,7475866], image_size=[100,100],
                       header_box=[0,0,10,10], avatar_box=[10,0,20,10], editor_box=[20,20,90,90],
                       header_hash=pixel_hash(image,[0,0,10,10]), avatar_hash=pixel_hash(image,[10,0,20,10]))
            text='Focused Window:\n企业微信 13 Normal 2019 975 7475866\nOpened Windows:\n'
            record={'tool':'Snapshot','is_error':False,'content':[{'type':'text','text':text},{'type':'image','path':str(path)}]}
            self.assertTrue(verify_frame(record,pin)['editor_blank'])
            image.paste('black',(20,20,40,40)); image.save(path)
            self.assertFalse(verify_frame(record,pin)['editor_blank'])
            image.paste('green',(0,0,10,10)); image.save(path)
            with self.assertRaises(PreflightFailure): verify_frame(record,pin)
            record['content'][0]['text']=text.replace('企业微信','个人微信')
            with self.assertRaises(PreflightFailure): verify_frame(record,pin)

    def make_desktop(self, directory, *, wrong_clipboard=False, send_failure=False):
        body='【测试答案副本 123456】 同学，选A。'
        message=BoundMessage('oid','binding','wecom','session-key',body)
        pin=dict(platform='wecom',display_name='苇中鹤',expires_at=time.time()+60,
                 outbox_id='oid',binding_id='binding',target_key='session-key',body_hash=message.body_hash,
                 compose_point=[1,1],send_point=[2,2],receipt_point=[3,3])
        class Fake:
            def __init__(self): self.calls=[]
            def call(self,tool,args):
                self.calls.append((tool,args))
                if tool=='Click' and args['loc']==[2,2] and send_failure: raise TimeoutError('unknown click')
                if tool=='Clipboard' and args['mode']=='get':
                    return {'content':[{'type':'text','text':'Clipboard content:\n'+('wrong' if wrong_clipboard else body)}]}
                if tool=='Snapshot':
                    return {'tool':'Snapshot','is_error':False,'content':[{'type':'text','text':'(4,4) 菜单项目 "复制(C)"'}]}
                return {}
        transport=Fake()
        desktop=MCPTestAnswerDesktop(transport,pin,directory)
        desktop._frame=lambda message: {'editor_blank':True}
        return desktop,transport,message

    def test_wrong_draft_never_reaches_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop,transport,message=self.make_desktop(directory,wrong_clipboard=True)
            with self.assertRaises(NotSubmitted): desktop.send(message)
            self.assertFalse(any(t=='Click' and a['loc']==[2,2] for t,a in transport.calls))

    def test_click_timeout_is_not_misreported_as_not_submitted(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop,transport,message=self.make_desktop(directory,send_failure=True)
            with self.assertRaises(TimeoutError): desktop.send(message)
            self.assertEqual(sum(t=='Click' and a['loc']==[2,2] for t,a in transport.calls),1)

    def test_exact_test_receipt_never_claims_student_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop,transport,message=self.make_desktop(directory)
            result=desktop.send(message)
            self.assertTrue(result['confirmed'])
            self.assertFalse(result['source_student_delivered'])
            self.assertFalse(result['server_receipt'])
            desktop.pin['display_name']='another person'
            with self.assertRaises(ValueError): desktop.authorize(message)

    def test_inaccessible_copy_menu_requires_exact_reviewed_visual_row(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop, transport, message = self.make_desktop(directory)
            image = Image.new('RGB', (20, 20), 'white')
            image.paste('black', (5, 5, 8, 8))
            path = Path(directory) / 'menu.png'
            image.save(path)
            desktop.pin.update(copy_menu_verified=True, copy_menu_box=[0,0,20,20],
                               copy_menu_hash='wrong', copy_menu_point=[4,4])
            original = transport.call
            def call(tool, args):
                result = original(tool, args)
                if tool == 'Snapshot':
                    return {'tool':'Snapshot','is_error':False,'content':[
                        {'type':'text','text':'UI Tree:\ndesktop\nwindow "企业微信"'},
                        {'type':'image','path':str(path)}]}
                return result
            transport.call = call
            with patch('helpdesk.mcp_test_delivery.verify_frame', return_value={'editor_blank':True}):
                self.assertFalse(desktop.reconcile(message)['confirmed'])
                self.assertFalse(any(t=='Click' and a['loc']==[4,4] for t,a in transport.calls))
                desktop.pin['copy_menu_hash'] = pixel_hash(image, [0,0,20,20])
                self.assertTrue(desktop.reconcile(message)['confirmed'])
                self.assertEqual(sum(t=='Click' and a['loc']==[4,4] for t,a in transport.calls),1)


if __name__=='__main__': unittest.main()
