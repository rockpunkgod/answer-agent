"""Text-only upload and independent offline review, using local fake desktop only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.mcp_preparation import DeepSeekSessionPreparer, prepare_question_text, question_text_fields
from helpdesk.mcp_preparation_review import PreparationReviewError, TEXT_REVIEW_STATEMENT, review_preparation
from tests.test_mcp_preparation import FakeDesktop, URL


class TextPreparationTests(unittest.TestCase):
    def setUp(self):
        # Anonymous upload/generation fixtures do not authorize real teaching.
        for module in ('mcp_preparation', 'mcp_generation'):
            source = patch('helpdesk.' + module + '.verify_frozen_teaching', return_value={})
            source.start();self.addCleanup(source.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        course = self.base / 'course.md'
        course.write_text('This is an independently verified course excerpt.', encoding='utf-8')
        self.course = {'path': str(course), 'name': course.name,
                       'sha256': sha256(course.read_bytes()).hexdigest(), 'content': course.read_text()}
        self.snapshot = dict(case_id='case', question_id='question', question_version='version',
            context_revision=0, student_words='operator test', student_material='A complete original passage.',
            intent='NEW', run_id='1234567890123456', attachments=[],
            teaching_skills=[{k: self.course[k] for k in ('path', 'sha256')}],
            student_question={'number': '12', 'verified_stem': 'What did the character do after school?',
                'options': [{'id': 'option-' + label, 'label': label, 'verified_text': 'Answer ' + label}
                            for label in 'ABCD']})
        self.controls = dict(upload_button='Upload files', picker_window='Open',
                             file_input='File name', open_button='Open')
        self.candidate_path = self.base / 'candidate.json'
        self.review_path = self.base / 'review.json'
        self.output = self.base / 'approved.json'
        self.readback = self.base / 'readback.json'

    def tearDown(self):
        self.tmp.cleanup()

    def candidate(self):
        fake = FakeDesktop([self.course])
        record = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.candidate_path,
                                        self.controls, poll_interval=0, timeout=2).run()
        return fake, record

    def review(self):
        return dict(reviewer='Independent operator',
            source_verified_excerpts={self.course['path']: self.course['content']},
            verified_question_stem=self.snapshot['student_question']['verified_stem'],
            reviewed_question_text=question_text_fields(self.snapshot),
            reviewed_input_fingerprint=input_fingerprint(self.snapshot),
            source_review_evidence='Original locally supplied worksheet, page 1 question 12; checked against transcription.',
            question_text_review_statement=TEXT_REVIEW_STATEMENT)

    def approve(self, review):
        self.review_path.write_text(json.dumps(review), encoding='utf-8')
        return review_preparation(self.snapshot, self.candidate_path, self.review_path, self.output, self.readback)

    def test_actual_frozen_question_text_is_uploaded_before_readback(self):
        fake, record = self.candidate()
        self.assertEqual(len(fake.uploaded), 2)
        self.assertEqual([x['kind'] for x in record['files']], ['course', 'question_text'])
        text = record['files'][-1]
        document = json.loads(Path(text['path']).read_text(encoding='utf-8'))
        self.assertEqual(document['question'], question_text_fields(self.snapshot))
        self.assertEqual(document['input_fingerprint'], input_fingerprint(self.snapshot))
        self.assertEqual(record['visible_attachment_names'], [self.course['name'], text['name']])
        self.assertIn(text['name'], fake.prompt)
        self.assertNotIn('再读取题图', fake.prompt)
        self.assertFalse(record['operator_verified'])
        approved = self.approve(self.review())
        self.assertEqual(approved['reviewed_image_hashes'], {})
        self.assertEqual(approved['reviewed_question_text'], document['question'])
        PreparedDeepSeekGenerator(None, self.output, self.base)._preparation(self.snapshot)

    def test_text_review_requires_independent_explicit_source_fields_and_fingerprint(self):
        self.candidate()
        for change in ({'reviewer': ''}, {'source_review_evidence': ''},
                       {'question_text_review_statement': 'I trust the model readback.'},
                       {'reviewed_question_text': {}}, {'reviewed_input_fingerprint': 'wrong'},
                       {'reviewed_image_hashes': {'invented.png': 'fake'}},
                       {'image_review_statement': 'I inspected both frozen question images and verified the question stem.'}):
            with self.subTest(change=change), self.assertRaises(PreparationReviewError):
                self.approve(self.review() | change)
            self.assertFalse(self.output.exists())
            self.assertFalse(self.readback.exists())

    def test_generation_rejects_changed_provenance_before_transport_or_attempt(self):
        self.candidate()
        approved = self.approve(self.review())
        original_preparation = self.output.read_bytes()
        text_path = Path(approved['question_text_file']['path'])
        for path in (text_path, self.review_path, self.candidate_path):
            original = path.read_bytes()
            path.write_bytes(original + b'\nchanged after review')
            transport = Mock()
            evidence_dir = self.base / ('attempt-' + path.name)
            generator = PreparedDeepSeekGenerator(transport, self.output, evidence_dir)
            with self.subTest(path=path), self.assertRaises(ValueError):
                generator.generate(self.snapshot)
            transport.call.assert_not_called()
            self.assertFalse(evidence_dir.exists())
            path.write_bytes(original)
        # Even unchanged source bytes cannot pass a changed or incomplete hash reference.
        for hash_key, path_key in (('candidate_sha256', 'candidate_evidence'),
                                   ('operator_review_sha256', 'operator_review_evidence')):
            for mutation in ('wrong_hash', 'missing_hash', 'missing_path'):
                preparation = deepcopy(approved)
                if mutation == 'wrong_hash':
                    preparation[hash_key] = '0' * 64
                else:
                    preparation.pop(hash_key if mutation == 'missing_hash' else path_key)
                self.output.write_text(json.dumps(preparation), encoding='utf-8')
                transport = Mock()
                evidence_dir = self.base / ('attempt-' + hash_key + '-' + mutation)
                with self.subTest(hash_key=hash_key, mutation=mutation), self.assertRaises(ValueError):
                    PreparedDeepSeekGenerator(transport, self.output, evidence_dir).generate(self.snapshot)
                transport.call.assert_not_called()
                self.assertFalse(evidence_dir.exists())
        self.output.write_bytes(original_preparation)
        PreparedDeepSeekGenerator(None, self.output, self.base)._preparation(self.snapshot)

    def test_generation_rejects_rehashed_wrong_question_and_reviewed_fields(self):
        self.candidate()
        approved = self.approve(self.review())
        text_path = Path(approved['question_text_file']['path'])
        original_text = text_path.read_bytes()
        for change in ('question', 'fingerprint', 'reviewed_fields', 'reviewed_fingerprint'):
            text_path.write_bytes(original_text)
            preparation = deepcopy(approved)
            if change in ('question', 'fingerprint'):
                document = json.loads(original_text)
                if change == 'question':
                    document['question']['options']['A'] = 'Altered option after source review'
                else:
                    document['input_fingerprint'] = 'different frozen input'
                text_path.write_text(json.dumps(document), encoding='utf-8')
                preparation['question_text_file']['sha256'] = sha256(text_path.read_bytes()).hexdigest()
            elif change == 'reviewed_fields':
                preparation['reviewed_question_text']['passage'] = 'An unrelated source passage'
            else:
                preparation['reviewed_input_fingerprint'] = 'different reviewed input'
            self.output.write_text(json.dumps(preparation), encoding='utf-8')
            transport = Mock()
            evidence_dir = self.base / ('attempt-' + change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'question text or review changed'):
                PreparedDeepSeekGenerator(transport, self.output, evidence_dir).generate(self.snapshot)
            transport.call.assert_not_called()
            self.assertFalse(evidence_dir.exists())

    def test_review_persists_actual_reviewed_edge_window_title(self):
        _, candidate = self.candidate()
        actual_title = 'Operator English source review - Microsoft Edge'
        tree = candidate['readback_snapshot']['content'][0]['text']
        candidate['readback_snapshot']['content'][0]['text'] = tree.replace(
            'window "DeepSeek - Microsoft Edge"', 'window "' + actual_title + '"')
        self.candidate_path.write_text(json.dumps(candidate), encoding='utf-8')
        approved = self.approve(self.review())
        self.assertEqual(approved['window_name'], actual_title)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['window_name'], actual_title)

    def test_tampered_derived_text_and_incomplete_question_fail_before_desktop(self):
        fake = FakeDesktop([self.course])
        preparer = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.candidate_path, self.controls)
        Path(preparer.files[-1]['path']).write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'question text changed'):
            preparer.run()
        self.assertEqual(fake.calls, [])
        incomplete = deepcopy(self.snapshot)
        incomplete['student_question']['options'].pop()
        with self.assertRaisesRegex(ValueError, 'A-D options'):
            DeepSeekSessionPreparer(fake, incomplete, URL, self.base / 'invalid.json', self.controls)
        self.assertEqual(fake.calls, [])

    def test_candidate_cannot_swap_generated_question_or_skip_upload_name(self):
        _, candidate = self.candidate()
        for change in ({'question_text_sha256': 'bad'}, {'question_text_fields': {}},
                       {'visible_attachment_names': [self.course['name']]}):
            self.candidate_path.write_text(json.dumps(candidate | change), encoding='utf-8')
            with self.subTest(change=change), self.assertRaises(PreparationReviewError):
                self.approve(self.review())
        self.assertFalse(self.output.exists())

    def test_variable_image_count_and_invalid_file_bounds(self):
        image = self.base / 'question.png'
        image.write_bytes(b'local fixture image')
        snapshot = deepcopy(self.snapshot)
        snapshot['attachments'] = [{'path': str(image), 'sha256': sha256(image.read_bytes()).hexdigest()}]
        fake = FakeDesktop([self.course])
        record = DeepSeekSessionPreparer(fake, snapshot, URL, self.candidate_path, self.controls,
                                        poll_interval=0, timeout=2).run()
        self.assertEqual([x['kind'] for x in record['files']], ['course', 'question_image'])
        review = {'reviewer': 'Independent operator',
            'source_verified_excerpts': {self.course['path']: self.course['content']},
            'verified_question_stem': snapshot['student_question']['verified_stem'],
            'reviewed_image_hashes': {str(image): snapshot['attachments'][0]['sha256']},
            'image_review_statement': 'I inspected all frozen question images and verified the question stem.'}
        self.review_path.write_text(json.dumps(review), encoding='utf-8')
        review_preparation(snapshot, self.candidate_path, self.review_path, self.output, self.readback)
        invalid = deepcopy(snapshot)
        invalid['attachments'] *= 21
        idle = FakeDesktop([self.course])
        with self.assertRaises(ValueError):
            DeepSeekSessionPreparer(idle, invalid, URL, self.base / 'invalid.json', self.controls)
        invalid['attachments'] = snapshot['attachments']
        invalid['teaching_skills'] = []
        with self.assertRaises(ValueError):
            DeepSeekSessionPreparer(idle, invalid, URL, self.base / 'invalid.json', self.controls)
        self.assertEqual(idle.calls, [])


if __name__ == '__main__':
    unittest.main()
