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

    def test_poll_persists_raw_observation_and_deduplicates_after_restart(self):
        message={'id':'reviewed-1','text':'anonymous question','source_sent_at':None,'observed_at':now()}
        result={'status':'OK','messages':[message],'continuous_listener':False}
        with patch.object(self.service,'get_messages',return_value=result):
            first=self.service.poll_messages(self.target)
        restarted=WeComTools(self.db_path,session=self.session,root=self.root)
        with patch.object(restarted,'get_messages',return_value=result):
            second=restarted.poll_messages(self.target)
        self.assertEqual(first['status'],'OBSERVATIONS_SAVED')
        self.assertEqual(second['status'],'NO_NEW_OBSERVATIONS')
        self.assertFalse(first['automatic_ack_eligible'])
        with closing(Store(self.db_path)) as db:
            rows=db.all("SELECT details FROM audit WHERE event='WECOM_VISIBLE_OBSERVATION'")
            self.assertEqual(len(rows),1)
            self.assertEqual(json.loads(rows[0][0])['message']['text'],message['text'])
            self.assertIsNone(json.loads(rows[0][0])['message']['source_sent_at'])
            self.assertEqual(db.one('SELECT count(*) FROM outbox')[0],1)

    def test_changed_text_is_new_observation_not_a_new_platform_question(self):
        for text in ('first wording','corrected wording','corrected wording'):
            with patch.object(self.service,'get_messages',return_value={'status':'OK','messages':[
                    {'id':'same-recipe','text':text,'source_sent_at':None,'observed_at':now()}]}):
                result=self.service.poll_messages(self.target)
            self.assertFalse(result['automatic_ack_eligible'])
        self.assertEqual(result['new_observation_count'],0)
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT count(*) FROM audit WHERE event='WECOM_VISIBLE_OBSERVATION'")[0],2)

    def test_history_requires_review_and_correct_chat_without_input(self):
        self.assertEqual(self.service.call('history_page',chat_id='other')['reason'],'CHAT_OUTSIDE_ALLOWLIST')
        self.assertEqual(self.service.call('history_page',chat_id=self.target)['reason'],'HISTORY_CONTROLS_NOT_REVIEWED')
        self.assertEqual(self.transport.calls,[])

    def test_history_stable_view_is_not_proof_history_exhausted(self):
        self.pin['display_bounds']=[0,0,200,100]
        self.session['history']={'reviewed':True,'region_box':[20,20,180,70],
            'region_hash':self.pin['header_hash'],'point':[100,50]}
        from PIL import Image
        with Image.open(self.image) as image:
            self.session['history']['region_hash']=pixel_hash(image,[20,20,180,70])
        with patch('helpdesk.wecom_tools.time.sleep'):
            result=self.service.call('history_page',chat_id=self.target)
        self.assertEqual(result['status'],'HISTORY_VIEW_UNCHANGED')
        self.assertFalse(result['history_exhausted'])
        self.assertEqual(result['messages_imported'],0)
        gestures=[a for t,a in self.transport.calls if t=='Scroll']
        self.assertEqual(gestures,[{'loc':[100,50],'direction':'up'}])
        self.transport.calls.clear()
        self.session['history']['point']=[100,85]
        self.assertEqual(self.service.call('history_page',chat_id=self.target)['reason'],'HISTORY_POINT_OUTSIDE_VIEWPORT')
        self.assertFalse(any(t=='Scroll' for t,a in self.transport.calls))

    def test_save_image_requires_review_before_any_input(self):
        self.session['messages']=[{'id':'photo','kind':'image'}]
        result=self.service.call('save_open_image',message_id='photo')
        self.assertEqual(result['reason'],'IMAGE_SAVE_CONTROLS_NOT_REVIEWED')
        self.assertEqual(self.transport.calls,[])

    def test_save_image_uses_internal_generated_path_and_checks_original_hash(self):
        from unittest.mock import Mock
        from PIL import Image
        database=self.root/'data/private/save-test/probe.sqlite'
        database.parent.mkdir(parents=True)
        (database.parent/'attachments').mkdir()
        Store(database).close()
        self.service.database=database
        region={'region_box':[0,0,10,10],'region_hash':self.pin['avatar_hash']}
        self.session['messages']=[{'id':'photo','kind':'image','save_preview':{
            'reviewed':True,'window_geometry_handle':[200,100,1],
            'source_region':region,'save_button':{**region,'point':[5,5]}}}]
        self.transport.foreground=Mock(return_value={'process':'WXWork','width':200,'height':100,'handle':1})
        def save(args,**kwargs):
            target=Path(args[-1])
            self.assertEqual(target.parent,database.parent/'attachments')
            self.assertFalse(target.exists())
            Image.new('RGB',(20,20),'white').save(target)
            return Mock(returncode=0)
        with patch('helpdesk.wecom_tools.subprocess.run',side_effect=save):
            result=self.service.call('save_open_image',message_id='photo')
        self.assertEqual(result['status'],'ORIGINAL_SAVED')
        self.assertTrue(Path(result['path']).is_file())
        self.transport.calls.clear()
        self.session['messages'][0]['attachment_sha256']='0'*64
        with patch('helpdesk.wecom_tools.subprocess.run',side_effect=save):
            result=self.service.call('save_open_image',message_id='photo')
        self.assertEqual(result['reason'],'SAVED_ORIGINAL_HASH_MISMATCH')
        with closing(Store(database)) as db:
            self.assertEqual(db.one('SELECT count(*) FROM performance_units')[0],0)
            self.assertEqual(db.one('SELECT count(*) FROM outbox')[0],0)

    def _review_search(self):
        self.pin['display_bounds']=[0,0,200,100]
        self.session['search']={'reviewed':True,
            'editor':{'region_box':[0,0,10,10],'region_hash':self.pin['avatar_hash'],
                      'input_box':[0,0,20,20],'point':[5,5]},
            'result':{'region_box':[20,20,30,30],'region_hash':self.pin['avatar_hash'],'point':[25,25]}}

    def test_search_uses_fixed_contact_and_verifies_final_chat_without_send(self):
        self._review_search()
        with patch('helpdesk.wecom_tools.time.sleep'):
            result=self.service.call('search_chat',chat_id=self.target)
        self.assertEqual(result['status'],'OPENED_VERIFIED')
        self.assertFalse(result['sent'])
        self.assertEqual([a['text'] for t,a in self.transport.calls if t=='Clipboard' and a['mode']=='set'],['苇中鹤'])
        self.assertEqual([a['loc'] for t,a in self.transport.calls if t=='Click'],[[5,5],[25,25]])
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT state FROM outbox WHERE id='reply'")[0],'PENDING')

    def test_search_can_activate_reviewed_main_window_before_query(self):
        from unittest.mock import Mock
        self._review_search();self.transport.process='OtherApp'
        self.transport.activate=Mock(side_effect=lambda:setattr(self.transport,'process','WXWork'))
        with patch('helpdesk.wecom_tools.time.sleep'):
            result=self.service.call('search_chat',chat_id=self.target)
        self.assertEqual(result['status'],'OPENED_VERIFIED')
        self.transport.activate.assert_called_once()

    def test_search_wrong_result_stops_before_selection_and_no_fallback_click(self):
        self._review_search();self.session['search']['result']['region_hash']='0'*64
        with patch('helpdesk.wecom_tools.time.sleep'):
            result=self.service.call('search_chat',chat_id=self.target)
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertEqual([a['loc'] for t,a in self.transport.calls if t=='Click'],[[5,5]])

    def test_search_rejects_unreviewed_target_and_control_point(self):
        self.assertEqual(self.service.call('search_chat',chat_id=self.target)['reason'],'SEARCH_CONTROLS_NOT_REVIEWED')
        self._review_search()
        self.assertEqual(self.service.call('search_chat',chat_id='other')['reason'],'CHAT_OUTSIDE_ALLOWLIST')
        self.session['search']['editor']['point']=[100,85]
        self.assertEqual(self.service.call('search_chat',chat_id=self.target)['reason'],'SEARCH_POINT_OUTSIDE_CONTROL')
        self.assertEqual(self.transport.calls,[])

    def test_search_only_accepts_explicitly_reviewed_editor_variants(self):
        self._review_search();self.session['search']['editor']['region_hash']='0'*64
        with patch('helpdesk.wecom_tools.time.sleep'):
            self.assertEqual(self.service.call('search_chat',chat_id=self.target)['reason'],'SEARCH_CONTROL_LAYOUT_CHANGED')
            self.session['search']['closed_region_hash']=self.pin['avatar_hash']
            self.assertEqual(self.service.call('search_chat',chat_id=self.target)['status'],'OPENED_VERIFIED')

    def test_scan_requires_review_and_empty_candidates_do_not_mean_empty_chat(self):
        self.assertEqual(self.service.call('scan_messages',chat_id=self.target)['reason'],'INCOMING_DISCOVERY_NOT_REVIEWED')
        self.pin['display_bounds']=[0,0,200,100]
        self.session['incoming_discovery']={'reviewed':True,'viewport':[20,20,180,70],
            'incoming_left':25,'bubble_color':[228,231,235],'copy_menu_search_box':[20,20,180,70]}
        result=self.service.call('scan_messages',chat_id=self.target)
        self.assertEqual(result['status'],'NO_VISIBLE_TEXT_CANDIDATES')
        self.assertIsNone(result['chat_empty'])
        self.assertFalse(any(t in ('Click','Clipboard','Shortcut') for t,a in self.transport.calls))

    def test_scan_copies_new_candidate_without_mutating_original_recipe(self):
        self.pin['display_bounds']=[0,0,200,100]
        self.session['incoming_discovery']={'reviewed':True,'copy_menu_search_box':[20,20,180,70]}
        def read(reader,chat_id):
            recipe=reader.session['messages'][0]
            self.assertTrue(recipe['copy_menu']['ocr_only'])
            self.assertNotIn('text_sha256',recipe)
            return {'status':'OK','messages':[{'id':recipe['id'],'text':'new raw question',
                    'source_sent_at':None,'observed_at':now()}],'continuous_listener':False}
        with patch('helpdesk.wecom_visible.incoming_bubbles',return_value=[[20,20,80,50]]), \
                patch.object(WeComTools,'get_messages',autospec=True,side_effect=read):
            result=self.service.scan_messages(self.target)
        self.assertEqual(result['messages'][0]['text'],'new raw question')
        self.assertIsNone(result['messages'][0]['source_sent_at'])
        self.assertFalse(result['automatic_ack_eligible'])
        self.assertNotIn('messages',self.session)
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one('SELECT count(*) FROM outbox')[0],1)

    def test_hover_time_stops_if_same_incoming_bubble_cannot_be_located(self):
        from unittest.mock import Mock
        self.pin['display_bounds']=[0,0,200,100]
        record={'attempt_id':'anonymous','content':[{'type':'image','path':str(self.image)}]}
        recipe={'region_box':[20,20,80,50],'time_hover':{'blank':{
            'region_box':[0,0,10,10],'region_hash':self.pin['avatar_hash'],'point':[5,5]}}}
        desktop=Mock()
        with patch.object(self.service,'_frame',return_value=(record,{})), \
                patch('helpdesk.wecom_visible.incoming_bubbles',return_value=[]), \
                patch('helpdesk.wecom_control_ocr.read_time_label') as read, \
                patch('helpdesk.wecom_tools.time.sleep'):
            with self.assertRaisesRegex(ValueError,'TIME_MESSAGE_GEOMETRY_CHANGED'):
                self.service._hover_time(desktop,self.pin,record,recipe)
        read.assert_not_called()
        self.assertFalse(any(call.args[0]=='Clipboard' for call in desktop._call.call_args_list))

    def test_hover_time_unavailable_keeps_original_time_missing(self):
        from unittest.mock import Mock
        self.pin['display_bounds']=[0,0,200,100]
        record={'attempt_id':'anonymous','content':[{'type':'image','path':str(self.image)}]}
        recipe={'region_box':[20,20,80,50],'time_hover':{'blank':{
            'region_box':[0,0,10,10],'region_hash':self.pin['avatar_hash'],'point':[5,5]}}}
        with patch.object(self.service,'_frame',return_value=(record,{})), \
                patch('helpdesk.wecom_visible.incoming_bubbles',return_value=[[20,20,80,50]]), \
                patch('helpdesk.wecom_control_ocr.read_time_label',side_effect=ValueError('TIME_LABEL_NOT_UNIQUE_OR_UNAVAILABLE')), \
                patch('helpdesk.wecom_tools.time.sleep'):
            _,_,evidence=self.service._hover_time(Mock(),self.pin,record,recipe)
        self.assertIsNone(evidence['source_sent_at'])
        self.assertEqual(evidence['time_status'],'REQUIRES_ORIGINAL_TIME_EVIDENCE')
        self.assertFalse(evidence['formal_statistics_eligible'])

    def test_new_time_evidence_enriches_existing_observation_without_recount(self):
        message={'id':'same-record','text':'raw original','source_sent_at':None}
        with patch.object(self.service,'get_messages',return_value={'status':'OK','messages':[message]}):
            self.assertEqual(self.service.poll_messages(self.target)['new_observation_count'],1)
            message['source_time_evidence']={'normalized_original_time_label':'10/1923:12:32',
                'time_status':'REQUIRES_YEAR_EVIDENCE','components':{'year':None}}
            second=self.service.poll_messages(self.target);third=self.service.poll_messages(self.target)
        self.assertEqual(second['new_observation_count'],0)
        self.assertEqual(second['new_time_evidence_count'],1)
        self.assertEqual(third['new_time_evidence_count'],0)
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT count(*) FROM audit WHERE event='WECOM_VISIBLE_OBSERVATION'")[0],1)
            self.assertEqual(db.one('SELECT count(*) FROM outbox')[0],1)

    def test_failed_poll_never_consumes_observations(self):
        with patch.object(self.service,'get_messages',side_effect=ValueError('WINDOW_CHANGED')):
            result=self.service.call('poll_messages',chat_id=self.target)
        self.assertEqual(result['status'],'UNAVAILABLE')
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT count(*) FROM audit WHERE event='WECOM_VISIBLE_OBSERVATION'")[0],0)

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

    def test_historical_receipt_cannot_promote_pending_or_unknown_outbox(self):
        for state in ('PENDING','SEND_UNKNOWN'):
            with closing(Store(self.db_path)) as db:
                db.execute('UPDATE outbox SET state=? WHERE id=?',(state,'reply'))
            self.assertEqual(self.service.call('verify_reply',outbox_id='reply')['status'],'UNAVAILABLE')
            self.assertEqual(self.transport.calls,[])
            with closing(Store(self.db_path)) as db:
                self.assertEqual(db.one('SELECT state FROM outbox WHERE id=?',('reply',))[0],state)

    def test_historical_readback_keeps_original_completion_and_never_sends(self):
        with closing(Store(self.db_path)) as db:
            db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED' WHERE id='reply'")
        self.pin.update(receipt_point=[100,50],copy_menu_verified=True,copy_menu_point=[110,55],
                        copy_menu_box=[100,40,120,60],copy_menu_hash='reviewed-fixture')
        with patch('helpdesk.wecom_tools.PinnedDesktop.reconcile',return_value={'confirmed':True}) as read:
            result=self.service.call('verify_reply',outbox_id='reply')
        self.assertEqual(result['status'],'HISTORICAL_TEXT_VERIFIED')
        self.assertFalse(result['new_delivery']);self.assertFalse(result['outbox_changed'])
        read.assert_called_once()
        self.assertEqual(self.transport.calls,[])
        with closing(Store(self.db_path)) as db:
            self.assertEqual(db.one("SELECT state FROM outbox WHERE id='reply'")[0],'SENT_UI_CONFIRMED')
            self.assertEqual(db.one('SELECT count(*) FROM performance_units')[0],0)

    def test_native_send_requires_reviewed_controls_before_any_draft_input(self):
        from helpdesk.wecom_tools import PinnedDesktop
        from helpdesk.delivery import BoundMessage,PreflightFailure
        self.transport.native_backend=True
        desktop=PinnedDesktop(self.transport,self.pin,self.root)
        message=BoundMessage('reply',self.binding,'wecom',self.target,'收到')
        with patch.object(desktop,'_frame',return_value={'editor_blank':True}):
            with self.assertRaisesRegex(PreflightFailure,'NATIVE_SEND_CONTROLS_NOT_REVIEWED'):
                desktop.preflight(message)
        self.assertEqual(self.transport.calls,[])

    def test_native_receipt_needs_change_since_send_not_an_old_identical_bubble(self):
        from helpdesk.wecom_tools import PinnedDesktop
        self.transport.native_backend=True
        desktop=PinnedDesktop(self.transport,self.pin,self.root)
        with patch.object(desktop,'_frame'),patch.object(desktop,'_receipt_hash',return_value='same'):
            self.assertFalse(desktop.reconcile(None)['confirmed'])  # Restart lost baseline.
            desktop._send_receipt_baseline='same'
            self.assertFalse(desktop.reconcile(None)['confirmed'])
            desktop._send_receipt_baseline='previous'
            with patch('helpdesk.wecom_tools.MCPTestAnswerDesktop.reconcile',return_value={'confirmed':True}) as read:
                self.assertTrue(desktop.reconcile(None)['confirmed'])
                read.assert_called_once()
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

    def test_copy_ocr_fallback_is_opt_in_and_cannot_invoke_input(self):
        record={'attempt_id':'a'*32,'backend':'WECOM_NATIVE','content':[{'type':'image','path':str(self.image)}]}
        recipe={'region_box':[0,0,10,10],'region_hash':'0'*64,'point':[5,5]}
        with patch('helpdesk.wecom_control_ocr.locate_copy',return_value={'loc':[12,13]}) as locate:
            with self.assertRaisesRegex(ValueError,'MESSAGE_LAYOUT_CHANGED'):
                self.service._copy_point(record,recipe,self.pin)
            locate.assert_not_called()
            recipe['ocr_search_box']=[0,0,20,20]
            self.assertEqual(self.service._copy_point(record,recipe,self.pin),[12,13])
            self.assertEqual(locate.call_args.kwargs['display_index'],0)
        self.assertEqual(self.transport.calls,[])

    def test_interrupted_read_dismisses_only_one_confirmed_copy_menu(self):
        from unittest.mock import Mock
        record={'content':[{'type':'image','path':str(self.image)}]}
        recipe={'copy_menu':{'ocr_search_box':[0,0,20,20]}}
        desktop=Mock()
        with patch.object(self.service,'_frame',return_value=(record,{})), \
                patch.object(self.service,'_checked_region',side_effect=[ValueError('MESSAGE_LAYOUT_CHANGED_RECAPTURE_REQUIRED'),None]), \
                patch.object(self.service,'_copy_point',return_value=[5,5]) as confirm:
            self.assertIs(self.service._message_frame(desktop,self.pin,recipe),record)
        confirm.assert_called_once()
        desktop._call.assert_called_once_with('Shortcut',shortcut='escape')

    def test_changed_message_or_unconfirmed_menu_never_retries_input(self):
        from unittest.mock import Mock
        desktop=Mock();record={};recipe={'copy_menu':{'ocr_search_box':[0,0,20,20]}}
        with patch.object(self.service,'_frame',return_value=(record,{})), \
                patch.object(self.service,'_checked_region',side_effect=ValueError('MESSAGE_LAYOUT_CHANGED_RECAPTURE_REQUIRED')), \
                patch.object(self.service,'_copy_point',side_effect=ValueError('COPY_OCR_NOT_UNIQUE_OR_UNAVAILABLE')):
            with self.assertRaises(ValueError):self.service._message_frame(desktop,self.pin,recipe)
        desktop._call.assert_not_called()
        with patch.object(self.service,'_frame',return_value=(record,{})), \
                patch.object(self.service,'_checked_region',side_effect=ValueError('MESSAGE_LAYOUT_CHANGED_RECAPTURE_REQUIRED')), \
                patch.object(self.service,'_copy_point',return_value=[5,5]):
            with self.assertRaises(ValueError):self.service._message_frame(desktop,self.pin,recipe)
        desktop._call.assert_called_once_with('Shortcut',shortcut='escape')

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
