"""Anonymous identity/review/generation checks; no real clients or teaching uploads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.domain import Intent, Option, Question, compare
from helpdesk.mcp_generation import input_fingerprint
from helpdesk.mcp_preparation import _files, prepare_question_text
from helpdesk.reference_resolution import ReferenceResolution, reference_input, validate_reference_snapshot
from helpdesk.service import Helpdesk, Incoming
from helpdesk.session_isolation import claim_deepseek_chat
from helpdesk.storage import Store, encode
from helpdesk.workflow import Workflow
from tools.lookup_reference import demo_snapshot


class ReferenceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Store(self.root / 'anonymous.db')
        self.app = Helpdesk(self.db)
        self.resolution = ReferenceResolution(self.db)
        self.data = demo_snapshot()
        self.student = Question.from_dict(self.data['student_question'])
        self.material = self.data['student_material']
        self.binding = self.app.bind('anonymous-English', 'anonymous-student', '匿名学生', verified=True)
        self.first = self.ingest(self.student)
        self.version = self.current()['current_version']
        self.provenance = {'source_url': 'https://fixtures.invalid/question/1',
            'retrieved_at': '2026-10-01T15:00:00+00:00',
            'source_policy': {'kind': 'SELF_AUTHORED_OFFLINE', 'business_record_storage_allowed': True}}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def ingest(self, question, **kwargs):
        return self.app.ingest(Incoming(self.binding, '匿名题面', Intent.NEW, source='anonymous-fixture',
            verified_question=question, raw_material=self.material, verified_material=self.material, **kwargs))

    def current(self):
        return self.db.one('SELECT * FROM questions WHERE id=?', (self.first.question_id,))

    def add(self, reference=None, **kwargs):
        return self.resolution.add(self.version, 'anonymous-reference', reference or self.student,
            self.material, 'SELF_AUTHORED_FIXTURE', provenance=kwargs.pop('provenance', self.provenance), **kwargs)

    def review(self, candidate_id, *, decision='confirm', consume=False, reviewer='匿名核对人'):
        q = self.current()
        return self.resolution.review(candidate_id, question_version=q['current_version'], context_revision=q['context_revision'],
            reviewer=reviewer, reason='逐项核对学生原文、题干、四个选项及来源', decision=decision, consume=consume)

    def mock_result(self, run):
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        return {'text': '第34题选A，因为题面说明带毯子帮助避难者。',
            'correct_option_id': snapshot['student_question']['options'][0]['id'], 'complete': True,
            'uploads_confirmed': True, 'session_id': snapshot['session_id'], 'simulated': True}

    def test_01_complete_exact_content_is_a_candidate_not_a_confirmed_reference(self):
        candidate, comparison = self.add()
        self.assertEqual(comparison.resolution_status, 'MATCH_CANDIDATE')
        self.assertEqual(comparison.relation, ('SAME_CONTENT',))
        self.assertTrue(all(e['relation'] == 'EQUAL' for e in comparison.evidence))
        self.assertEqual(self.resolution.list()[0]['state'], 'MATCHED_CANDIDATE')
        self.assertEqual(self.app.context(self.first.turn_id)['references'], [])
        events = [r[0] for r in self.db.all("SELECT event FROM audit WHERE json_extract(details,'$.candidate_id')=? ORDER BY id", (candidate,))]
        self.assertEqual(events, ['REFERENCE_CANDIDATE_DISCOVERED', 'REFERENCE_CANDIDATE_COMPARING', 'REFERENCE_CANDIDATE_COMPARED'])

    def test_02_missing_option_is_incomplete_and_cannot_be_confirmed(self):
        candidate, comparison = self.add(replace(self.student, options=self.student.options[:3]))
        self.assertEqual(comparison.resolution_status, 'INCOMPLETE')
        self.assertEqual(self.resolution.list()[0]['state'], 'DISCOVERED')
        self.assertTrue(any(e['field'] == 'option:D' and e['relation'] == 'UNKNOWN' for e in comparison.evidence))
        with self.assertRaisesRegex(ValueError, 'Complete unambiguous'):
            self.review(candidate)
        self.assertFalse(self.resolution.list()[0]['can_confirm'])

    def test_03_reordered_option_content_maps_reference_B_to_student_D(self):
        order = (0, 3, 2, 1)
        reference = replace(self.student, options=tuple(Option.confirmed(label,
            self.student.options[index].verified_text, position, 'anonymous-reference')
            for position, (label, index) in enumerate(zip('ABCD', order))))
        candidate, comparison = self.add(reference)
        self.assertIn('OPTION_REORDER', comparison.relation)
        mapping = dict(comparison.option_mapping)
        self.assertEqual(mapping[reference.options[1].id], self.student.options[3].id)
        evidence = next(e for e in comparison.evidence if e['field'] == 'option:D')
        self.assertEqual((evidence['reference_label'], evidence['student_label'], evidence['relation']), ('B', 'D', 'EQUAL'))
        self.review(candidate, consume=True)
        self.assertEqual(reference_input(self.app.context(self.first.turn_id))[0]['option_mapping'],
                         [list(pair) for pair in comparison.option_mapping])

    def test_04_number_change_is_same_content_and_never_creates_a_new_question(self):
        _, comparison = self.add(replace(self.student, number='12'))
        self.assertIn('QUESTION_NUMBER_CHANGED', comparison.relation)
        self.assertEqual(comparison.resolution_status, 'MATCH_CANDIDATE')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM questions')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_05_NOT_or_EXCEPT_change_is_a_high_risk_stem_difference(self):
        for word in ('NOT', 'EXCEPT'):
            stem = self.student.verified_stem + ' ' + word
            comparison = compare('r', replace(self.student, verified_stem=stem, raw_stem=stem), self.material,
                                 self.version, self.student, self.material)
            self.assertIn('STEM_CHANGED', comparison.relation)
            self.assertIn('NOT_DIFFERENCE', comparison.relation)
            self.assertEqual(comparison.resolution_status, 'MISMATCH')
            self.assertTrue(any(d['kind'] == 'KEY_CONDITION_CONFLICT' for d in comparison.field_differences))

    def test_06_numeric_and_scope_changes_require_reconfirmation(self):
        for before, after in (('How many blankets were needed: 12?', 'How many blankets were needed: 21?'),
                              ('Choose a value above ten.', 'Choose a value below ten.'),
                              ('Which value is between 10 and 20?', 'Which value is between 20 and 10?'),
                              ('Did 12 students use 21 blankets?', 'Did 21 students use 12 blankets?')):
            reference = replace(self.student, raw_stem=before, verified_stem=before)
            student = replace(self.student, raw_stem=after, verified_stem=after)
            comparison = compare('r', reference, self.material, 's', student, self.material)
            self.assertIn('CONDITION_CHANGED', comparison.relation)
            self.assertTrue(any(d['kind'] == 'KEY_CONDITION_CONFLICT' for d in comparison.field_differences))
            self.assertFalse(comparison.option_mapping)

    def test_07_wrong_candidate_is_rejected_and_cannot_be_overridden(self):
        wrong = replace(self.student, verified_stem='Where did the family spend the winter?')
        candidate, comparison = self.add(wrong)
        self.assertEqual(comparison.resolution_status, 'MISMATCH')
        self.assertEqual(self.resolution.list()[0]['state'], 'REJECTED')
        with self.assertRaises(ValueError):
            self.review(candidate, consume=True)
        self.assertEqual(self.app.context(self.first.turn_id)['references'], [])

    def test_08_retrieval_replay_with_new_option_ids_keeps_one_candidate(self):
        first, _ = self.add()
        reference = replace(self.student, options=tuple(Option.confirmed(o.label, o.verified_text, o.order, o.source)
                                                      for o in self.student.options))
        second, _ = self.add(reference)
        self.assertEqual(first, second)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
        def add_concurrently():
            db = Store(self.db.path)
            try:
                return ReferenceResolution(db).add(self.version, 'r-again', reference, self.material,
                    'SELF_AUTHORED_FIXTURE', provenance=self.provenance)[0]
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: add_concurrently(), range(2))), [first, first])

    def test_retrieval_refreshes_legacy_rejection_evidence_without_confirming_it(self):
        import re
        from helpdesk.domain import CONDITION_PATTERN
        self.student = replace(self.student, raw_stem='Did 21 students use 12 blankets?',
                               verified_stem='Did 21 students use 12 blankets?')
        self.first = self.ingest(self.student)
        self.version = self.current()['current_version']
        reference = replace(self.student, raw_stem='Did 12 students use 21 blankets?',
                            verified_stem='Did 12 students use 21 blankets?')
        with patch('helpdesk.domain._conditions',
                   lambda text: sorted(re.findall(CONDITION_PATTERN, (text or '').casefold()))):
            candidate, previous = self.add(reference)
        refreshed, _ = self.add(reference)
        view = self.resolution.list()[0]
        self.assertEqual(refreshed, candidate)
        self.assertTrue(any(d['kind'] == 'KEY_CONDITION_CONFLICT'
                            for d in view['comparison_result']['field_differences']))
        self.assertEqual(view['state'], 'REJECTED')
        self.assertFalse(view['can_confirm'])
        self.assertFalse(view['consumption_enabled'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        event = self.db.one("SELECT details FROM audit WHERE event='REFERENCE_CANDIDATE_COMPARISON_REFRESHED'")
        evidence = json.loads(event['details'])
        self.assertEqual(evidence['previous_comparison']['field_differences'], list(previous.field_differences))
        self.add(reference)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_CANDIDATE_COMPARISON_REFRESHED'")[0], 1)

    def test_duplicate_reference_does_not_replace_a_confirmed_comparison(self):
        candidate, comparison = self.add()
        original = self.review(candidate, consume=True)
        with patch('helpdesk.reference_resolution.compare',
                   return_value=replace(comparison, reason='Anonymous changed comparison rule')):
            with self.assertRaisesRegex(ValueError, 'Confirmed reference comparison changed'):
                self.add()
        current = self.resolution.list()[0]
        self.assertEqual(current['confirmed_by'], original['confirmed_by'])
        self.assertEqual(current['comparison_result'], original['comparison_result'])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_CANDIDATE_COMPARISON_REFRESHED'")[0], 0)

    def test_09_manual_confirmation_records_original_actor_and_does_not_consume_shadow(self):
        candidate, _ = self.add()
        before = self.review(candidate)
        again = self.review(candidate, reviewer='另一个匿名核对人')
        self.assertEqual(before['state'], 'CONFIRMED')
        self.assertEqual(again['confirmed_by'], before['confirmed_by'])
        self.assertEqual(again['confirmed_at'], before['confirmed_at'])
        self.assertFalse(before['consumption_enabled'])
        self.assertEqual(self.app.context(self.first.turn_id)['references'], [])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_CANDIDATE_CONFIRMED'")[0], 1)

    def test_10_confirmed_consumable_reference_reaches_frozen_generation_input(self):
        before = self.app.context(self.first.turn_id)
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        flow = Workflow(self.db)
        run = flow.start(self.first.turn_id)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        reference = reference_input(snapshot)[0]
        self.assertEqual(reference['candidate_id'], candidate)
        self.assertEqual(reference['state'], 'CONFIRMED')
        self.assertEqual(reference['reference_material'], self.material)
        self.assertEqual(encode(snapshot['student_question']), encode(before['student_question']))
        self.assertNotIn('reference_answers', reference)
        prepared = prepare_question_text(snapshot, self.root / 'prepared')
        body = json.loads(Path(prepared['path']).read_text(encoding='utf-8'))
        self.assertEqual(body['confirmed_references'][0], reference)
        self.assertNotEqual(input_fingerprint(snapshot), input_fingerprint({**snapshot, 'references': []}))
        result = flow.finish(run, self.mock_result(run))
        self.assertEqual(result['state'], 'GENERATED')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual(self.db.one('SELECT MAX(version) FROM schema_migrations')[0], 6)

    def test_unverified_OCR_condition_is_observation_not_confirmed_change(self):
        reference = replace(self.student, raw_stem='It has NOT 12 blankets.', verified_stem=None,
                            uncertain_fields=('stem',))
        _, comparison = self.add(reference)
        self.assertEqual(comparison.resolution_status, 'INCOMPLETE')
        self.assertNotIn('CONDITION_CHANGED', comparison.relation)
        self.assertNotIn('NOT_DIFFERENCE', comparison.relation)

    def test_candidate_exposes_original_observation_without_promoting_raw_OCR(self):
        raw_question = replace(self.student, raw_stem='OCR: not clearly identified',
            verified_stem=None, uncertain_fields=('stem',))
        outcome = self.app.ingest(Incoming(self.binding, '匿名补图请求', Intent.NEW,
            source='anonymous-fixture', verified_question=raw_question,
            raw_material='OCR observation only', verified_material=None,
            source_sent_at='2026-09-30T22:58:00+08:00', observed_at='2026-09-30T23:05:00+08:00',
            source_time_evidence={'source': 'operator_verified_original', 'message_locator': 'anonymous-ocr-fixture',
                                  'evidence': 'Explicit synthetic boundary time; no real message modified'}))
        version = self.db.one('SELECT current_version FROM questions WHERE id=?', (outcome.question_id,))[0]
        candidate, comparison = self.resolution.add(version, 'reference-ocr', self.student, self.material,
            'SELF_AUTHORED_FIXTURE', provenance=self.provenance)
        tables = ('messages', 'question_versions', 'material_versions', 'audit')
        before = {table: [tuple(row) for row in self.db.all('SELECT * FROM ' + table)] for table in tables}
        view = self.resolution.list(outcome.question_id)[0]
        evidence = view['student_evidence']
        self.assertEqual(evidence['source_message_id'], outcome.message_id)
        self.assertEqual(evidence['raw_material'], 'OCR observation only')
        self.assertEqual(evidence['raw_stem'], raw_question.raw_stem)
        self.assertEqual(evidence['student_sent_at'], '2026-09-30T22:58:00+08:00')
        self.assertEqual(evidence['collected_at'], '2026-09-30T23:05:00+08:00')
        self.assertEqual(evidence['images'], [])
        self.assertEqual(comparison.resolution_status, 'INCOMPLETE')
        self.assertFalse(view['can_confirm'])
        self.assertEqual(before, {table: [tuple(row) for row in self.db.all('SELECT * FROM ' + table)] for table in tables})

    def test_blank_material_and_ambiguous_options_do_not_match(self):
        self.assertEqual(compare('r', self.student, '', 's', self.student, '').resolution_status, 'INCOMPLETE')
        reference = replace(self.student, options=tuple(Option.confirmed(label, 'Repeated identical answer.', i, 'r')
                                                       for i, label in enumerate('ABCD')))
        candidate, comparison = self.add(reference)
        self.assertEqual(comparison.resolution_status, 'UNKNOWN')
        with self.assertRaises(ValueError):
            self.review(candidate)

    def test_pending_student_correction_blocks_confirmation_at_current_context(self):
        candidate, _ = self.add()
        correction = self.app.ingest(Incoming(self.binding, '发错了，稍后补图', Intent.CORRECTION,
            quote_message_id=self.first.message_id))
        self.assertEqual(correction.status, 'NEEDS_REVIEW')
        self.assertTrue(self.resolution.list()[0]['input_pending_review'])
        with self.assertRaisesRegex(ValueError, 'Unresolved student input'):
            self.review(candidate)

    def test_content_tampering_is_detected_before_confirmation_and_upload(self):
        candidate, _ = self.add()
        row = self.db.one('SELECT payload FROM reference_candidates WHERE id=?', (candidate,))
        changed = json.loads(row[0]);changed['verified_stem'] += ' NOT'
        self.db.execute('UPDATE reference_candidates SET payload=? WHERE id=?', (encode(changed), candidate))
        with self.assertRaisesRegex(ValueError, 'content changed'):
            self.review(candidate, consume=True)
        with self.assertRaisesRegex(ValueError, 'Stored candidate evidence changed'):
            self.add()

    def test_content_tampering_after_confirmation_stops_generation_context(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        self.db.execute('UPDATE reference_candidates SET material_text=? WHERE id=?', ('Different material.', candidate))
        with self.assertRaisesRegex(ValueError, 'content changed'):
            self.app.context(self.first.turn_id)
        with self.assertRaisesRegex(ValueError, 'Stored candidate evidence changed'):
            self.add()

    def test_immutable_student_version_and_altered_frozen_input_are_rejected(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        changed = self.student.to_dict();changed['verified_stem'] += ' NOT'
        import sqlite3
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable question version'):
            self.db.execute('UPDATE question_versions SET payload=? WHERE id=?', (encode(changed), self.version))
        snapshot = self.app.context(self.first.turn_id)
        snapshot['student_question'] = changed
        with self.assertRaisesRegex(ValueError, 'frozen student input'):
            reference_input(snapshot)

    def test_rejected_confirmation_blocks_web_upload_and_late_generated_result(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        flow = Workflow(self.db)
        run = flow.start(self.first.turn_id)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        self.review(candidate, decision='reject')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CONFIRMATION_CHANGED'):
            claim_deepseek_chat(snapshot, 'https://chat.deepseek.com/a/chat/s/anonymous-identity')
        result = flow.finish(run, self.mock_result(run))
        self.assertEqual(result['state'], 'REJECTED')
        self.assertEqual(result['reason'], 'REFERENCE_CONFIRMATION_CHANGED')
        self.assertIsNone(result['outbox_id'])

    def test_rejected_confirmation_blocks_existing_answer_approval(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        flow = Workflow(self.db)
        run = flow.start(self.first.turn_id)
        finished = flow.finish(run, self.mock_result(run))
        self.review(candidate, decision='reject')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CONFIRMATION_CHANGED'):
            flow.approve(finished['outbox_id'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_rejected_reference_after_approval_blocks_mock_dispatch_before_a_receipt(self):
        candidate, _ = self.add();self.review(candidate, consume=True)
        flow = Workflow(self.db)
        run = flow.start(self.first.turn_id)
        finished = flow.finish(run, self.mock_result(run));flow.approve(finished['outbox_id'])
        self.review(candidate, decision='reject')
        self.assertEqual(flow.dispatch(finished['outbox_id']), 'STALE')
        self.assertEqual(flow.desktop.receipts(), [])
        self.assertEqual(self.db.one('SELECT last_error FROM outbox WHERE id=?', (finished['outbox_id'],))[0],
            'REFERENCE_CONFIRMATION_CHANGED')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_capture_cli_registers_candidate_on_existing_question_without_confirmation(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from helpdesk.reference_lookup import LookupConfig
        from tools.crawl_reference import main
        config = LookupConfig(enabled=True, fixture_root=Path(__file__).parent / 'fixtures/reference_lookup',
            cache_root=self.root / 'capture-cache')
        stream = StringIO()
        with patch('tools.crawl_reference.LookupConfig.load', return_value=config), redirect_stdout(stream):
            result = main(['--local-file', 'demo.html', '--db', self.db.path, '--question-id', self.first.question_id,
                '--question-version', self.version, '--context-revision', str(self.current()['context_revision'])])
        self.assertEqual(result, 0)
        output = json.loads(stream.getvalue())
        self.assertFalse(output['confirmation_performed'])
        self.assertEqual(len(output['candidate_ids']), 1)
        self.assertEqual(self.resolution.list()[0]['state'], 'MATCHED_CANDIDATE')
        self.assertEqual(self.app.context(self.first.turn_id)['references'], [])

    def test_restart_preserves_confirmation_and_ordinary_followup_keeps_reference(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        self.db.close();self.db = Store(self.root / 'anonymous.db');self.app = Helpdesk(self.db)
        self.resolution = ReferenceResolution(self.db)
        follow = self.app.ingest(Incoming(self.binding, '为什么不选B？', Intent.FOLLOWUP,
            quote_message_id=self.first.message_id))
        snapshot = self.app.context(follow.turn_id)
        self.assertEqual(reference_input(snapshot)[0]['candidate_id'], candidate)
        validate_reference_snapshot(self.db, snapshot)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)

    def test_missing_storage_permission_cannot_be_consumed_and_transaction_rolls_back(self):
        candidate, _ = self.add(provenance={'source_policy': {'business_record_storage_allowed': False}})
        with self.assertRaisesRegex(ValueError, 'approved internal reference use'):
            self.review(candidate, consume=True)
        self.assertEqual(self.resolution.list()[0]['state'], 'MATCHED_CANDIDATE')
        self.assertFalse(self.db.one("SELECT id FROM audit WHERE event='REFERENCE_CANDIDATE_CONFIRMED'"))

    def test_legacy_reference_remains_unconfirmed_and_cannot_be_silently_promoted(self):
        comparison = compare('legacy', self.student, self.material, self.version, self.student, self.material)
        from dataclasses import asdict
        self.db.execute('''INSERT INTO reference_candidates VALUES(?,?,?,?,?,?,?,?)''',
            ('legacy', self.version, 'legacy-version', encode(self.student.to_dict()), self.material,
             encode(asdict(comparison)), 'historical-source', '2026-09-17T00:00:00+00:00'))
        self.assertEqual(self.app.context(self.first.turn_id)['references'], [])
        with self.assertRaisesRegex(ValueError, 'Historical reference'):
            self.review('legacy', consume=True)

    def test_confirmed_reference_text_is_also_prepared_when_original_images_exist(self):
        candidate, _ = self.add()
        self.review(candidate, consume=True)
        snapshot = self.app.context(self.first.turn_id)
        course = self.root / 'course.md';course.write_text('Anonymous input fixture, not a teaching rule.', encoding='utf-8')
        image = self.root / 'original.png';image.write_bytes(b'anonymous-image-fixture')
        snapshot['teaching_skills'] = [{'path': str(course), 'sha256': sha256(course.read_bytes()).hexdigest()}]
        snapshot['attachments'] = [{'path': str(image), 'sha256': sha256(image.read_bytes()).hexdigest()}]
        prepared = prepare_question_text(snapshot, self.root / 'prepared')
        files = _files(snapshot, question_text_path=prepared['path'])
        self.assertEqual([f['kind'] for f in files], ['course', 'question_image', 'question_text'])

    @patch('helpdesk.mcp_preparation.verify_frozen_teaching', return_value={})
    @patch('helpdesk.mcp_generation.verify_frozen_teaching', return_value={})
    def test_confirmed_candidate_completes_strict_and_fast_preparation_for_text_and_images(self, *_source_mocks):
        from helpdesk.mcp_generation import PreparedDeepSeekGenerator
        from helpdesk.mcp_preparation import DeepSeekSessionPreparer, question_text_fields
        from helpdesk.mcp_preparation_review import review_preparation, TEXT_REVIEW_STATEMENT
        from tests.test_mcp_preparation import FakeDesktop, URL
        from tests.test_mcp_generation import Transport, URL as GENERATION_URL
        course = self.root / 'anonymous-course.md'
        excerpt = 'Anonymous course input fixture; no real teaching rules or uploads.'
        course.write_text(excerpt, encoding='utf-8')
        image = self.root / 'original.png';image.write_bytes(b'anonymous-image-fixture')
        for with_images in (False, True):
            for mode in ('STRICT_READBACK', 'FAST_UPLOAD_THEN_GENERATE'):
                with self.subTest(with_images=with_images, mode=mode):
                    attachments = ({'path': str(image), 'sha256': sha256(image.read_bytes()).hexdigest()},) if with_images else ()
                    self.first = self.ingest(self.student, attachments=attachments)
                    self.version = self.current()['current_version']
                    candidate, _ = self.add();self.review(candidate, consume=True)
                    flow = Workflow(self.db, teaching_paths=(course,))
                    run = flow.start(self.first.turn_id)
                    snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
                    folder = self.root / ('images-' + str(with_images) + '-' + mode);folder.mkdir()
                    candidate_path = folder / 'candidate.json';review_path = folder / 'review.json'
                    approved_path = folder / 'approved.json';proof = folder / 'proof.json'
                    class MatchingUpload(FakeDesktop):
                        def call(self, tool, args):
                            result = super().call(tool, args)
                            for item in result.get('content', []):
                                if item.get('type') == 'text':
                                    item['text'] = item['text'].replace('1234567890123456', run).replace(
                                        'What did the character do after school?', snapshot['student_question']['verified_stem'])
                            return result
                    url = URL + '-' + run
                    class BoundUpload(MatchingUpload):
                        def call(self, tool, args):
                            result = super().call(tool, args)
                            for item in result.get('content', []):
                                if item.get('type') == 'text':
                                    item['text'] = item['text'].replace(URL, url)
                            return result
                    fake = BoundUpload([{'name': course.name, 'content': excerpt}])
                    controls = {'upload_button': 'Upload files', 'picker_window': 'Open', 'file_input': 'File name',
                        'open_button': 'Open', 'preparation_mode': mode}
                    record = DeepSeekSessionPreparer(fake, snapshot, url, candidate_path, controls, poll_interval=0, timeout=2).run()
                    self.assertEqual(record['files'][-1]['kind'], 'question_text')
                    self.assertIn(record['files'][-1]['name'], fake.uploaded)
                    review = {'reviewer': '独立匿名核对人', 'source_verified_excerpts': {str(course): excerpt},
                        'verified_question_stem': snapshot['student_question']['verified_stem']}
                    if with_images:
                        review.update(reviewed_image_hashes={str(image): sha256(image.read_bytes()).hexdigest()},
                            image_review_statement='I inspected all frozen question images and verified the question stem.')
                    else:
                        review.update(reviewed_question_text=question_text_fields(snapshot),
                            reviewed_input_fingerprint=input_fingerprint(snapshot), question_text_review_statement=TEXT_REVIEW_STATEMENT,
                            source_review_evidence='明确标注的本地匿名题面与原始输入核对')
                    review_path.write_text(json.dumps(review), encoding='utf-8')
                    prepared = review_preparation(snapshot, candidate_path, review_path, approved_path, proof)
                    self.assertIn('question_text_file', prepared)
                    class BoundGeneration(Transport):
                        def call(self, tool, args):
                            result = super().call(tool, args)
                            for item in result.get('content', []):
                                if item.get('type') == 'text':
                                    item['text'] = item['text'].replace(GENERATION_URL, url).replace('1234567890123456', run)
                            return result
                    transport = BoundGeneration()
                    result = PreparedDeepSeekGenerator(transport, approved_path, folder / 'generation', timeout=2, poll_interval=0).generate(snapshot)
                    self.assertEqual(result['correct_option_id'], self.student.options[0].id)
                    self.assertIn('已经人工确认的参考题及选项映射', transport.prompt)
                    self.assertEqual(transport.calls.count('Shortcut'), 1)
                    # Fake transport metadata is never recorded as a real business receipt.
                    result['simulated'] = True
                    result['adapter'] = flow.generation_adapter.identity
                    completed = flow.finish(run, result)
                    self.assertEqual(completed['state'], 'GENERATED', completed)
                    self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)


if __name__ == '__main__':
    unittest.main()
