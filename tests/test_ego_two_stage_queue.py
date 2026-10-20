"""Two native stages through the original queue; every source/browser is a fixture."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from helpdesk.automatic_answer_runtime import AutomaticAnswerRuntime
from helpdesk.domain import Intent, Question
from helpdesk.reviewed_question_queue import get, request_enqueue
from helpdesk.service import Helpdesk, Incoming
from tests import test_ego_direct_queue as direct_fixture
from tests.test_question_matching import STUDENT_ONLY


class EgoTwoStageQueueTests(unittest.TestCase):
    def setUp(self):
        self.fx = direct_fixture.DirectQueueTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.db, self.base = self.fx.db, self.fx.base
        self.calls = []
        self.model = STUDENT_ONLY
        self.failure = None
        self.correct_during_teach = False
        self.fx.submit = self.submit
        self.fx.admit = self.admit
        self.config = dict(self.fx.config, ego_policy='MATCH_THEN_TEACH')
        self.runtime = AutomaticAnswerRuntime(self.db.path, self.config, manifest=self.fx.fx.manifest_path)
        self.lookup = Mock(run_for_question=Mock(return_value=dict(retrieval_status='NO_RESULTS',
            top_candidates=[], lookup_key='ANONYMOUS_SEARCH_FIXTURE')))
        stub = patch('helpdesk.reference_lookup.ReferenceLookup', return_value=self.lookup)
        stub.start()
        self.addCleanup(stub.stop)

    def admit(self):
        return request_enqueue(self.db, self.fx.task['id'], self.fx.fx.manifest_path,
                               ego_space=1, ego_policy='MATCH_THEN_TEACH')

    def submit(self, request_id, url, prompt, *, attachments, capture_only, new_session=False):
        task = self.fx.fx.tasks.get_task(self.fx.task['id'])
        directory = Path(task['evidence_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        proof = directory / (request_id + '.answer.json')
        if proof.is_file() and capture_only:
            return json.loads(proof.read_text(encoding='utf-8'))
        files = [dict(path=str(p), name=p.name, sha256=sha256(p.read_bytes()).hexdigest()) for p in attachments]
        request = dict(space_id=1, page='p1', session_url=url, prompt=prompt, files=files, response_token=None)
        if new_session:
            request['new_session'] = True
        state = dict(phase='SUBMITTED',
                     fingerprint=sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        state_path = directory / (request_id + '.state.json')
        self.calls.append(dict(id=request_id, url=url, prompt=prompt, files=files, new=new_session))
        if self.correct_during_teach and request_id.endswith('-teach'):
            self.correct_during_teach = False
            self.correct_original()
        if self.failure == 'before_capture':
            state_path.write_text(json.dumps(state), encoding='utf-8')
            raise TimeoutError('anonymous submitted-unknown fixture')
        resolved = 'https://chat.deepseek.com/a/chat/s/anonymous-' + task['run_id'] if new_session else url
        result = dict(adapter='EGO_LITE', space_id=1, page='p1', session_url=resolved, response_token=None,
            complete=True, completion_evidence='NEW_ASSISTANT_AND_READ_ALOUD_CONTROL', upload_manifest=files,
            text=json.dumps(self.model) if request_id.endswith('-match') else '第12题选C，因为要照顾母亲。',
            delivered=False, formal_statistics_eligible=False)
        raw = json.dumps(result, ensure_ascii=False).encode()
        proof.write_bytes(raw)
        state.update(phase='CAPTURED', output_sha256=sha256(raw).hexdigest(), resolved_session_url=resolved)
        state_path.write_text(json.dumps(state), encoding='utf-8')
        if self.failure == 'after_capture':
            raise TimeoutError('anonymous crash after native capture')
        return result

    def test_match_then_teach_share_original_session_without_premature_completion(self):
        self.admit()
        result = self.runtime.tick()
        self.assertEqual(result['state'], 'READY_FOR_GENERATION', result)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['id'].endswith('-match'))
        self.assertEqual(self.calls[0]['files'], [])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)
        result = self.runtime.tick()
        self.assertEqual(result['state'], 'GENERATED', result)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.calls[1]['new'])
        self.assertIn('题面核验结果', self.calls[1]['prompt'])
        self.assertEqual([f['path'] for f in self.calls[1]['files']], [str(self.fx.fx.course)])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE purpose='ANSWER'")[0], 'PENDING')
        kinds = [json.loads(r[0])['kind'] for r in self.db.all("SELECT details FROM audit WHERE event='ANSWER_CALL_STARTED'")]
        self.assertEqual(kinds, ['DEEPSEEK_MATCH', 'DEEPSEEK_TEACH'])
        self.runtime.tick()
        self.assertEqual(len(self.calls), 2)

    def test_ordinary_followup_uses_origin_match_and_does_not_search_or_reupload(self):
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.assertEqual(self.runtime.tick()['state'], 'GENERATED')
        self.fx.followup()
        result = self.runtime.tick()
        self.assertEqual(result['state'], 'GENERATED', result)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1]['files'], [])
        self.assertFalse(self.calls[-1]['new'])
        self.lookup.run_for_question.assert_called_once()
        self.assertNotIn('此前实际回复', self.calls[-1]['prompt'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_captured_match_can_recover_without_resubmission_after_restart(self):
        self.admit()
        self.failure = 'after_capture'
        result = self.runtime.tick()
        self.assertEqual(result['state'], 'EXECUTION_UNCERTAIN', result)
        self.assertEqual(len(self.calls), 1)
        self.failure = None
        restarted = AutomaticAnswerRuntime(self.db.path, self.config, manifest=self.fx.fx.manifest_path)
        self.assertEqual(restarted.tick()['state'], 'GENERATED')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_EGO_CALL_RECOVERED'")[0], 1)

    def test_unknown_match_or_unresolved_result_never_teaches_or_resubmits(self):
        self.admit()
        self.failure = 'before_capture'
        self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.runtime.tick()
        restarted = AutomaticAnswerRuntime(self.db.path, self.config, manifest=self.fx.fx.manifest_path)
        restarted.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def test_captured_teaching_recovers_after_restart_without_another_request(self):
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.failure = 'after_capture'
        self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(len(self.calls), 2)
        self.failure = None
        restarted = AutomaticAnswerRuntime(self.db.path, self.config, manifest=self.fx.fx.manifest_path)
        self.assertEqual(restarted.tick()['state'], 'GENERATED')
        self.assertEqual(restarted.tick()['state'], 'IDLE')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_unresolved_matching_result_stops_before_teaching(self):
        self.model = dict(STUDENT_ONLY, match_status='UNRESOLVED', unresolved_fields=['unclear original'])
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.runtime.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def test_correction_after_matching_stops_old_teaching(self):
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.correct_original()
        self.assertEqual(self.runtime.tick()['state'], 'IDLE')
        queued = get(self.db, self.fx.task['id'])
        self.assertEqual(queued['phase'], 'NEEDS_ATTENTION')
        self.assertEqual(queued['last_error'], 'AUTHORITATIVE_RUN_STALE')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def correct_original(self):
        task = self.fx.fx.tasks.get_task(self.fx.task['id'])
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (task['run_id'],))[0])
        question = replace(Question.from_dict(snapshot['student_question']), raw_stem='Why did John NOT go home?',
                           verified_stem='Why did John NOT go home?')
        Helpdesk(self.db).ingest(Incoming(self.fx.fx.binding, 'synthetic corrected original', Intent.CORRECTION,
                                         question_id=task['question_id'], verified_question=question))

    def test_original_image_is_uploaded_only_in_matching_stage(self):
        from PIL import Image
        photo = self.base / 'anonymous-original.png'
        Image.new('RGB', (20, 20), 'white').save(photo)
        _, receipt, outcome = self.fx.fx.receive('two-stage-photo-fixture', photo=photo)
        self.fx.task = self.fx.fx.review(self.fx.fx.create(receipt))
        self.fx.fx.confirm_fixture_ack(outcome.message_id)
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.assertEqual([f['path'] for f in self.calls[0]['files']], [str(photo)])
        self.assertEqual(self.runtime.tick()['state'], 'GENERATED')
        self.assertEqual([f['path'] for f in self.calls[1]['files']], [str(self.fx.fx.course)])

    def test_real_candidate_ids_and_program_match_reach_teaching(self):
        from helpdesk.reference_resolution import ReferenceResolution
        self.admit()
        task = self.fx.fx.tasks.get_task(self.fx.task['id'])
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (task['run_id'],))[0])
        cid = ReferenceResolution(self.db).add(snapshot['question_version'], 'SELF_AUTHORED_FIXTURE',
            Question.from_dict(snapshot['student_question']), snapshot['student_material'], 'ANONYMOUS_FIXTURE',
            provenance={'source_policy':{'business_record_storage_allowed':True}})[0]
        self.lookup.run_for_question.return_value.update(retrieval_status='CANDIDATES_FOUND',
                                                         top_candidates=[{'candidate_id':cid}])
        self.model = dict(match_status='MATCH', selected_candidate=cid, relation=['SAME_CONTENT'],
            differences=['FORMATTING_ONLY'], option_mapping=dict(zip('ABCD','ABCD')), unresolved_fields=[])
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.assertIn(cid, self.calls[0]['prompt'])
        self.assertEqual(self.runtime.tick()['state'], 'GENERATED')
        self.assertIn('VERIFIED_REFERENCE', self.calls[1]['prompt'])

    def test_correction_during_teaching_keeps_capture_but_creates_no_old_outbox(self):
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.correct_during_teach = True
        self.assertEqual(self.runtime.tick()['state'], 'NEEDS_ATTENTION')
        task = self.fx.fx.tasks.get_task(self.fx.task['id'])
        capture = Path(task['evidence_dir']) / (task['run_id'] + '-teach.answer.json')
        self.assertTrue(capture.is_file())
        self.assertEqual(json.loads(capture.read_text(encoding='utf-8'))['text'], '第12题选C，因为要照顾母亲。')
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def test_frozen_policy_cannot_be_silently_changed(self):
        self.admit()
        with self.assertRaisesRegex(ValueError, 'BACKEND_CHANGED'):
            request_enqueue(self.db, self.fx.task['id'], self.fx.fx.manifest_path, ego_space=1)
        changed = AutomaticAnswerRuntime(self.db.path, self.fx.config, manifest=self.fx.fx.manifest_path)
        self.assertEqual(changed.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.calls, [])

    def test_two_stage_policy_without_ego_browser_is_not_silently_ignored(self):
        from helpdesk.reviewed_question_queue import enqueue
        with self.assertRaises(ValueError):
            enqueue(self.db, self.fx.task['id'], self.fx.fx.manifest_path, ego_policy='MATCH_THEN_TEACH')
        self.assertIsNone(self.fx.fx.tasks.get_task(self.fx.task['id'])['run_id'])

    def test_unavailable_browser_preflight_does_not_count_a_model_call(self):
        self.admit()
        with patch('helpdesk.ego_generation.EgoDeepSeek', side_effect=ValueError('anonymous missing browser')):
            self.assertEqual(self.runtime.tick()['state'], 'EXECUTION_UNCERTAIN')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_CALL_STARTED'")[0], 0)

    def test_bounded_teaching_revision_keeps_matched_policy_and_reuses_match(self):
        from helpdesk.ego_generation import run_existing
        from helpdesk.ego_revision import prepare_revision
        from helpdesk.storage import encode, now
        self.admit()
        self.assertEqual(self.runtime.tick()['state'], 'READY_FOR_GENERATION')
        self.assertEqual(self.runtime.tick()['state'], 'GENERATED')
        task = self.fx.fx.tasks.get_task(self.fx.task['id'])
        parent = task['run_id']
        body = self.db.one('SELECT text FROM answers')[0]
        # Synthetic checker outcome, not a claim that real ANSWER rejected this text.
        check = dict(status='REVIEW_REQUIRED', draft_sha256=sha256(body.encode()).hexdigest(),
                     output={'automatic_flags':[{'message':'anonymous quote fixture', 'excerpt':body}]})
        with self.db.transaction():
            self.db.execute("UPDATE runs SET state='REJECTED',error='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW' WHERE id=?", (parent,))
            self.db.execute("UPDATE answers SET state='REJECTED'")
            self.db.execute("UPDATE outbox SET state='STALE' WHERE purpose='ANSWER'")
            self.db.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                            (parent,'TEACHING_DRAFT_CHECKED',encode(check),now()))
        child = prepare_revision(self.db, parent)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (child,))[0])
        self.assertEqual(snapshot['ego_policy'], 'MATCH_THEN_TEACH')
        result = run_existing(self.db, child, 1, task['evidence_dir'])
        self.assertEqual(result['state'], 'GENERATED', result)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1]['files'], [])
        self.lookup.run_for_question.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'BUDGET_EXHAUSTED|REJECTED_PARENT_REQUIRED'):
            prepare_revision(self.db, child)
