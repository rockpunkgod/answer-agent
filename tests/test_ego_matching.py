"""Native ego proof fixtures only; no browser, account, sending or live claims."""
from hashlib import sha256
import json
from pathlib import Path
import unittest

from helpdesk import question_matching as matching
from helpdesk.storage import Store, encode
from tests import test_question_matching as matching_fixture
from tests.test_question_matching import STUDENT_ONLY, URL


class EgoMatchingProofTests(unittest.TestCase):
    def setUp(self):
        self.fx = matching_fixture.MatchingTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.db, self.base = self.fx.db, self.fx.base
        self.outbox_before = self.db.one('SELECT COUNT(*) FROM outbox')[0]
        self.snapshot = matching.prepare_input(self.db, self.fx.run, self.fx.lookup())
        self.snapshot.update(ego_policy='MATCH_THEN_TEACH',
            ego_browser=dict(space_id=7, page='p1', evidence_dir=str(self.base)), ego_request_url=URL)
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(self.snapshot), self.fx.run))
        self.capture = self.base / (self.fx.run + '-match.answer.json')

    def begin(self):
        return matching.begin_ego_attempt(self.db, self.snapshot, self.capture)

    def captured(self, attempt, *, change=None, state_change=None, model=None):
        request = attempt['request']
        result = dict(adapter='EGO_LITE', space_id=7, page='p1', session_url=URL,
            response_token=None, text=json.dumps(STUDENT_ONLY if model is None else model),
            complete=True, completion_evidence='NEW_ASSISTANT_AND_READ_ALOUD_CONTROL',
            upload_manifest=request['files'], delivered=False, formal_statistics_eligible=False)
        result.update(change or {})
        raw = encode(result).encode()
        self.capture.write_bytes(raw)
        state = dict(phase='CAPTURED', output_sha256=sha256(raw).hexdigest(), resolved_session_url=URL,
            fingerprint=sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        state.update(state_change or {})
        self.capture.with_name(self.fx.run + '-match.state.json').write_text(encode(state), encoding='utf-8')

    def test_native_capture_uses_original_receipt_and_replays_after_restart(self):
        attempt = self.begin()
        self.captured(attempt)
        saved = matching.record_ego_result(self.db, self.snapshot, self.capture)
        self.assertEqual(matching.record_ego_result(self.db, saved, self.capture), saved)
        reopened = Store(self.db.path)
        try:
            self.assertEqual(matching.validate_receipt(reopened, saved), saved['question_match_result'])
            self.assertEqual(reopened.one("SELECT COUNT(*) FROM audit WHERE event='QUESTION_MATCH_VERIFIED'")[0], 1)
            self.assertEqual(reopened.one('SELECT COUNT(*) FROM outbox')[0], self.outbox_before)
            self.assertEqual(reopened.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)
            self.assertEqual(reopened.one('SELECT COUNT(*) FROM performance_units')[0], 0)
            self.assertEqual(reopened.one('SELECT session_url FROM deepseek_chats')[0], URL)
        finally:
            reopened.close()

    def test_request_is_durable_before_upload_and_cannot_be_restarted(self):
        attempt = self.begin()
        self.assertEqual(attempt['backend'], 'EGO_LITE')
        self.assertFalse(self.capture.exists())
        self.assertEqual(attempt['request']['files'], [])
        self.assertNotIn('teaching_skills', attempt['request']['prompt'])
        with self.assertRaisesRegex(ValueError, 'ATTEMPT_REQUIRES_REVIEW'):
            self.begin()

    def test_captured_state_must_match_request_and_output(self):
        attempt = self.begin()
        for field, value in (('phase', 'SUBMITTED'), ('fingerprint', 'wrong'), ('output_sha256', 'wrong')):
            with self.subTest(field=field):
                self.captured(attempt, state_change={field:value})
                with self.assertRaisesRegex(ValueError, 'NATIVE_CAPTURE_CHANGED'):
                    matching.record_ego_result(self.db, self.snapshot, self.capture)
                self.capture.with_name('matching-' + self.fx.run + '.json').unlink()
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='QUESTION_MATCH_VERIFIED'")[0], 0)

    def test_wrong_space_incomplete_response_and_upload_manifest_cannot_pass(self):
        attempt = self.begin()
        for change in ({'space_id':8}, {'page':'p2'}, {'complete':False},
                       {'upload_manifest':[{'name':'unexpected-teaching.md'}]}):
            with self.subTest(change=change):
                self.captured(attempt, change=change)
                with self.assertRaisesRegex(ValueError, 'NATIVE_CAPTURE_CHANGED'):
                    matching.record_ego_result(self.db, self.snapshot, self.capture)
                self.capture.with_name('matching-' + self.fx.run + '.json').unlink()

    def test_new_chat_requires_actual_chat_url_not_a_blank_page(self):
        self.snapshot['ego_request_url'] = 'https://chat.deepseek.com/'
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(self.snapshot), self.fx.run))
        attempt = self.begin()
        self.assertTrue(attempt['request']['new_session'])
        self.captured(attempt, change={'session_url':'https://chat.deepseek.com/'})
        with self.assertRaises(ValueError):
            matching.record_ego_result(self.db, self.snapshot, self.capture)

    def test_receipt_rechecks_native_state_and_forbids_mcp_relabeling(self):
        attempt = self.begin()
        self.captured(attempt)
        saved = matching.record_ego_result(self.db, self.snapshot, self.capture)
        state = self.capture.with_name(self.fx.run + '-match.state.json')
        state.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'NATIVE_CAPTURE_CHANGED'):
            matching.validate_receipt(self.db, saved)
        self.captured(attempt)
        proof_path = Path(saved['question_match_result']['evidence_path'])
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
        proof.pop('backend')
        proof_path.write_text(encode(proof), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'BACKEND_CHANGED'):
            matching.record_result(self.db, saved, proof_path)

    def test_model_extra_confidence_cannot_replace_structural_validation(self):
        attempt = self.begin()
        self.captured(attempt, model=dict(STUDENT_ONLY, confidence=.99))
        with self.assertRaisesRegex(ValueError, 'SCHEMA_INVALID'):
            matching.record_ego_result(self.db, self.snapshot, self.capture)

    def test_request_uploads_original_only_and_detects_changed_original(self):
        from PIL import Image
        image = self.base / 'synthetic-original.png'
        Image.new('RGB', (8, 8), 'white').save(image)
        self.snapshot['attachments'] = [{'path':str(image), 'sha256':sha256(image.read_bytes()).hexdigest()}]
        self.snapshot['teaching_skills'] = [{'path':str(self.base / 'must-not-read-teaching.md')}]
        self.snapshot['question_match_input']['binding'] = matching._binding(self.snapshot)
        request = matching.ego_request(self.snapshot)
        self.assertEqual([f['path'] for f in request['files']], [str(image)])
        self.assertNotIn('must-not-read-teaching', request['prompt'])
        image.write_bytes(b'changed anonymous image')
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_IMAGE_CHANGED'):
            matching.ego_request(self.snapshot)

    def test_capture_path_is_checked_before_reading_unapproved_content(self):
        wrong = self.base / 'unapproved.answer.json'
        wrong.write_text('this must not be parsed as evidence', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'CAPTURE_PATH_INVALID'):
            matching.record_ego_result(self.db, self.snapshot, wrong)
        self.assertFalse(wrong.with_name('matching-' + self.fx.run + '.json').exists())
        with self.assertRaisesRegex(ValueError, 'CAPTURE_PATH_INVALID'):
            matching.begin_ego_attempt(self.db, self.snapshot, wrong)
