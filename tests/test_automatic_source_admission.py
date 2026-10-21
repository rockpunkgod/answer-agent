"""Anonymous source admission fixtures; no model, browser or delivery claims."""
import unittest
from unittest.mock import patch

from helpdesk.semantic_decisions import SharedSemanticDecisions
from tests import test_source_question_tasks as source_fixture


class AutomaticSourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fx=source_fixture.SourceQuestionTasksTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.message,self.receipt,self.outcome=self.fx.receive('automatic-new')
        self.shared=SharedSemanticDecisions(self.fx.db,self.fx.raw)
        self.decision=self.shared.confirm(self.receipt['id'],question_type='阅读理解',
            actor='anonymous semantic fixture',evidence='offline complete source/association fixture')

    def admit(self):
        return self.fx.tasks.admit_confirmed_semantic(self.fx.raw,self.decision,
            self.fx.manifest_path,ego_space=7)

    def test_task_created_without_second_human_gate_but_generation_waits_for_real_ack(self):
        before=self.fx.business_counts()
        task=self.admit()
        self.assertEqual(task['auto_queue']['phase'],'WAITING_ACK')
        self.assertEqual(task['message_id'],self.outcome.message_id)
        self.assertEqual(task['reviewer'],'system:confirmed-semantic-source')
        self.assertIsNone(task['run_id'])
        self.assertEqual(self.fx.business_counts(),{**before,'performance_units':before['performance_units']+1})
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM operator_tasks')[0],1)
        self.assertEqual(self.fx.db.one('SELECT confirmed_quantity FROM performance_units')[0],0)
        again=self.admit()
        self.assertEqual(again['id'],task['id'])
        self.assertEqual(self.fx.source_review_count(),1)
        self.fx.confirm_fixture_ack(self.outcome.message_id)
        ready=self.admit()
        self.assertEqual(ready['auto_queue']['phase'],'WAITING_DESKTOP_EXECUTOR')
        self.assertIsNotNone(ready['run_id'])
        self.assertEqual(self.admit()['run_id'],ready['run_id'])
        self.assertEqual(self.fx.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0],0)

    def test_changed_original_receipt_cannot_be_automatically_admitted(self):
        with self.fx.raw.connect() as db:
            db.execute('UPDATE messages SET normalized_text=? WHERE message_id=?',
                       ('changed source',self.message.message_id))
        with self.assertRaises(ValueError):self.admit()
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM operator_tasks')[0],0)

    def test_existing_manual_review_is_reused_without_forging_automatic_review(self):
        draft=self.fx.tasks.create_from_semantic(self.fx.raw,self.decision)
        original=self.fx.review(draft)
        admitted=self.admit()
        self.assertEqual(admitted['id'],original['id'])
        self.assertEqual(admitted['reviewer'],original['reviewer'])
        self.assertEqual(admitted['source_evidence'],original['source_evidence'])
        self.assertEqual(self.fx.source_review_count(),1)

    def test_unconfirmed_semantic_id_never_creates_task_or_queue(self):
        self.decision='semantic:unconfirmed'
        with self.assertRaises(ValueError):self.admit()
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM operator_tasks')[0],0)

    def runtime(self):
        from helpdesk.automatic_answer_runtime import AutomaticAnswerRuntime
        with patch('helpdesk.teaching_bundle.verify_bundle',return_value=self.fx.manifest):
            return AutomaticAnswerRuntime(self.fx.db.path,
                {'enabled':True,'backend':'ego_lite','space_id':7,'ego_policy':'MATCH_THEN_TEACH',
                 'admit_confirmed_sources':True},manifest=self.fx.manifest_path,
                collector_config={'collector':{'database':self.fx.raw.path}})

    def test_runtime_admits_once_and_restart_preserves_ack_wait_without_browser_calls(self):
        runtime=self.runtime()
        with patch.object(runtime,'_execute_ready',side_effect=AssertionError('ACK not confirmed')):
            self.assertEqual(runtime.tick()['state'],'IDLE')
            self.assertEqual(runtime.snapshot()['last_source_admission']['phase'],'WAITING_ACK')
            self.assertEqual(runtime.tick()['state'],'IDLE')
        restarted=self.runtime()
        self.assertEqual(restarted.tick()['state'],'IDLE')
        self.assertEqual(self.fx.db.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_AUTO_ADMITTED'")[0],1)
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM operator_tasks')[0],1)
        self.fx.confirm_fixture_ack(self.outcome.message_id)
        from helpdesk.reviewed_question_queue import resume_pending,list_queue
        resume_pending(self.fx.db)
        self.assertEqual(list_queue(self.fx.db)[0]['phase'],'WAITING_DESKTOP_EXECUTOR')

    def test_one_invalid_semantic_source_does_not_block_next_confirmed_source_or_repeat_failures(self):
        _,receipt,_=self.fx.receive('automatic-other')
        self.shared.confirm(receipt['id'],question_type='阅读理解',actor='fixture',evidence='offline fixture')
        with self.fx.raw.connect() as db:
            db.execute('UPDATE messages SET normalized_text=? WHERE message_id=?',('changed',self.message.message_id))
        runtime=self.runtime()
        self.assertEqual(runtime.tick()['state'],'IDLE')
        self.assertEqual(self.fx.db.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_AUTO_ADMISSION_BLOCKED'")[0],1)
        self.assertEqual(self.fx.db.one("SELECT COUNT(*) FROM audit WHERE event='SOURCE_AUTO_ADMITTED'")[0],1)
        self.assertEqual(runtime.tick()['state'],'IDLE')
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM operator_tasks')[0],1)

    def test_automatic_admission_cannot_silently_enable_without_collector_or_two_stage_policy(self):
        from helpdesk.automatic_answer_runtime import AutomaticAnswerRuntime
        for changes in ({'backend':'windows_mcp'},{'ego_policy':'DIRECT_ORIGINAL'},{'enabled':False}):
            config={'enabled':True,'backend':'ego_lite','space_id':7,'ego_policy':'MATCH_THEN_TEACH',
                    'admit_confirmed_sources':True,**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                AutomaticAnswerRuntime(self.fx.db.path,config,manifest=self.fx.manifest_path)


if __name__=='__main__':unittest.main()
