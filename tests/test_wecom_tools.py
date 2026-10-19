"""Anonymous local controls/Outbox contracts; never real desktop or sending."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from helpdesk.storage import Store,now
from helpdesk.service import Helpdesk
from helpdesk.wecom_tools import WeComTools,control_inventory
from helpdesk.mcp_test_delivery import pixel_hash


class Transport:
    def __init__(self,image):self.image=image;self.calls=[];self.clipboard='';self.draft='';self.process='WXWork'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def call(self,tool,arguments):
        self.calls.append((tool,arguments))
        if tool=='PowerShell':
            content=[{'type':'text','text':'Response: '+json.dumps({'process':self.process,'handle':1,'left':0,'top':0,'width':200,'height':100})+'\nStatus Code: 0'}]
        elif tool=='Screenshot':content=[{'type':'image','path':str(self.image)}]
        elif tool=='Clipboard':
            if arguments['mode']=='set':self.clipboard=arguments['text']
            content=[{'type':'text','text':'Clipboard content:\n'+self.clipboard}]
        else:
            if tool=='Shortcut' and arguments['shortcut']=='ctrl+v':self.draft=self.clipboard
            if tool=='Shortcut' and arguments['shortcut']=='ctrl+c':self.clipboard=self.draft
            content=[]
        return {'tool':tool,'is_error':False,'content':content,'attempt_id':'anonymous-local-fixture'}


class WeComToolTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db_path=self.root/'business.sqlite'
        self.image=self.root/'frame.png'
        image=Image.new('RGB',(200,100),'white');image.save(self.image)
        self.target='reviewed-native-test-session'
        self.pin={'platform':'wecom','display_name':'苇中鹤','target_key':self.target,'display_index':0,
            'window_geometry_handle':[200,100,1],'image_size':[200,100],
            'header_box':[20,0,180,10],'header_hash':pixel_hash(image,[20,0,180,10]),
            'avatar_box':[0,0,10,10],'avatar_hash':pixel_hash(image,[0,0,10,10]),
            'editor_box':[20,80,180,90],'compose_point':[100,85],'expires_at':time.time()+100}
        self.session={'platform':'wecom','display_name':'苇中鹤','target_key':self.target,'pin':self.pin}
        self.transport=Transport(self.image)
        with closing(Store(self.db_path)) as db:
            self.binding=Helpdesk(db).bind('wecom',self.target,'approved test contact',verified=True)
            db.execute('''INSERT INTO messages(id,binding_id,source,observed_at,raw_text,attachments,fingerprint,intent,status,created_at)
                          VALUES('message',?,'OPERATOR_TEST',?,'original test','[]','unique','IRRELEVANT','PROCESSED',?)''',(self.binding,now(),now()))
            db.execute('''INSERT INTO outbox(id,message_id,binding_id,purpose,body,idempotency_key,state,created_at,review_status,simulated)
                          VALUES('reply','message',?,'PROGRESS','收到','unique-probe','PENDING',?,'OPERATOR_AUTHORIZED',0)''',(self.binding,now()))
        self.service=WeComTools(self.db_path,session=self.session,root=self.root,
            transport_factory=lambda:self.transport,allow_input=True,
            probe=lambda root:{'status':'OK','controls':[{'control_type':'Window'},{'control_type':'Pane'}],'truncated':False,'window':{'client_version':'anonymous'}})

    def test_default_backend_never_constructs_windows_mcp(self):
        with patch('helpdesk.wecom_native.NativeWeComTransport',return_value=self.transport) as native, \
                patch('helpdesk.mcp_transport.MCPProcess',side_effect=AssertionError('Unexpected Windows-MCP')):
            service=WeComTools(self.db_path,session=self.session,root=self.root)
            self.assertEqual(service.backend,'NATIVE')
            self.assertIs(service.transport_factory(),self.transport)
            native.assert_called_once()

    def test_inventory_does_not_invent_buttons_or_automation_ids(self):
        result=self.service.call('inspect_controls')
        self.assertEqual(result['uia_node_count'],2)
        self.assertTrue(all(c['status']=='NOT_EXPOSED' and c['selector'] is None for c in result['controls']))
        self.assertEqual(self.transport.calls,[])

    def test_open_activates_native_once_but_still_verifies_target(self):
        from helpdesk.delivery import PreflightFailure
        from unittest.mock import Mock
        self.transport.activate=Mock()
        state={'status':'VERIFIED_CURRENT_CHAT','chat_id':self.target}
        with patch.object(self.service,'get_chat_state',side_effect=[PreflightFailure('FOREGROUND_PROBE_FAILED'),state]):
            self.assertEqual(self.service.call('open_chat',chat_id=self.target)['status'],'ALREADY_OPEN')
        self.transport.activate.assert_called_once()
        self.transport.activate.reset_mock()
        with patch.object(self.service,'get_chat_state',side_effect=PreflightFailure('TEST_CONTACT_VISUAL_PIN_CHANGED')):
            self.assertEqual(self.service.call('open_chat',chat_id=self.target)['reason'],'TEST_CONTACT_VISUAL_PIN_CHANGED')
        self.transport.activate.assert_not_called()

    def test_current_chat_is_not_full_chat_list_and_foreground_mismatch_stops(self):
        self.assertFalse(self.service.call('list_chats')['complete_chat_list'])
        self.assertEqual(self.service.call('open_chat',chat_id=self.target)['status'],'ALREADY_OPEN')
        self.assertEqual(self.service.call('open_chat',chat_id='other')['reason'],'CHAT_OUTSIDE_ALLOWLIST')
        self.transport.process='OtherApp'
        self.assertEqual(self.service.call('get_chat_state')['status'],'UNAVAILABLE')
        self.assertFalse(any(t=='Click' for t,a in self.transport.calls))

    def test_absent_message_recipe_never_means_no_messages(self):
        result=self.service.call('get_messages',chat_id=self.target)
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertNotIn('messages',result)
        self.assertEqual(self.transport.calls,[])

    def test_selection_variant_needs_exact_text_and_still_rejects_other_layouts(self):
        recipe={'region_box':[0,0,10,10],'region_hash':'0'*64,
            'selected_region_hash':self.pin['avatar_hash']}
        record={'content':[{'type':'image','path':str(self.image)}]}
        with self.assertRaisesRegex(ValueError,'EXACT_TEXT_HASH_REQUIRED'):
            self.service._checked_region(record,recipe)
        recipe['text_sha256']=sha256(b'original text').hexdigest()
        self.service._checked_region(record,recipe)
        recipe['selected_region_hash']='1'*64
        with self.assertRaisesRegex(ValueError,'MESSAGE_LAYOUT_CHANGED'):
            self.service._checked_region(record,recipe)

    def test_prepare_only_uses_original_outbox_and_never_sends_or_completes(self):
        result=self.service.call('prepare_reply',outbox_id='reply')
        self.assertEqual(result['status'],'PREPARED_VERIFIED')
        self.assertFalse(result['sent']);self.assertEqual(self.transport.draft,'收到')
        clicks=[a for t,a in self.transport.calls if t=='Click']
        self.assertEqual(clicks,[{'loc':[100,85]}])
        self.assertFalse(any(t=='Type' for t,a in self.transport.calls))
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT state FROM outbox WHERE id='reply'")[0],'PENDING')
            self.assertEqual(db.one('SELECT count(*) FROM performance_units')[0],0)
            events=[json.loads(r[0]) for r in db.all("SELECT details FROM audit WHERE event='WECOM_CONTROL_OPERATION'")]
            self.assertEqual([e['status'] for e in events],['PREPARING','PREPARED_VERIFIED'])
            self.assertNotIn('收到',json.dumps(events,ensure_ascii=False))

    def test_pause_unknown_and_wrong_recipient_cannot_prepare(self):
        with closing(Store(self.db_path)) as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('stop_requested','true')")
        self.assertEqual(self.service.call('prepare_reply',outbox_id='reply')['reason'],'STOPPED')
        self.assertFalse(any(t=='Click' for t,a in self.transport.calls))
        with closing(Store(self.db_path)) as db:
            db.execute("UPDATE settings SET value='false' WHERE key='stop_requested'")
            db.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id='reply'")
        self.assertEqual(self.service.call('prepare_reply',outbox_id='reply')['reason'],'PRIOR_SEND_REQUIRES_RECONCILIATION')

    def test_send_disabled_and_unsafe_tool_are_not_executable(self):
        self.assertEqual(self.service.call('send_reply',outbox_id='reply')['reason'],'SEND_DISABLED')
        self.assertEqual(self.service.call('_pin')['reason'],'OPERATION_OUTSIDE_ALLOWLIST')
        self.assertEqual(self.transport.calls,[])

    def test_expired_recipe_does_not_issue_ui_input(self):
        self.pin['expires_at']=0
        self.assertEqual(self.service.call('get_chat_state')['reason'],'SESSION_PIN_EXPIRED_OR_CHANGED')
        self.assertEqual(self.transport.calls,[])

    def test_saved_original_requires_hash_and_approved_directory(self):
        folder=self.root/'attachments';folder.mkdir();image=folder/'original.jpg';image.write_bytes(b'original fixture')
        self.session['messages']=[{'id':'image','kind':'image','attachment_name':'original.jpg',
                                  'attachment_sha256':sha256(image.read_bytes()).hexdigest()}]
        result=self.service.call('download_attachment',message_id='image')
        self.assertFalse(result['new_download_performed'])
        image.write_bytes(b'changed')
        self.assertEqual(self.service.call('download_attachment',message_id='image')['status'],'UNAVAILABLE')
