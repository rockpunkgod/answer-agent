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
                'text':getattr(self,'reply_text','第12题选C，因为要照顾母亲。'),'upload_manifest':[
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

    def followup(self):
        from helpdesk.domain import Intent
        self.follow_index=getattr(self,'follow_index',0)+1
        _,receipt,outcome=self.fx.receive('follow-direct-'+str(self.follow_index),intent=Intent.FOLLOWUP,
                                         question_id=self.task['question_id'])
        self.task=self.fx.review(self.fx.create(receipt));self.fx.confirm_fixture_ack(outcome.message_id)
        return self.admit()

    def test_followup_reuses_session_and_uploads_no_old_files_or_unsent_draft(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        first=self.fx.tasks.get_task(self.task['id'])
        admission=self.followup();self.assertTrue(admission['enqueued'],admission)
        task=self.fx.tasks.get_task(self.task['id'])
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
        self.assertEqual(snapshot['ego_material_reuse']['origin_run_id'],first['run_id'])
        result=self.runtime.tick();self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.calls[-1][3],[])
        self.assertFalse(self.calls[-1][-1])
        self.assertEqual(self.calls[-1][2],snapshot['student_words'])
        self.assertNotIn('此前实际回复',self.calls[-1][2])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0],1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],0)

    def test_changed_origin_capture_stops_followup_before_another_call(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        original=self.fx.tasks.get_task(self.task['id'])
        proof=Path(original['evidence_dir'])/(original['run_id']+'-teach.answer.json')
        proof.write_text('{}',encoding='utf-8')
        admission=self.followup()
        self.assertEqual(admission['admission_phase'],'NEEDS_ATTENTION',admission)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0],1)

    def test_followup_context_uses_registered_actual_reply_instead_of_old_draft(self):
        from helpdesk.manual_delivery import ManualDeliveries
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        actual='第12题选C。这是人工实际交付的补充说明。'
        ManualDeliveries(self.db).register(row['id'],question_version=row['question_version'],
            context_revision=row['context_revision'],reviewer='anonymous verifier',
            verification_evidence='Synthetic human attestation, not a real platform receipt',
            delivered_at='2026-10-01T10:00:00+08:00',content=actual)
        admission=self.followup();self.assertTrue(admission['enqueued'],admission)
        result=self.runtime.tick();self.assertEqual(result['state'],'GENERATED',result)
        self.assertIn(actual,self.calls[-1][2])
        self.assertNotIn('因为要照顾母亲。',self.calls[-1][2])
        self.assertEqual(self.calls[-1][3],[])

    def register_actual_original(self):
        from helpdesk.manual_delivery import ManualDeliveries
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER' ORDER BY rowid LIMIT 1")
        return ManualDeliveries(self.db).register(row['id'],question_version=row['question_version'],
            context_revision=row['context_revision'],reviewer='anonymous verifier',
            verification_evidence='Synthetic actual delivery registered while a followup was frozen',
            delivered_at='2026-10-01T10:00:00+08:00',content='第12题选C。这是老师后来实际交付的说明。')

    def test_new_actual_delivery_after_freeze_stops_before_followup_submission(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.followup();self.register_actual_original()
        result=self.runtime.tick()
        self.assertEqual(result['state'],'NEEDS_ATTENTION',result)
        self.assertEqual(result['reason'],'QUEUE_EVIDENCE_INVALID:ValueError')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)

    def test_actual_delivery_while_generating_blocks_new_outbox(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.followup()
        original=self.submit
        def changing(*args,**kwargs):
            captured=original(*args,**kwargs)
            self.register_actual_original()
            return captured
        with patch.object(self,'submit',side_effect=changing):
            result=self.runtime.tick()
        self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(result['reason'],'GENERATION_UNCERTAIN:ValueError')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)
        task=self.fx.tasks.get_task(self.task['id'])
        self.assertTrue((Path(task['evidence_dir'])/(task['run_id']+'-teach.answer.json')).is_file())
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE run_id=?",(task['run_id'],))[0],0)

    def test_actual_delivery_after_generation_blocks_pending_followup_delivery(self):
        from helpdesk.test_answer_queue import validate_source_answer
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.followup();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER' ORDER BY rowid DESC LIMIT 1")
        self.register_actual_original()
        with self.assertRaisesRegex(ValueError,'GENERATION_CONTEXT_CHANGED'):
            validate_source_answer(self.db,row,approval=False)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?',(row['id'],))[0],'PENDING')

    def test_followup_can_explain_without_repeating_original_answer_letter(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.followup();self.reply_text='B 说的是度假，原文说的是照顾母亲，目的不一致。'
        result=self.runtime.tick();self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.calls[-1][3],[])
        self.assertEqual(self.db.one('SELECT correct_option_id FROM answer_evidence ORDER BY rowid DESC LIMIT 1')[0],
                         self.db.one('SELECT correct_option_id FROM answer_evidence ORDER BY rowid LIMIT 1')[0])

    def test_followup_cannot_silently_change_the_original_selection(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.followup();self.reply_text='第12题选D。'
        result=self.runtime.tick();self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)

    def test_followup_keeps_original_photo_in_session_without_reupload(self):
        from PIL import Image
        photo=self.base/'original-for-follow.png';Image.new('RGB',(20,20),'white').save(photo)
        _,receipt,outcome=self.fx.receive('photo-before-follow',photo=photo)
        self.task=self.fx.review(self.fx.create(receipt));self.fx.confirm_fixture_ack(outcome.message_id)
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        self.assertEqual(self.calls[0][3],[self.fx.course,photo])
        self.followup();result=self.runtime.tick();self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.calls[-1][3],[])

    def test_origin_changed_after_followup_freeze_stops_before_browser(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        original=self.fx.tasks.get_task(self.task['id'])
        self.followup()
        (Path(original['evidence_dir'])/(original['run_id']+'-teach.answer.json')).write_text('{}',encoding='utf-8')
        result=self.runtime.tick();self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(len(self.calls),1)

    def test_multiple_followups_point_to_original_upload_without_a_reuse_chain(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        original=self.fx.tasks.get_task(self.task['id'])
        for _ in range(2):
            self.followup();result=self.runtime.tick();self.assertEqual(result['state'],'GENERATED',result)
            task=self.fx.tasks.get_task(self.task['id'])
            snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(task['run_id'],))[0])
            self.assertEqual(snapshot['ego_material_reuse']['origin_run_id'],original['run_id'])
            self.assertEqual(self.calls[-1][3],[])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0],1)

    def test_actual_teacher_choice_conflict_cannot_inherit_old_generated_selection(self):
        from helpdesk.manual_delivery import ManualDeliveries
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        ManualDeliveries(self.db).register(row['id'],question_version=row['question_version'],
            context_revision=row['context_revision'],reviewer='anonymous verifier',
            verification_evidence='Synthetic corrected teacher response; no actual platform delivery',
            delivered_at='2026-10-01T10:00:00+08:00',content='第12题选D，这是老师实际交付的不同结论。')
        admission=self.followup()
        self.assertEqual(admission['admission_phase'],'NEEDS_ATTENTION',admission)
        self.assertEqual(len(self.calls),1)

    def test_actual_delivery_image_is_not_silently_omitted_from_followup_context(self):
        from PIL import Image
        from helpdesk.manual_delivery import ManualDeliveries
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        root=self.base/'approved-delivery';root.mkdir()
        image=root/'teacher.png';Image.new('RGB',(20,20),'white').save(image)
        ManualDeliveries(self.db,attachment_root=root).register(row['id'],question_version=row['question_version'],
            context_revision=row['context_revision'],reviewer='anonymous verifier',
            verification_evidence='Synthetic human attestation of an image reply',
            delivered_at='2026-10-01T10:00:00+08:00',content='',attachments=[image.name])
        admission=self.followup()
        self.assertEqual(admission['admission_phase'],'NEEDS_ATTENTION',admission)
        self.assertEqual(len(self.calls),1)

    def register_approved_image(self):
        from PIL import Image
        from helpdesk.manual_delivery import ManualDeliveries
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER' ORDER BY rowid LIMIT 1")
        root=Path(self.db.path).parent/'delivery-attachments';root.mkdir(exist_ok=True)
        image=root/'teacher.png';Image.new('RGB',(20,20),'white').save(image)
        ManualDeliveries(self.db,attachment_root=root).register(row['id'],question_version=row['question_version'],
            context_revision=row['context_revision'],reviewer='anonymous verifier',
            verification_evidence='Synthetic registered image, not a real platform receipt',
            delivered_at='2026-10-01T10:00:00+08:00',content='',attachments=[image.name])
        return image

    def test_followup_uploads_registered_image_without_reuploading_course_or_draft(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        image=self.register_approved_image()
        self.followup();result=self.runtime.tick()
        self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.calls[-1][3],[image])
        self.assertIn('已人工交付附件：teacher.png',self.calls[-1][2])
        self.assertNotIn('因为要照顾母亲。',self.calls[-1][2])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0],1)

    def test_missing_or_changed_actual_image_blocks_followup_before_browser(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        image=self.register_approved_image()
        raw=image.read_bytes();image.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        admission=self.followup()
        self.assertEqual(admission['admission_phase'],'NEEDS_ATTENTION',admission)
        self.assertEqual(len(self.calls),1)

    def test_actual_image_changed_after_freeze_blocks_followup_before_browser(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        image=self.register_approved_image();self.followup();image.unlink()
        result=self.runtime.tick()
        self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(len(self.calls),1)

    def test_actual_image_changed_after_capture_blocks_delivery(self):
        from helpdesk.test_answer_queue import validate_source_answer
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        image=self.register_approved_image();self.followup()
        self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        row=self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER' ORDER BY rowid DESC LIMIT 1")
        image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'EGO_DELIVERY_ATTACHMENT_CONTEXT_REQUIRES_REVIEW'):
            validate_source_answer(self.db,row,approval=False)

    def test_actual_image_changed_during_generation_preserves_capture_without_new_answer(self):
        self.admit();self.assertEqual(self.runtime.tick()['state'],'GENERATED')
        image=self.register_approved_image();self.followup()
        original=self.submit
        def changing(*args,**kwargs):
            captured=original(*args,**kwargs)
            image.write_bytes(b'changed while awaiting model')
            return captured
        with patch.object(self,'submit',side_effect=changing):
            result=self.runtime.tick()
        self.assertEqual(result['state'],'EXECUTION_UNCERTAIN',result)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0],1)
        task=self.fx.tasks.get_task(self.task['id'])
        self.assertTrue((Path(task['evidence_dir'])/(task['run_id']+'-teach.answer.json')).exists())
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM outbox WHERE run_id=?',(task['run_id'],))[0],0)
