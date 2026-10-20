"""SQLite + mocked ego integration; no real browser, student or delivery."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.ego_generation import EgoDeepSeekGenerator, selected_option
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store, encode
from helpdesk.workflow import Workflow
from helpdesk.session_isolation import claim_deepseek_chat


class EgoGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'business.db');self.addCleanup(self.store.close)
        self.service=Helpdesk(self.store)
        student=self.service.bind('anonymous-group','student-1','anonymous',verified=True)
        self.incoming=self.service.ingest(Incoming(student,'请讲第12题',Intent.NEW,
            verified_question=demo_question(),raw_material='Passage',verified_material='Passage'))
        self.course=self.root/'teaching.md';self.course.write_text('original teaching',encoding='utf-8')
        self.skill={'name':self.course.name,'path':str(self.course),'sha256':sha256(self.course.read_bytes()).hexdigest(),
                    'manifest_path':str(self.root/'manifest.json'),'content':'original teaching','source':'verified_teaching_manifest'}
        self.generator=EgoDeepSeekGenerator(1,self.root/'evidence')
        self.workflow=Workflow(self.store,teaching_manifest=self.root/'manifest.json',generation_adapter=self.generator)
        with patch.object(self.workflow,'_skills',return_value=[self.skill]), \
                patch.object(self.workflow,'_require_confirmed_ack'):
            self.run_id=self.workflow.start(self.incoming.turn_id)
        self.snapshot=json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?',(self.run_id,))[0])
        self.url='https://chat.deepseek.com/a/chat/s/anonymous'
        claim_deepseek_chat(self.snapshot,self.url)
        # This fixture starts with an independently bound, existing conversation.
        self.snapshot['ego_request_url']=self.url
        self.store.execute('UPDATE runs SET input_json=? WHERE id=?',(encode(self.snapshot),self.run_id))
        self.calls=[]
        self.body='第12题选D，因为应根据学生版本判断。'
        self.action=None
        self.patches=[patch('helpdesk.ego_generation.verify_frozen_teaching',return_value={}),
            patch('helpdesk.ego_generation.validate_direct_source',return_value=None),
            patch('helpdesk.ego_generation.EgoDeepSeek',side_effect=lambda *a,**k:self)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def submit(self, request_id, url, prompt, *, attachments, capture_only, new_session=False):
        self.calls.append((request_id,url,prompt,attachments,capture_only))
        if self.action:self.action()
        result={'adapter':'EGO_LITE','space_id':1,'page':'p1','complete':True,'session_url':self.url if new_session else url,'text':self.body,
            'upload_manifest':[{'path':str(p),'name':p.name,'sha256':sha256(p.read_bytes()).hexdigest()} for p in attachments]}
        directory=self.root/'evidence';directory.mkdir(exist_ok=True)
        (directory/(request_id+'.answer.json')).write_text(json.dumps(result),encoding='utf-8')
        (directory/(request_id+'.state.json')).write_text(json.dumps({'phase':'CAPTURED',
            'output_sha256':sha256((directory/(request_id+'.answer.json')).read_bytes()).hexdigest()}),encoding='utf-8')
        return result

    def test_unknown_capture_recovery_preserves_initial_result_and_one_call(self):
        from helpdesk.call_costs import task_cost,confirm_ego_capture
        with patch.object(self,'submit',side_effect=TimeoutError('synthetic observation timeout')):
            with self.assertRaises(TimeoutError):self.generator.generate(self.snapshot)
        result=self.generator.generate(self.snapshot)
        initial=json.loads(self.store.one("SELECT details FROM audit WHERE event='ANSWER_CALL_RESULT'")[0])
        self.assertEqual(initial['status'],'UNKNOWN')
        self.assertEqual(self.store.one("SELECT count(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0],1)
        confirm_ego_capture(self.store,self.run_id,initial['call_id'],result['web_session_evidence'])
        self.assertEqual(self.store.one("SELECT count(*) FROM audit WHERE event='ANSWER_EGO_CALL_RECOVERED'")[0],1)
        costs=task_cost(self.store,self.run_id)
        self.assertEqual(costs['confirmed_calls'],1)
        self.assertEqual(costs['cost_status'],'UNVERIFIED')
        self.assertIsNone(costs['total_cny'])
        self.assertEqual(self.workflow.finish(self.run_id,result)['state'],'GENERATED')
        Path(result['web_session_evidence']).write_text('{}',encoding='utf-8')
        costs=task_cost(self.store,self.run_id)
        self.assertEqual(costs['confirmed_calls'],0)
        self.assertTrue(any(r.startswith('RECOVERY_PROOF_CHANGED:') for r in costs['unverified']))

    def test_natural_draft_returns_to_existing_outbox_without_delivery(self):
        result=self.generator.generate(self.snapshot)
        completed=self.workflow.finish(self.run_id,result)
        self.assertEqual(completed['state'],'GENERATED',completed)
        outbox=self.store.one('SELECT * FROM outbox WHERE id=?',(completed['outbox_id'],))
        self.assertEqual(outbox['body'],self.body)
        self.assertEqual(outbox['state'],'PENDING')
        self.assertEqual(outbox['simulated'],0)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0],1)
        again=self.workflow.finish(self.run_id,result)
        self.assertEqual(again['outbox_id'],completed['outbox_id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0],1)
        self.assertNotIn('BEGIN_',self.calls[0][2])
        self.assertNotIn('JSON',self.calls[0][2])
        self.assertEqual(self.calls[0][3],[self.course])
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0],1)

    def test_paused_or_changed_question_stops_before_browser(self):
        for reason in ['paused','changed']:
            with self.subTest(reason=reason):
                with self.store.transaction():
                    if reason=='paused':self.store.execute("UPDATE settings SET value='true' WHERE key='stop_requested'")
                    else:
                        self.store.execute("UPDATE settings SET value='false' WHERE key='stop_requested'")
                        self.store.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?',(self.snapshot['question_id'],))
                with self.assertRaises(ValueError):self.generator.generate(self.snapshot)
        self.assertEqual(self.calls,[])

    def test_correction_while_generating_blocks_old_result(self):
        def correct():
            with self.store.transaction():
                self.store.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?',(self.snapshot['question_id'],))
        self.action=correct
        with self.assertRaisesRegex(ValueError,'CURRENT_QUESTION_CHANGED'):
            self.generator.generate(self.snapshot)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0],0)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0],0)

    def test_unverified_match_or_changed_teaching_stops_before_browser(self):
        for target in ['validate_direct_source','verify_frozen_teaching']:
            with patch('helpdesk.ego_generation.'+target,side_effect=ValueError('unverified')):
                with self.assertRaisesRegex(ValueError,'unverified'):self.generator.generate(self.snapshot)
        self.assertEqual(self.calls,[])

    def test_plain_answer_requires_one_explicit_student_option_and_question(self):
        for text in ['不选B，答案是D。','第12题选D。','所以第12题落在D。']:
            self.assertTrue(selected_option(self.snapshot,text))
        for text in ['我认为可以。','选B，也选D。','第13题选D。','不选B。','不能选B。','不应该选择B。',
                     '第12题落在C和D之间。','第12题不落在D。','第13题落在D。','选C，所以第12题落在D。']:
            with self.assertRaises(ValueError):selected_option(self.snapshot,text)

    def test_unknown_browser_is_not_a_completed_draft(self):
        with patch.object(self,'submit',side_effect=TimeoutError('anonymous timeout')):
            with self.assertRaises(TimeoutError):self.generator.generate(self.snapshot)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0],0)
        row=self.store.one("SELECT details FROM audit WHERE event='ANSWER_CALL_RESULT'")
        self.assertEqual(json.loads(row[0])['status'],'UNKNOWN')

    def test_completed_multi_question_reply_is_saved_rejected_never_left_running(self):
        from helpdesk.ego_generation import run_existing
        self.body='第12题选D。第13题选B。'
        result=run_existing(self.store,self.run_id,1,self.root/'evidence')
        self.assertEqual(result['state'],'REJECTED')
        self.assertEqual(result['reason'],'EGO_ANSWER_OPTION_REQUIRES_REVIEW')
        self.assertIsNone(result['outbox_id'])
        self.assertEqual(self.store.one('SELECT text FROM answers')[0],self.body)
        self.assertEqual(self.store.one('SELECT state FROM runs')[0],'REJECTED')
        self.assertEqual(self.store.one("SELECT count(*) FROM audit WHERE event='EGO_TEACH_CAPTURED'")[0],0)
        self.assertEqual(self.store.one("SELECT count(*) FROM audit WHERE event='EGO_TEACH_CAPTURE_REJECTED'")[0],1)
        again=run_existing(self.store,self.run_id,1,self.root/'evidence')
        self.assertFalse(again['resubmitted']);self.assertEqual(len(self.calls),1)

    def test_completed_wrong_question_preserves_raw_reply_and_specific_reason(self):
        from helpdesk.ego_generation import run_existing
        self.body='第13题选D。'
        result=run_existing(self.store,self.run_id,1,self.root/'evidence')
        self.assertEqual(result['state'],'REJECTED')
        self.assertEqual(result['reason'],'EGO_ANSWER_QUESTION_REQUIRES_REVIEW')
        self.assertEqual(self.store.one('SELECT text FROM answers')[0],self.body)
        self.assertIsNone(result['outbox_id'])

    def test_changed_capture_is_rejected_by_workflow_before_outbox(self):
        result=self.generator.generate(self.snapshot)
        proof=Path(result['web_session_evidence'])
        raw=json.loads(proof.read_text(encoding='utf-8'));raw['text']='第12题选B。'
        proof.write_text(json.dumps(raw),encoding='utf-8')
        completed=self.workflow.finish(self.run_id,result)
        self.assertEqual(completed['state'],'REJECTED')
        self.assertEqual(completed['reason'],'EGO_CAPTURE_EVIDENCE_INVALID')
        self.assertIsNone(completed['outbox_id'])

    def test_original_run_entry_does_not_replay_a_completed_run(self):
        from helpdesk.ego_generation import run_existing
        first=run_existing(self.store,self.run_id,1,self.root/'evidence')
        self.assertEqual(first['state'],'GENERATED',first)
        second=run_existing(self.store,self.run_id,1,self.root/'evidence')
        self.assertFalse(second['resubmitted'])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM answers')[0],1)

    def test_capture_change_also_blocks_existing_delivery_validation(self):
        from helpdesk.test_answer_queue import validate_source_answer
        result=self.generator.generate(self.snapshot)
        completed=self.workflow.finish(self.run_id,result)
        row=self.store.one('SELECT * FROM outbox WHERE id=?',(completed['outbox_id'],))
        validate_source_answer(self.store,row,approval=False)
        Path(result['web_session_evidence']).write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'EVIDENCE_CHANGED'):
            validate_source_answer(self.store,row,approval=False)

    def test_frozen_browser_cannot_change_before_generation(self):
        for space, page, directory in [(2,'p1',self.root/'evidence'),
                                       (1,'p2',self.root/'evidence'),
                                       (1,'p1',self.root/'other-evidence')]:
            generator=EgoDeepSeekGenerator(space,directory,page=page)
            with self.assertRaisesRegex(ValueError,'EGO_FROZEN_BROWSER_CHANGED'):
                generator.generate(self.snapshot)
        self.assertEqual(self.calls,[])
