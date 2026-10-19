"""Anonymous source DB + mocked ego; original source and queue guards are real."""
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from helpdesk.automatic_answer_runtime import AutomaticAnswerRuntime
from helpdesk.ego_generation import validate_direct_source
from helpdesk.reviewed_question_queue import request_enqueue, resume_pending
from tests import test_source_question_tasks as source_fixtures


class DirectQueueTests(unittest.TestCase):
    def setUp(self):
        self.fx=source_fixtures.SourceQuestionTasksTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.db,self.base=self.fx.db,self.fx.base
        self.task=self.fx.review();self.fx.confirm_fixture_ack()
        self.calls=[];self.failure=False
        for target in ['helpdesk.ego_generation.verify_frozen_teaching','helpdesk.teaching_bundle.verify_bundle']:
            p=patch(target,return_value=self.fx.manifest);p.start();self.addCleanup(p.stop)
        p=patch('helpdesk.ego_generation.EgoDeepSeek',side_effect=lambda *a,**k:self)
        p.start();self.addCleanup(p.stop)
        self.config={'enabled':True,'backend':'ego_lite','space_id':1,'page':'p1'}
        self.runtime=AutomaticAnswerRuntime(self.db.path,self.config,manifest=self.fx.manifest_path)

    def submit(self,request_id,url,prompt,*,attachments,capture_only,new_session=False):
        task=self.fx.tasks.get_task(self.task['id'])
        directory=Path(task['evidence_dir'])
        proof=directory/(request_id+'.answer.json')
        if capture_only and proof.is_file():
            return json.loads(proof.read_text(encoding='utf-8'))
        self.calls.append((request_id,url,prompt,attachments,new_session))
        if self.failure:raise TimeoutError('anonymous fixture')
        result={'adapter':'EGO_LITE','complete':True,'session_url':'https://chat.deepseek.com/a/chat/s/anonymous',
                'text':'第12题选C，因为要照顾母亲。','upload_manifest':[
                    {'path':str(p),'name':p.name,'sha256':sha256(p.read_bytes()).hexdigest()} for p in attachments]}
        task=self.fx.tasks.get_task(self.task['id'])
        directory=Path(task['evidence_dir']);directory.mkdir(parents=True,exist_ok=True)
        (directory/(request_id+'.answer.json')).write_text(json.dumps(result),encoding='utf-8')
        (directory/(request_id+'.state.json')).write_text(json.dumps({'phase':'CAPTURED'}),encoding='utf-8')
        return result

    def admit(self):
        return request_enqueue(self.db,self.task['id'],self.fx.manifest_path,ego_space=1)

    def test_review_direct_generation_original_outbox_no_match_or_delivery(self):
        queued=self.admit()
        task=self.fx.tasks.get_task(self.task['id'])
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
        self.assertNotIn('question_match_result',snapshot)
        validate_direct_source(snapshot)
        result=self.runtime.tick()
        self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.calls[0][-1])
        self.assertNotIn('JSON',self.calls[0][2]);self.assertNotIn('BEGIN_',self.calls[0][2])
        answer=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        self.assertEqual(answer['run_id'],task['run_id'])
        self.assertEqual(answer['state'],'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0],1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0],1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],0)
        self.runtime.tick()
        self.assertEqual(len(self.calls),1)

    def test_unknown_submission_stops_original_queue_without_replay(self):
        self.admit();self.failure=True
        first=self.runtime.tick()
        self.assertEqual(first['state'],'EXECUTION_UNCERTAIN',first)
        self.assertEqual(self.runtime.tick()['state'],'IDLE')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],0)

    def test_backend_admission_survives_waiting_ack_and_rejects_switch(self):
        self.db.execute("UPDATE outbox SET state='PENDING' WHERE purpose='ACK'")
        admission=self.admit();self.assertEqual(admission['admission_phase'],'WAITING_ACK')
        with self.assertRaisesRegex(ValueError,'ADMISSION_BACKEND_CHANGED'):
            request_enqueue(self.db,self.task['id'],self.fx.manifest_path)
        self.fx.confirm_fixture_ack();resume_pending(self.db)
        task=self.fx.tasks.get_task(self.task['id'])
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
        self.assertEqual(snapshot['generation_adapter'],'EGO_LITE_DEEPSEEK')
        self.assertEqual(snapshot['ego_policy'],'DIRECT_ORIGINAL')
        self.assertEqual(snapshot['ego_browser']['space_id'],1)

    def test_missing_source_review_cannot_enter_direct_generation(self):
        self.admit();task=self.fx.tasks.get_task(self.task['id'])
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
        self.db.execute("DELETE FROM audit WHERE event='SOURCE_QUESTION_INPUT_REVIEWED'")
        with self.assertRaises(ValueError):validate_direct_source(snapshot)
        self.assertEqual(self.calls,[])

    def test_restart_after_capture_recovers_original_run_without_another_submit(self):
        self.admit()
        with patch('helpdesk.workflow.Workflow.finish',side_effect=SystemExit('synthetic process stop')):
            with self.assertRaises(SystemExit):self.runtime.tick()
        self.assertEqual(self.db.one('SELECT state FROM runs')[0],'RUNNING')
        runtime=AutomaticAnswerRuntime(self.db.path,self.config,manifest=self.fx.manifest_path)
        result=runtime.tick()
        self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0],1)

    def test_original_photo_and_teaching_are_the_only_uploaded_files(self):
        from PIL import Image
        photo=self.base/'original.png';Image.new('RGB',(20,20),'white').save(photo)
        _,receipt,outcome=self.fx.receive('photo-direct',photo=photo)
        self.task=self.fx.review(self.fx.create(receipt));self.fx.confirm_fixture_ack(outcome.message_id)
        self.admit()
        result=self.runtime.tick()
        self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.calls[0][3],[self.fx.course,photo])
        task=self.fx.tasks.get_task(self.task['id'])
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
        self.assertEqual(self.calls[0][2],snapshot['student_words'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],0)

    def test_same_returned_chat_cannot_be_bound_to_another_independent_question(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        _,receipt,outcome=self.fx.receive('other-question')
        self.task=self.fx.review(self.fx.create(receipt));self.fx.confirm_fixture_ack(outcome.message_id)
        self.admit();result=self.runtime.tick()
        self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0],1)
