"""Anonymous identity facts; no live account, Qt gate or desktop access."""
from datetime import datetime,timezone
from contextlib import contextmanager,ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch

from helpdesk.personal_wechat_accessibility import verify_test_chat_alignment
from helpdesk.test_routing import PersonalWeChatTestRecipient
from helpdesk.personal_wechat_test_identity import personal_test_identity


class PersonalTestIdentityTests(unittest.TestCase):
    def setUp(self):
        self.recipient=PersonalWeChatTestRecipient('account','room@chatroom','synthetic review',datetime.now(timezone.utc))
        self.inputs=dict(live_account_id='account',room_matches=[{'username':'room@chatroom','nick_name':'测试'}],
            ui_rows=[{'kind':'image','text':''},{'kind':'text','text':'Please explain printed question 7.'}],
            db_rows=[{'kind':'text','text':'Earlier text','message_id':'earlier'},
                     {'kind':'image','text':'','message_id':'image'},
                     {'kind':'text','text':'Please explain printed question 7.','message_id':'question'}],
            source_message_ids=['image','question'])

    def test_actual_account_unique_room_and_exact_original_sequence_are_all_required(self):
        result=verify_test_chat_alignment(self.recipient,**self.inputs)
        self.assertEqual(result['matched_message_ids'],['image','question'])
        self.assertEqual(result['target_key'],self.recipient.stable_key)
        self.assertFalse(result['sending_authorized'])
        self.assertFalse(result['formal_performance_eligible'])
        for change in ({'live_account_id':'other'}, {'room_matches':[]},
                       {'room_matches':[{'username':'other@chatroom','nick_name':'测试'}]},
                       {'room_matches':[self.inputs['room_matches'][0],{'username':'other@chatroom','nick_name':'测试'}]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                verify_test_chat_alignment(self.recipient,**dict(self.inputs,**change))

    def test_repeated_or_truncated_messages_are_not_identity(self):
        repeated=[*self.inputs['db_rows'],
                  {'kind':'image','text':'','message_id':'image2'},
                  {'kind':'text','text':'Please explain printed question 7.','message_id':'question2'}]
        for change in ({'db_rows':repeated},
                       {'ui_rows':[self.inputs['ui_rows'][0],{'kind':'text','text':'Please explain'}]},
                       {'source_message_ids':['another-question']},
                       {'source_message_ids':['question','question']},
                       {'ui_rows':[self.inputs['ui_rows'][0]]},
                       {'ui_rows':[]}, {'source_message_ids':[]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                verify_test_chat_alignment(self.recipient,**dict(self.inputs,**change))

    def test_invalid_facts_cannot_be_used_as_identity(self):
        for change in ({'room_matches':[None]}, {'ui_rows':[None]}, {'db_rows':[None]},
                       {'source_message_ids':[{}]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                verify_test_chat_alignment(self.recipient,**dict(self.inputs,**change))

    def test_changed_order_or_duplicate_platform_locator_stops(self):
        for rows in (list(reversed(self.inputs['ui_rows'])),
                     [{'kind':'image','text':''},{'kind':'text','text':'Please explain printed question 8.'}]):
            with self.assertRaises(ValueError):
                verify_test_chat_alignment(self.recipient,**dict(self.inputs,ui_rows=rows))
        rows=[*self.inputs['db_rows'],self.inputs['db_rows'][0]]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_INVALID'):
            verify_test_chat_alignment(self.recipient,**dict(self.inputs,db_rows=rows))


class LiveGuardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        base=Path(__file__).resolve().parents[1]/'data/private'
        base.mkdir(parents=True,exist_ok=True)
        self.private=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=base)))
        self.config=dict(prototype_only=True,groups_reviewed=True,private_root=str(self.private),
            rooms={'room@chatroom':'测试','r1@chatroom':'English 1','r2@chatroom':'English 2'},
            test_room_ids=['room@chatroom'],upstream='unused-reviewed-upstream',process_id=7,
            process_create_time=123,window_handle=8,account_id='account',account_directory='account-dir',db_root='unused')
        self.recipient=PersonalWeChatTestRecipient('account','room@chatroom','synthetic review',datetime.now(timezone.utc))
        self.process=SimpleNamespace(pid=7,is_running=Mock(return_value=True),name=Mock(return_value='Weixin.exe'),
                                     create_time=Mock(return_value=123))
        self.module=SimpleNamespace(extract_master_key_from_cfg=Mock(return_value=(None,None,'account')))
        self.auto=SimpleNamespace(InitializeUIAutomationInCurrentThread=Mock(),
                                  UninitializeUIAutomationInCurrentThread=Mock(),ControlFromHandle=Mock())
        def owner(handle,pointer):pointer._obj.value=7;return 1
        user=SimpleNamespace(IsWindow=Mock(return_value=True),GetWindowThreadProcessId=Mock(side_effect=owner))
        self.stack.enter_context(patch.dict('sys.modules',{'psutil':SimpleNamespace(Process=lambda pid:self.process),
                                                           'uiautomation':self.auto}))
        self.stack.enter_context(patch('ctypes.WinDLL',return_value=user,create=True))
        prefix='helpdesk.personal_wechat_test_identity.'
        self.stack.enter_context(patch(prefix+'load_read_modules',return_value=(self.module,None)))
        self.connection=SimpleNamespace(execute=Mock(return_value=[{'username':'room@chatroom','nick_name':'测试'}]),close=Mock())
        db=SimpleNamespace(wxid='account',_db_files=[('contact','contact.db',None)],_open=lambda rel:self.connection)
        self.stack.enter_context(patch(prefix+'strict_database',return_value=db))
        reader=SimpleNamespace(snapshot=lambda *args:{'rows':[
            {'sort_seq':1,'shard':'message_0.db','local_id':1,'local_type':3},
            {'sort_seq':2,'shard':'message_0.db','local_id':2,'local_type':1}]})
        self.stack.enter_context(patch(prefix+'ReplicaSnapshotReader',return_value=reader))
        def record(row):
            return None,None,SimpleNamespace(message_type='image' if row['local_id']==1 else 'text',
                normalized_text='' if row['local_id']==1 else 'student:\nExplain B.',
                raw_payload={'sender_username':'student'},message_id='image' if row['local_id']==1 else 'question')
        self.stack.enter_context(patch(prefix+'PersonalWeChatSource',return_value=SimpleNamespace(_record=record)))
        self.rows=self.stack.enter_context(patch(prefix+'current_chat_rows',return_value=[
            {'kind':'image','text':''},{'kind':'text','text':'Explain B.'}]))
        self.gate=self.stack.enter_context(patch(prefix+'temporary_accessibility'))
        self.stack.enter_context(patch(prefix+'load_accessibility_helper',return_value=SimpleNamespace(auto=self.auto)))

    def test_guard_rereads_account_and_never_reuses_previous_success(self):
        with personal_test_identity(self.config,source_message_ids=['image','question']) as guard:
            self.assertTrue(guard(self.recipient))
            self.assertEqual(guard.last_evidence['status'],'LIVE_PERSONAL_TEST_CHAT_BOUND')
            self.gate.assert_not_called()
            self.module.extract_master_key_from_cfg.return_value=(None,None,'other-account')
            with self.assertRaisesRegex(ValueError,'LIVE_ACCOUNT_CHANGED'):
                guard(self.recipient)
            self.assertIsNone(guard.last_evidence)
        self.connection.close.assert_called_once()
        self.auto.UninitializeUIAutomationInCurrentThread.assert_called_once()

    def test_explicit_gate_failure_records_independent_restoration(self):
        @contextmanager
        def scoped(*args,**kwargs):
            state={'process_flag_changed':True,'restored':False}
            try:yield state
            finally:state['restored']=True
        self.gate.side_effect=scoped
        self.rows.side_effect=ValueError('ACCESSIBILITY_CURRENT_CHAT_CHANGED')
        with personal_test_identity(self.config,source_message_ids=['image','question'],allow_temporary_accessibility=True) as guard:
            with self.assertRaisesRegex(ValueError,'CURRENT_CHAT_CHANGED'):
                guard(self.recipient)
            self.assertIsNone(guard.last_evidence)
            self.assertTrue(guard.last_attempt['process_flag_changed'])
            self.assertTrue(guard.last_attempt['process_flag_restored'])
            self.assertFalse(guard.last_attempt['sending_authorized'])
        self.auto.UninitializeUIAutomationInCurrentThread.assert_called_once()
