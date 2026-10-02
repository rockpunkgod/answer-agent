"""Synthetic messages/search responses and fake browser only; no real accounts."""
import copy
from dataclasses import replace
from hashlib import sha256
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

from helpdesk.__main__ import demo_question
from helpdesk.automatic_preparation import complete_automatic_preparation
from helpdesk.delivery import MockDesktop
from helpdesk.domain import Intent, Option, Question
from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.operator_tasks import OperatorTasks
from helpdesk import question_matching as matching
from helpdesk.reference_resolution import ReferenceResolution
from helpdesk.service import Helpdesk, Incoming
from helpdesk.session_isolation import claim_deepseek_chat
from helpdesk.storage import Store, encode
from helpdesk.workflow import Workflow
from tests import test_answer_teaching as source_fixture
from tests.test_lesson_checks import SYNTHETIC_CHECKER
from tests.test_mcp_preparation import FakeDesktop, URL, snap
from helpdesk.teaching_routes import OBJECTIVE_CHECKER


STUDENT_ONLY = {'match_status': 'STUDENT_ONLY', 'selected_candidate': None,
                'relation': ['NO_SUITABLE_CANDIDATE'], 'differences': [],
                'option_mapping': {}, 'unresolved_fields': []}


def response(snapshot, data, prefix='match_run_'):
    token = prefix + snapshot['run_id']
    return snap('desktop\n└── window "DeepSeek - Microsoft Edge"\n'
                f'    ├── (1,1) 文档 "DeepSeek" [value:"{URL}"]\n'
                '    ├── 按钮 "朗读"\n'
                f'    ├── text "BEGIN_{token}"\n'
                '    ├── text "' + json.dumps(data, ensure_ascii=False) + '"\n'
                f'    └── text "END_{token}"\n')


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / 'business.db')
        self.addCleanup(self.db.close)
        self.app = Helpdesk(self.db)
        binding = self.binding = self.app.bind('synthetic-English', 'synthetic-student', '匿名', verified=True)
        self.incoming = self.app.ingest(Incoming(binding, 'SELF_AUTHORED_SYNTHETIC_REQUEST', Intent.NEW,
            verified_question=demo_question(), raw_material='He returned home to help his mother.',
            verified_material='He returned home to help his mother.'))
        self.flow = Workflow(self.db, desktop=MockDesktop(self.base / 'mock-desktop.db'))
        self.run = self.flow.start(self.incoming.turn_id)
        self.original = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (self.run,))[0])

    def lookup(self, ids=(), *, status=None):
        return Mock(run_for_question=Mock(return_value={
            'retrieval_status': status or ('CANDIDATES_FOUND' if ids else 'NO_RESULTS'),
            'top_candidates': [{'candidate_id': i} for i in ids], 'lookup_key': 'SYNTHETIC_SEARCH'}))

    def candidate(self, question=None):
        return ReferenceResolution(self.db).add(self.original['question_version'], 'SYNTHETIC_REFERENCE',
            question or demo_question(), self.original['student_material'], 'SELF_AUTHORED_TEST',
            provenance={'source_policy': {'business_record_storage_allowed': True}})[0]

    def test_search_frozen_once_real_ids_top2_and_no_ledger_changes(self):
        cid = self.candidate()
        lookup = self.lookup([cid])
        frozen = matching.prepare_input(self.db, self.run, lookup)
        again = matching.prepare_input(self.db, self.run, lookup)
        self.assertEqual(frozen, again)
        lookup.run_for_question.assert_called_once()
        self.assertEqual(lookup.run_for_question.call_args.kwargs['trigger'], 'initial_question')
        self.assertEqual(frozen['question_match_input']['candidates'][0]['candidate_id'], cid)
        self.assertNotIn('reference_answers', encode(matching.matching_payload(frozen)))
        self.assertNotIn('teaching_skills', matching.matching_payload(frozen))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)

    def test_unknown_or_duplicate_candidate_and_unavailable_search_fail_closed(self):
        cid = self.candidate()
        for lookup, reason in ((self.lookup(['made-up']), 'NOT_FOUND'),
                               (self.lookup([cid, cid]), 'DUPLICATE'),
                               (self.lookup([cid, cid, cid]), 'TOP2'),
                               (self.lookup(status='DISABLED'), 'SEARCH_UNAVAILABLE'),
                               (self.lookup(status='INTERRUPTED'), 'SEARCH_UNAVAILABLE')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                matching.prepare_input(self.db, self.run, lookup)
        self.assertNotIn('question_match_input', json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (self.run,))[0]))

    def test_all_24_independent_option_orders_and_number_change(self):
        cid = self.candidate()
        frozen = matching.prepare_input(self.db, self.run, self.lookup([cid]))
        student = Question.from_dict(frozen['student_question'])
        for order in itertools.permutations(range(4)):
            changed = copy.deepcopy(frozen)
            reference = replace(student, number='99', options=tuple(
                Option.confirmed(label, student.options[index].verified_text, i, 'SELF_AUTHORED_TEST')
                for i, (label, index) in enumerate(zip('ABCD', order))))
            changed['question_match_input']['candidates'][0]['reference_question'] = reference.to_dict()
            reordered = order != (0, 1, 2, 3)
            model = {'match_status': 'MATCH', 'selected_candidate': cid,
                     'relation': ['SAME_CONTENT', 'QUESTION_NUMBER_CHANGED'] + (['OPTION_REORDER'] if reordered else []),
                     'differences': ['NUMBER_ONLY'] + (['OPTION_ORDER'] if reordered else []),
                     'option_mapping': {label: 'ABCD'[index] for label, index in zip('ABCD', order)}, 'unresolved_fields': []}
            with self.subTest(order=order):
                checked = matching.validate_result(changed, model)
                self.assertEqual(checked['status'], 'VERIFIED_REFERENCE')
                self.assertEqual(len(checked['comparison']['option_mapping']), 4)
                wrong = copy.deepcopy(model)
                wrong['option_mapping']['A'] = wrong['option_mapping']['B']
                with self.assertRaisesRegex(ValueError, 'BIJECTION'):
                    matching.validate_result(changed, wrong)

    def test_negation_conditions_missing_options_and_unresolved_cannot_claim_match(self):
        cid = self.candidate()
        frozen = matching.prepare_input(self.db, self.run, self.lookup([cid]))
        model = {'match_status': 'MATCH', 'selected_candidate': cid, 'relation': ['SAME_CONTENT'],
                 'differences': ['FORMATTING_ONLY'], 'option_mapping': dict(zip('ABCD', 'ABCD')), 'unresolved_fields': []}
        self.assertEqual(matching.validate_result(frozen, model)['status'], 'VERIFIED_REFERENCE')
        for ref_stem, stu_stem in (
            ('Which is TRUE?', 'Which is NOT TRUE?'), ('All EXCEPT which?', 'All which?'),
            ('He gave 2 books to 3 children.', 'He gave 3 books to 2 children.'),
            ('Between 2 and 8 meters.', 'Between 8 and 2 meters.')):
            changed = copy.deepcopy(frozen)
            changed['student_question'].update(raw_stem=stu_stem, verified_stem=stu_stem)
            changed['question_match_input']['candidates'][0]['reference_question'].update(raw_stem=ref_stem, verified_stem=ref_stem)
            with self.subTest(stem=stu_stem), self.assertRaisesRegex(ValueError, 'PROGRAM_COMPARISON'):
                matching.validate_result(changed, model)
        for change in ('missing-option', 'material', 'unknown', 'confidence', 'candidate'):
            changed, result = copy.deepcopy(frozen), copy.deepcopy(model)
            if change == 'missing-option':
                changed['question_match_input']['candidates'][0]['reference_question']['options'].pop()
            elif change == 'material':
                changed['question_match_input']['candidates'][0]['reference_material'] = 'A different passage.'
            elif change == 'unknown':
                result['unresolved_fields'] = ['candidate_conflict']
            elif change == 'confidence':
                result['confidence'] = .99
            else:
                result['selected_candidate'] = 'not-in-Top2'
            with self.subTest(change=change), self.assertRaises(ValueError):
                matching.validate_result(changed, result)

    def test_clear_no_candidate_is_usable_unclear_source_is_not_filled(self):
        frozen = matching.prepare_input(self.db, self.run, self.lookup())
        self.assertEqual(matching.validate_result(frozen, STUDENT_ONLY)['status'], 'VERIFIED_STUDENT_ONLY')
        changed = copy.deepcopy(frozen)
        changed['student_question']['options'][0]['verified_text'] = None
        with self.assertRaisesRegex(ValueError, 'SOURCE_INCOMPLETE'):
            matching.validate_result(changed, STUDENT_ONLY)

    def test_existing_rejected_reference_can_be_compared_but_never_promoted(self):
        cid = self.candidate(demo_question(stem='Why did he NOT return home?'))
        frozen = matching.prepare_input(self.db, self.run, self.lookup([cid]))
        candidate = frozen['question_match_input']['candidates'][0]
        self.assertEqual(candidate['state'], 'REJECTED')
        self.assertIn('NOT', candidate['reference_question']['verified_stem'])
        self.assertEqual(matching.validate_result(frozen, STUDENT_ONLY)['status'], 'VERIFIED_STUDENT_ONLY')
        claimed = {'match_status': 'MATCH', 'selected_candidate': cid, 'relation': ['SAME_CONTENT'],
                   'differences': ['FORMATTING_ONLY'], 'option_mapping': dict(zip('ABCD', 'ABCD')), 'unresolved_fields': []}
        with self.assertRaisesRegex(ValueError, 'MATCH_REJECTED_CANDIDATE'):
            matching.validate_result(frozen, claimed)
        self.assertEqual(json.loads(self.db.one('SELECT comparison FROM reference_candidates WHERE id=?', (cid,))[0])['candidate']['state'], 'REJECTED')

    def proof(self, frozen, data=STUDENT_ONLY):
        claim_deepseek_chat(frozen, URL)
        matching.begin_attempt(self.db, frozen, self.base / 'preparation.json', URL)
        path = self.base / ('matching-' + frozen['run_id'] + '.json')
        path.write_text(encode({'status': 'MATCH_OUTPUT_CAPTURED', 'binding': matching._binding(frozen),
            'session_url': URL, 'display_index': None, 'snapshot': response(frozen, data)}), encoding='utf-8')
        return path

    def test_capture_restart_and_duplicate_registration_keep_one_result(self):
        frozen = matching.prepare_input(self.db, self.run, self.lookup())
        proof = self.proof(frozen)
        saved = matching.record_result(self.db, frozen, proof)
        self.assertEqual(matching.record_result(self.db, saved, proof), saved)
        restarted = Store(self.base / 'business.db')
        try:
            actual = json.loads(restarted.one('SELECT input_json FROM runs WHERE id=?', (self.run,))[0])
            self.assertEqual(matching.validate_receipt(restarted, actual), saved['question_match_result'])
            self.assertEqual(restarted.one("SELECT COUNT(*) FROM audit WHERE event='QUESTION_MATCH_VERIFIED'")[0], 1)
        finally:
            restarted.close()
        proof.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'EVIDENCE_CHANGED'):
            matching.validate_receipt(self.db, saved)

    def test_changed_stored_candidate_is_not_hidden_by_frozen_model_result(self):
        cid = self.candidate()
        frozen = matching.prepare_input(self.db, self.run, self.lookup([cid]))
        saved = matching.record_result(self.db, frozen, self.proof(frozen))
        self.db.execute('UPDATE reference_candidates SET material_text=? WHERE id=?', ('Changed reference.', cid))
        with self.assertRaisesRegex(ValueError, 'CANDIDATE_CONTENT_CHANGED'):
            matching.validate_receipt(self.db, saved)

    def test_followup_does_not_silently_run_a_fresh_full_search(self):
        follow = self.app.ingest(Incoming(self.binding, 'Why not B?', Intent.FOLLOWUP,
                                         quote_message_id=self.incoming.message_id))
        self.db.execute("UPDATE runs SET state='REJECTED' WHERE id=?", (self.run,))
        run = self.flow.start(follow.turn_id)
        lookup = self.lookup()
        with self.assertRaisesRegex(ValueError, 'FOLLOWUP_REUSE_REQUIRES_REVIEW'):
            matching.prepare_input(self.db, run, lookup)
        lookup.run_for_question.assert_not_called()

    def test_correction_during_matching_keeps_evidence_and_stops_commit(self):
        frozen = matching.prepare_input(self.db, self.run, self.lookup())
        proof = self.proof(frozen)
        material = self.db.one('SELECT material_id FROM questions WHERE id=?', (self.incoming.question_id,))[0]
        self.app.correct_material(material, self.incoming.message_id, 'Changed original.', 'Changed original.')
        with self.assertRaisesRegex(ValueError, 'MATCH_RUN_NOT_ACTIVE'):
            matching.record_result(self.db, frozen, proof)
        self.assertTrue(proof.is_file())
        self.assertFalse(self.db.one("SELECT id FROM audit WHERE event='QUESTION_MATCH_VERIFIED'"))


class TwoStageDesktop(FakeDesktop):
    """Fake page/picker, explicitly not evidence of a real browser submission."""
    def __init__(self, snapshot, *, unknown=False, result=None):
        super().__init__([])
        self.context = snapshot
        self.submission_uploads = []
        self.unknown = unknown
        self.match_result = result or STUDENT_ONLY

    def call(self, tool, args):
        if tool == 'Type' and not self.picker:
            self.submitted = False
        if tool == 'Shortcut' and args.get('shortcut') == 'enter':
            self.submission_uploads.append(list(self.uploaded))
            if self.unknown:
                raise TimeoutError('SYNTHETIC uncertain submission')
        observed = super().call(tool, args)
        if tool == 'Snapshot' and self.submitted and not self.picker:
            for item in observed['content']:
                if item.get('type') == 'text':
                    tree = item['text'].split('    ├── 按钮 "朗读"', 1)[0]
                    first = 'BEGIN_match_run_' in self.prompt
                    data = self.match_result if first else {'option_label': 'A', 'text': 'SYNTHETIC ANSWER。选A。'}
                    answer = response(self.context, data, 'match_run_' if first else 'answer_run_')['content'][0]['text']
                    item['text'] = tree + '    ├── 按钮 "朗读"' + answer.split('    ├── 按钮 "朗读"', 1)[1]
        return observed


class TwoStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source = source_fixture.AnswerTeachingTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.base = self.source.base
        (self.source.repo / OBJECTIVE_CHECKER).write_text(SYNTHETIC_CHECKER, encoding='utf-8')
        self.source.git('add', '--', OBJECTIVE_CHECKER)
        self.source.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-m', 'Synthetic two-stage checker fixture')
        self.source.commit = self.source.git('rev-parse', 'HEAD').decode().strip()
        self.source.write_pin()
        self.manifest = self.source.bundle(for_generation=True)
        self.db = Store(self.base / 'business.db')
        self.addCleanup(self.db.close)
        self.tasks = OperatorTasks(self.db)
        images = []
        for i in range(2):
            path = self.base / f'synthetic-image-{i}.png'
            Image.new('RGB', (20, 20), (i * 50, 100, 200)).save(path)
            images.append({'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest(),
                           'provenance': 'SELF_AUTHORED SYNTHETIC UPLOAD FIXTURE; NOT A STUDENT IMAGE'})
        draft = self.tasks.create_draft({'passage': 'He returned home to help his mother.', 'number': '12',
            'stem': 'Why did he return home?', 'question_type': '阅读理解',
            'options': {o.label: o.verified_text for o in demo_question().options}, 'attachments': images})
        self.task = self.tasks.review(draft['id'], expected_revision=draft['revision'], reviewer='SYNTHETIC REVIEWER',
                                     source_evidence='SELF_AUTHORED TEST; not a real student or human review')
        self.task = self.tasks.freeze(self.task['id'], teaching_manifest=self.manifest,
            preparation_path=self.base / 'prepared.json', evidence_dir=self.base / 'generation')
        lookup = Mock(run_for_question=Mock(return_value={'retrieval_status': 'NO_RESULTS', 'top_candidates': []}))
        self.snapshot = matching.prepare_input(self.db, self.task['run_id'], lookup)
        self.controls = {'preparation_mode': 'VERIFY_THEN_TEACH', 'upload_button': 'Upload files',
                         'picker_window': 'Open', 'file_input': 'File name', 'open_button': 'Open'}
        self.path = self.base / 'candidate.json'

    def test_actual_existing_functions_run_two_stages_and_do_not_award_draft(self):
        desktop = TwoStageDesktop(self.snapshot)
        candidate = DeepSeekSessionPreparer(desktop, self.snapshot, URL, self.path, self.controls,
                                            poll_interval=0, timeout=2).run()
        first = desktop.submission_uploads[0]
        self.assertEqual(len(first), 3)
        self.assertEqual(first[:2], ['synthetic-image-0.png', 'synthetic-image-1.png'])
        self.assertTrue(first[2].startswith('match-'))
        self.assertFalse(any(name.endswith('.md') for name in first))
        saved = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (self.task['run_id'],))[0])
        self.assertEqual(saved['question_match_result']['checked']['status'], 'VERIFIED_STUDENT_ONLY')
        preparation = complete_automatic_preparation(self.db, self.task['id'], self.path)
        self.assertEqual(preparation['preparation_mode'], 'VERIFY_THEN_TEACH')
        generator = PreparedDeepSeekGenerator(desktop, self.task['preparation_path'], self.task['evidence_dir'],
                                              poll_interval=0, timeout=2)
        generated = generator.generate(saved)
        self.assertEqual(len(desktop.submission_uploads), 2)
        self.assertIn('第二阶段教学', desktop.prompt)
        self.assertEqual(len(desktop.uploaded), len(set(desktop.uploaded)))
        self.assertEqual(candidate['effective_material_order'], 'QUESTION_VERIFY_THEN_COURSE')
        flow = Workflow(self.db, desktop=MockDesktop(self.base / 'no-send.db'), teaching_manifest=self.manifest,
                        generation_adapter=generator)
        outcome = flow.finish(saved['run_id'], generated)
        self.assertEqual(outcome['state'], 'GENERATED')
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (outcome['outbox_id'],))[0], 'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_first_submission_unknown_never_uploads_teaching_or_resubmits(self):
        desktop = TwoStageDesktop(self.snapshot, unknown=True)
        make = lambda: DeepSeekSessionPreparer(desktop, self.snapshot, URL, self.path, self.controls,
                                               poll_interval=0, timeout=2)
        with self.assertRaises(TimeoutError):
            make().run()
        with self.assertRaises(FileExistsError):
            make().run()
        calls_before = len(desktop.calls)
        with self.assertRaisesRegex(ValueError, 'MATCH_ATTEMPT_REQUIRES_REVIEW'):
            DeepSeekSessionPreparer(desktop, self.snapshot, URL, self.base / 'new-name.json', self.controls,
                                   poll_interval=0, timeout=2).run()
        self.assertEqual(len(desktop.calls), calls_before)
        self.assertEqual(len(desktop.submission_uploads), 1)
        self.assertFalse(any(name.endswith('.md') for name in desktop.uploaded))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['status'], 'OUTCOME_REQUIRES_REVIEW')

    def test_unresolved_candidate_conflict_stops_before_teaching(self):
        unresolved = {**STUDENT_ONLY, 'match_status': 'UNRESOLVED', 'unresolved_fields': ['candidate_conflict']}
        desktop = TwoStageDesktop(self.snapshot, result=unresolved)
        with self.assertRaisesRegex(ValueError, 'MATCH_REQUIRES_REVIEW'):
            DeepSeekSessionPreparer(desktop, self.snapshot, URL, self.path, self.controls, poll_interval=0, timeout=2).run()
        self.assertFalse(any(name.endswith('.md') for name in desktop.uploaded))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.assertFalse(self.db.one("SELECT id FROM audit WHERE event='QUESTION_MATCH_VERIFIED'"))


if __name__ == '__main__':
    unittest.main()
