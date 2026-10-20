"""Original-task SQLite fixtures plus synthetic API responses; no paid calls."""
from dataclasses import replace
from hashlib import sha256
import base64
from html import escape
import json
from pathlib import Path
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import Mock,patch

from PIL import Image
from helpdesk.domain import Question,Intent
from helpdesk.service import Helpdesk,Incoming
from helpdesk.youdao_api import ApiError,Settings
from helpdesk.youdao_reference_pipeline import YoudaoReferencePipeline
from tests import test_ego_direct_queue as fixtures


class YoudaoPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.DirectQueueTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.base,self.db,self.source=self.fx.base,self.fx.db,self.fx.fx
        folder=self.base/'data/private/youdao/test-images';folder.mkdir(parents=True)
        self.image=folder/'匿名单题.png';Image.new('RGB',(100,100),'white').save(self.image)
        _,receipt,outcome=self.source.receive('synthetic-photo',photo=self.image)
        self.fx.task=self.source.review(self.source.create(receipt));self.source.confirm_fixture_ack(outcome.message_id)
        self.fx.admit()
        task=self.source.tasks.get_task(self.fx.task['id']);self.run_id=task['run_id']
        self.snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(self.run_id,))[0])
        self.settings=Settings(workspace=self.base,erase_enabled=True,search_enabled=True,
            erase_reservation_cny='0.10',search_reservation_cny='0.10')
        stub=patch.dict('os.environ',{'YOUDAO_ERASE_APP_KEY':'synthetic-e','YOUDAO_ERASE_APP_SECRET':'synthetic-es',
                                    'YOUDAO_SEARCH_APP_KEY':'synthetic-s','YOUDAO_SEARCH_APP_SECRET':'synthetic-ss'},clear=True)
        stub.start();self.addCleanup(stub.stop)
        self.calls=[];self.contents=None;self.correct=False
        self.pipeline=YoudaoReferencePipeline(self.settings,transport=self.transport)

    def html(self, *, reverse=False, number=None, stem=None):
        q=Question.from_dict(self.snapshot['student_question'])
        options=[o.verified_text for o in q.options]
        if reverse:options.reverse()
        return '<p>'+escape(self.snapshot['student_material'])+'</p><p>'+escape(number or q.number)+'. '+escape(stem or q.verified_stem)+'</p>'+''.join(
            '<p>'+label+'. '+escape(text)+'</p>' for label,text in zip('ABCD',options))

    def transport(self,api,fields,settings):
        self.calls.append(api)
        if api=='erase':
            return 200,json.dumps({'errorCode':'0','eraseEnhanceImg':base64.b64encode(self.image.read_bytes()).decode()}).encode()
        if self.correct:
            q=Question.from_dict(self.snapshot['student_question'])
            Helpdesk(self.db).ingest(Incoming(self.source.binding,'synthetic changed condition',Intent.CORRECTION,
                question_id=self.snapshot['question_id'],verified_question=replace(q,raw_stem='Why did John NOT go home?',
                                                                               verified_stem='Why did John NOT go home?')))
        content=self.contents if self.contents is not None else [self.html()]
        return 200,json.dumps({'errorCode':'0','data':{'questions':[
            {'id':'synthetic-'+str(i),'content':html,'answer':'FORBIDDEN_EXTERNAL_ANSWER',
             'analysis':'FORBIDDEN_EXTERNAL_TEACHING'} for i,html in enumerate(content)]}}).encode()

    def test_candidates_return_to_original_review_without_answer_or_count(self):
        result=self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(result['status'],'MATCH_CANDIDATE');self.assertEqual(result['cleanup_status'],'CLEANUP_SKIPPED')
        self.assertEqual(self.calls,['search'])
        repeated=self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(repeated['actual_requests'],0);self.assertEqual(self.calls,['search'])
        self.assertEqual(result['candidates'][0]['candidate_id'],repeated['candidates'][0]['candidate_id'])
        rows=self.db.all('SELECT payload,comparison FROM reference_candidates')
        self.assertEqual(len(rows),1)
        for record in (result,dict(rows[0])):
            self.assertNotIn('FORBIDDEN_EXTERNAL',json.dumps(record))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0],0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],0)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0],1)
        call=json.loads(self.db.one("SELECT details FROM audit WHERE event='ANSWER_CALL_STARTED'")[0])
        self.assertTrue(call['injected'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_CHARGE'")[0],0)
        from helpdesk.call_costs import question_cost
        costs=question_cost(self.db,self.snapshot['question_id'])
        self.assertEqual(costs['observed_call_counts']['SEARCH'],1)
        self.assertIsNone(costs['total_cny'])
        self.assertEqual(costs['cost_status'],'UNVERIFIED')

    def test_top_two_and_student_option_letters_preserved(self):
        self.contents=[self.html(reverse=True),self.html(number='99'),self.html(number='98')]
        result=self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(len(result['candidates']),2)
        mapping=result['candidates'][0]['option_mapping']
        self.assertEqual({m['reference_label']:m['student_label'] for m in mapping},dict(A='D',B='C',C='B',D='A'))
        current=self.db.one('SELECT payload FROM question_versions WHERE id=?',(self.snapshot['question_version'],))[0]
        self.assertEqual(json.loads(current),self.snapshot['student_question'])

    def test_incomplete_content_stays_unverified_and_conflicting_not_is_blocked(self):
        self.contents=['<p>only a fragment</p>',self.html(stem='Why did John NOT go home?')]
        result=self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(result['status'],'MANUAL_REVIEW_REQUIRED')
        self.assertTrue(all(c['status']!='MATCH_CANDIDATE' for c in result['candidates']))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM reference_candidates WHERE json_extract(comparison,'$.candidate.consumption_enabled')=1")[0],0)

    def test_cleanup_requires_current_print_review_and_never_replaces_original(self):
        original=self.image.read_bytes()
        result=self.pipeline.run(self.db,self.run_id,self.image,needs_cleanup=True)
        self.assertEqual(result['status'],'MANUAL_REVIEW_REQUIRED');self.assertEqual(self.calls,['erase'])
        self.assertEqual(result['cleanup_status'],'CLEANUP_SUCCEEDED')
        review=dict(original_sha256=result['original_sha256'],processed_sha256=result['cleanup']['processed_sha256'],
            question_version=self.snapshot['question_version'],context_revision=self.snapshot['context_revision'],
            reviewer='SYNTHETIC print reviewer',question=self.snapshot['student_question'],material=self.snapshot['student_material'])
        for change in (dict(verified_stem='Why did John NOT go home?'),dict(number='99'),
                       dict(options=review['question']['options'][:-1])):
            damaged=dict(review,question=dict(review['question'],**change))
            self.assertEqual(self.pipeline.run(self.db,self.run_id,self.image,needs_cleanup=True,cleanup_review=damaged)['status'],'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(self.calls,['erase'])
        reviewed=self.pipeline.run(self.db,self.run_id,self.image,needs_cleanup=True,cleanup_review=review)
        self.assertEqual(reviewed['status'],'MATCH_CANDIDATE');self.assertEqual(self.calls,['erase','search'])
        self.assertTrue(reviewed['cleanup']['print_preservation_verified'])
        self.pipeline.run(self.db,self.run_id,self.image,needs_cleanup=True,cleanup_review=review)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='YOUDAO_CLEANUP_REVIEWED'")[0],1)
        self.assertEqual(self.image.read_bytes(),original)
        kinds=[json.loads(r[0])['kind'] for r in self.db.all("SELECT details FROM audit WHERE event='ANSWER_CALL_STARTED'")]
        self.assertEqual(kinds,['OTHER','SEARCH'])

    def test_reservation_stops_search_after_cleanup_budget(self):
        pipe=YoudaoReferencePipeline(replace(self.settings,budget_cny='0.15'),transport=self.transport)
        first=pipe.run(self.db,self.run_id,self.image,needs_cleanup=True)
        review=dict(original_sha256=first['original_sha256'],processed_sha256=first['cleanup']['processed_sha256'],
            question_version=self.snapshot['question_version'],context_revision=self.snapshot['context_revision'],
            reviewer='SYNTHETIC',question=self.snapshot['student_question'],material=self.snapshot['student_material'])
        result=pipe.run(self.db,self.run_id,self.image,needs_cleanup=True,cleanup_review=review)
        self.assertEqual(result['status'],'BUDGET_EXCEEDED');self.assertEqual(self.calls,['erase'])

    def test_correction_during_search_preserves_call_evidence_but_blocks_application(self):
        self.correct=True
        with self.assertRaises(ValueError):self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(self.calls,['search'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reference_candidates')[0],0)
        with self.assertRaises(ValueError):self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(self.calls,['search'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_RESULT'")[0],1)

    def test_unbound_or_changed_original_stops_before_post(self):
        Image.new('RGB',(100,100),'black').save(self.image)
        with self.assertRaisesRegex(ApiError,'ORIGINAL_IMAGE_NOT_BOUND'):
            self.pipeline.run(self.db,self.run_id,self.image)
        self.assertEqual(self.calls,[])

    def test_cli_uses_existing_task_without_schema_changes_or_external_answer_output(self):
        from tools.test_youdao_apis import main
        before=[r[0] for r in self.db.all("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")]
        with patch('tools.test_youdao_apis.Settings.load',return_value=self.settings), \
             patch('tools.test_youdao_apis.ROOT',self.base), \
             patch('tools.test_youdao_apis.YoudaoReferencePipeline',return_value=self.pipeline), \
             redirect_stdout(io.StringIO()) as output:
            code=main(['--image',str(self.image),'--pipeline','--db',str(self.db.path),'--run-id',self.run_id,
                       '--output-dir',str(self.base/'data/private/results')])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['actual_requests'],1)
        self.assertNotIn('FORBIDDEN_EXTERNAL',output.getvalue())
        self.assertEqual(before,[r[0] for r in self.db.all("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")])
