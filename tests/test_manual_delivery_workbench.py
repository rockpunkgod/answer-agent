"""Anonymous local API/headless browser; never a real account or delivery."""
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread
import unittest

from playwright.sync_api import sync_playwright
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.service import Helpdesk
from helpdesk.storage import Store
from tests import test_manual_delivery_registration as registration_fixture


ROOT=Path(__file__).resolve().parents[1]


class ManualDeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fx=registration_fixture.ManualDeliveryRegistrationTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.server=DemoHTTPServer(('127.0.0.1',0),Path(self.fx.db.path),processing_mode='ACK_ONLY')
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.token=''
        self.token=self.request('GET','/api/state')[1]['csrf_token']
        self.payload=dict(self.fx.args,original_outbox_id=self.fx.origin['id'],part_number=1,total_parts=1,attachments=[])

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def request(self,method,path,payload=None,headers=None):
        with closing(HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)) as connection:
            fields={'Origin':self.origin,'X-CSRF-Token':self.token,'Content-Type':'application/json'} if payload is not None else {}
            fields.update(headers or {})
            connection.request(method,path,body=json.dumps(payload).encode() if payload is not None else None,headers=fields)
            response=connection.getresponse();raw=response.read()
            return response.status,json.loads(raw) if 'application/json' in response.getheader('Content-Type','') else raw.decode()

    def test_registration_is_available_without_enabling_answer_sending(self):
        status,data=self.request('GET','/api/manual-deliveries')
        self.assertEqual(status,200)
        self.assertFalse(data['sends_messages'])
        self.assertEqual(data['tasks'][0]['outbox_id'],self.fx.origin['id'])
        response=self.request('POST','/api/manual-deliveries',self.payload)
        self.assertEqual(response[0],200)
        self.assertEqual(response[1]['result']['counting_status'],'CONFIRMED')
        self.assertEqual(self.request('POST','/api/action',{'action':'dispatch_answer'})[0],403)

    def test_legacy_confirmed_ack_without_time_does_not_break_the_workbench(self):
        status,data=self.request('GET','/api/state')
        self.assertEqual(status,200)
        item=next(x for x in data['dashboard']['health']['sla'] if x['message_id']==self.fx.origin['message_id'])
        self.assertIsNone(item['ack_seconds'])
        self.assertIsNone(self.fx.db.one("SELECT sent_at FROM outbox WHERE message_id=? AND purpose='ACK'",(self.fx.origin['message_id'],))[0])

    def test_local_origin_csrf_and_host_are_required_without_side_effects(self):
        for fields in ({'Origin':'https://other.invalid'},{'X-CSRF-Token':'wrong'},{'Host':'other.invalid'}):
            self.assertEqual(self.request('POST','/api/manual-deliveries',self.payload,headers=fields)[0],403)
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)

    def test_no_arbitrary_target_commands_or_test_copy_can_be_registered(self):
        for payload in (self.payload|{'target_group':'another-group'},self.payload|{'command':'anything'},
                        self.payload|{'original_outbox_id':'not-real'},self.payload|{'context_revision':True}):
            self.assertEqual(self.request('POST','/api/manual-deliveries',payload)[0],400)
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)

    def test_partial_and_duplicate_entries_keep_one_existing_counting_unit(self):
        first=self.payload|{'total_parts':2}
        self.assertEqual(self.request('POST','/api/manual-deliveries',first)[1]['result']['state'],'PARTIAL_DELIVERY')
        data=self.request('GET','/api/manual-deliveries')[1]
        self.assertEqual(data['tasks'][0]['total_parts'],2)
        self.assertEqual(len(data['tasks'][0]['parts']),1)
        final=first|{'part_number':2,'content':'Actual second synthetic part.','delivered_at':'2026-10-01T10:01:00+08:00'}
        self.assertEqual(self.request('POST','/api/manual-deliveries',final)[1]['result']['counting_status'],'CONFIRMED')
        self.assertTrue(self.request('POST','/api/manual-deliveries',final)[1]['result']['replayed'])
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM performance_units')[0],1)
        self.assertEqual(self.fx.unit()['confirmed_quantity'],1)

    def test_attachments_cannot_read_outside_the_fixed_local_directory(self):
        status,data=self.request('POST','/api/manual-deliveries',self.payload|{'attachments':['C:/Windows/win.ini']})
        self.assertEqual(status,400)
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)

    def test_direct_turn_endpoint_binds_the_existing_task_without_a_free_recipient(self):
        payload=self.payload.copy();payload['turn_id']=self.fx.origin['turn_id'];del payload['original_outbox_id']
        status,data=self.request('POST','/api/manual-deliveries',payload)
        self.assertEqual(status,200)
        self.assertEqual(data['result']['counting_status'],'CONFIRMED')
        self.assertEqual(self.request('POST','/api/manual-deliveries',payload|{'student_key':'unbound-person'})[0],400)


class ManualDeliveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright=sync_playwright().start();cls.browser=cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()

    def test_original_task_card_shows_actual_delivery_and_keeps_later_correction_visible(self):
        from playwright.sync_api import expect
        fixture=ManualDeliveryHTTPTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.assertEqual(fixture.request('POST','/api/manual-deliveries',fixture.payload)[0],200)
        with self.browser.new_context() as context:
            page=context.new_page();page.set_default_timeout(5000)
            errors=[];page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto(fixture.origin)
            draft_id=fixture.fx.fixture.fx.draft['id']
            card=page.locator('#operator-tasks details[data-draft-id="'+draft_id+'"]')
            expect(card.locator('summary')).to_contain_text('已人工核验交付')
            with page.expect_response(fixture.origin+'/api/state'):
                page.locator('#refresh').click()
            expect(page.locator('#manual-delivery-form button[type="submit"]')).to_be_disabled()
            self.assertEqual(card.locator('button').count(),0)
            self.assertNotIn('草稿尚未交付',card.inner_text())
            question=fixture.fx.db.one('SELECT * FROM questions WHERE id=?',(fixture.fx.fixture.fx.outcome.question_id,))
            Helpdesk(fixture.fx.db).correct_material(question['material_id'],fixture.fx.origin['message_id'],
                'Synthetic corrected material','Synthetic corrected material')
            page.reload()
            expect(card.locator('summary')).to_contain_text('历史已人工交付，题目已更正')
            self.assertEqual(card.locator('button').count(),0)
            self.assertEqual(errors,[])

    def test_only_explicit_attestation_posts_current_task_and_all_external_content_is_text(self):
        from playwright.sync_api import expect
        with self.browser.new_context() as context:
            page=context.new_page();page.set_default_timeout(5000)
            origin='http://127.0.0.1:49251';posts=[];pending_csrf=[]
            hostile='<img src=x onerror="window.manualInjection=true">'
            task={'outbox_id':'synthetic-original','question_version':'synthetic-version','context_revision':2,
                  'label':'匿名任务','draft':hostile,'stale':False,'total_parts':None,'parts':[]}
            def route(call):
                path=call.request.url.removeprefix(origin)
                if path=='/':
                    call.fulfill(status=200,content_type='text/html',body=(ROOT/'helpdesk/static/index.html').read_text(encoding='utf-8'))
                elif path=='/manual-delivery.js':
                    call.fulfill(status=200,content_type='text/javascript',body=(ROOT/'helpdesk/static/manual-delivery.js').read_text(encoding='utf-8'))
                elif path=='/api/manual-deliveries':
                    if call.request.method=='POST':
                        value=json.loads(call.request.post_data);posts.append(value)
                        task['total_parts']=1
                        task['completed']=True
                        task['parts']=[{'part_number':1,'total_parts':1,'reviewer':value['reviewer'],
                                       'state':'SENT_UI_CONFIRMED','check_status':'SENT_UI_CONFIRMED',
                                       'confirmed_at':value['delivered_at'],'actual_content':value['content'],
                                       'verification_evidence':value['verification_evidence'],'attachments':[]}]
                        call.fulfill(status=200,content_type='application/json',body=json.dumps({'result':{'state':'SENT_UI_CONFIRMED','counting_status':'CONFIRMED'}}))
                    else:
                        call.fulfill(status=200,content_type='application/json',body=json.dumps({'tasks':[task],'attachment_files':[]}))
                elif path=='/api/state':
                    pending_csrf.append(call)
                else:
                    call.fulfill(status=404,body='')
            page.route('**/*',route);page.goto(origin+'/')
            page.locator('#manual-delivery-form').wait_for(state='visible')
            self.assertEqual(posts,[])
            self.assertEqual(page.locator('#manual-delivery-time').input_value(),'')
            self.assertEqual(page.locator('#manual-delivery-content').input_value(),'')
            self.assertEqual(page.locator('#manual-delivery-current img').count(),0)
            self.assertIsNone(page.evaluate('window.manualInjection'))

            page.locator('#manual-delivery-content').fill(hostile)
            page.locator('#manual-delivery-time').fill('2026-10-01T10:00:00+08:00')
            page.locator('#manual-delivery-reviewer').fill('匿名核验人')
            page.locator('#manual-delivery-evidence').fill('匿名老师实际消息核对依据')
            page.get_by_role('button',name='保存人工核验记录',exact=True).click()
            self.assertEqual(posts,[])
            page.locator('#manual-delivery-attested').check()
            page.get_by_role('button',name='保存人工核验记录',exact=True).click()
            expect(page.locator('#manual-delivery-task')).to_be_disabled()
            # These edits happen while the token request is deliberately pending.
            # The submitted task must retain the contents explicitly attested at click.
            page.locator('#manual-delivery-content').fill('Edited after submission, not attested.')
            page.locator('#manual-delivery-time').fill('2026-10-01T11:00:00+08:00')
            self.assertEqual(len(pending_csrf),1)
            pending_csrf[0].fulfill(status=200,content_type='application/json',body='{"csrf_token":"synthetic-csrf"}')
            page.get_by_text('人工核验交付已回流，沿用原计量单元。',exact=True).wait_for()
            self.assertEqual(len(posts),1)
            self.assertEqual(posts[0]['original_outbox_id'],'synthetic-original')
            self.assertEqual(posts[0]['question_version'],'synthetic-version')
            self.assertEqual(posts[0]['content'],hostile)
            self.assertEqual(posts[0]['delivered_at'],'2026-10-01T10:00:00+08:00')
            self.assertNotIn('target_group',posts[0])
            self.assertEqual(page.locator('#manual-delivery-current img').count(),0)
            self.assertIsNone(page.evaluate('window.manualInjection'))

    def test_saved_reply_is_bound_to_selected_task_and_stale_fetch_does_not_mix_sources(self):
        with self.browser.new_context() as context:
            page=context.new_page();page.set_default_timeout(5000)
            origin='http://127.0.0.1:49251';posts=[];delayed=[]
            hostile='<img src=x onerror="window.replyInjection=true">'
            tasks=[{'outbox_id':identity,'question_version':'version-'+identity,'context_revision':2,
                    'label':'匿名任务'+identity,'draft':'Generated draft is not delivered','stale':False,
                    'total_parts':None,'parts':[],'completed':False} for identity in ('first','second')]
            def source(identity):
                return {'status':'READY','message':'仅供人工核验的匿名原回复','replies':[
                    {'collector_message_id':'teacher-'+identity,'source_evidence_sha256':'a'*64,
                     'sender_display_name':'匿名老师','content':hostile if identity=='second' else 'Old first task reply',
                     'delivered_at':'2026-10-01T02:00:00+00:00','association':'MANUAL_LINK_REQUIRED'}]}
            def route(call):
                path=call.request.url.removeprefix(origin)
                if path=='/':
                    call.fulfill(status=200,content_type='text/html',body=(ROOT/'helpdesk/static/index.html').read_text(encoding='utf-8'))
                elif path=='/manual-delivery.js':
                    call.fulfill(status=200,content_type='text/javascript',body=(ROOT/'helpdesk/static/manual-delivery.js').read_text(encoding='utf-8'))
                elif path=='/api/manual-deliveries':
                    if call.request.method=='POST':
                        posts.append(json.loads(call.request.post_data));tasks[1]['completed']=True
                        call.fulfill(status=200,content_type='application/json',body=json.dumps({'result':{'state':'SENT_UI_CONFIRMED','counting_status':'CONFIRMED'}}))
                    else:
                        call.fulfill(status=200,content_type='application/json',body=json.dumps({'tasks':tasks,'attachment_files':[]}))
                elif path=='/api/manual-delivery-replies?original_outbox_id=first':
                    delayed.append(call)
                elif path=='/api/manual-delivery-replies?original_outbox_id=second':
                    call.fulfill(status=200,content_type='application/json',body=json.dumps(source('second')))
                elif path=='/api/state':
                    call.fulfill(status=200,content_type='application/json',body='{"csrf_token":"synthetic-token"}')
                else:
                    call.fulfill(status=404,body='')
            page.route('**/*',route);page.goto(origin+'/')
            page.locator('#manual-delivery-form').wait_for(state='visible')
            page.locator('#manual-delivery-task').select_option('second')
            page.locator('#manual-delivery-source option[value="teacher-second"]').wait_for(state='attached')
            self.assertEqual(len(delayed),1)
            delayed[0].fulfill(status=200,content_type='application/json',body=json.dumps(source('first')))
            page.wait_for_load_state('networkidle')
            self.assertEqual(page.locator('#manual-delivery-source option[value="teacher-first"]').count(),0)
            self.assertEqual(posts,[])
            page.locator('#manual-delivery-source').select_option('teacher-second')
            self.assertEqual(page.locator('#manual-delivery-content').input_value(),hostile)
            self.assertTrue(page.locator('#manual-delivery-content').evaluate('(element)=>element.readOnly'))
            page.locator('#manual-delivery-attested').check()
            page.locator('#manual-delivery-source').select_option('')
            self.assertFalse(page.locator('#manual-delivery-attested').is_checked())
            self.assertFalse(page.locator('#manual-delivery-content').evaluate('(element)=>element.readOnly'))
            page.locator('#manual-delivery-source').select_option('teacher-second')
            page.locator('#manual-delivery-reviewer').fill('匿名核验人')
            page.locator('#manual-delivery-evidence').fill('确认实际原回复对应第二项任务')
            page.locator('#manual-delivery-attested').check()
            page.get_by_role('button',name='保存人工核验记录',exact=True).click()
            page.get_by_text('人工核验交付已回流，沿用原计量单元。',exact=True).wait_for()
            self.assertEqual(posts,[{'original_outbox_id':'second','question_version':'version-second','context_revision':2,
                'reviewer':'匿名核验人','verification_evidence':'确认实际原回复对应第二项任务','part_number':1,'total_parts':1,
                'collector_message_id':'teacher-second','source_evidence_sha256':'a'*64,'source_verified':True}])
            self.assertEqual(page.locator('#manual-delivery-panel img').count(),0)
            self.assertIsNone(page.evaluate('window.replyInjection'))


if __name__=='__main__':
    unittest.main()
