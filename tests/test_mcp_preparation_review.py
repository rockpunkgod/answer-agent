from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.mcp_preparation_review import PreparationReviewError, review_preparation


URL = 'https://chat.deepseek.com/a/chat/s/review-test'
RUN = '1234567890123456'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.files = []
        for i in range(7):
            path = self.base / (f'course{i}.md' if i < 5 else f'image{i}.png')
            path.write_text(f'Verified course excerpt {i} is present.' if i < 5 else
                            f'image {i} bytes', encoding='utf-8')
            self.files.append({'kind': 'course' if i < 5 else 'question_image',
                               'path': str(path), 'name': path.name,
                               'sha256': sha256(path.read_bytes()).hexdigest()})
        stem = 'What did the character do after school?'
        self.snapshot = {key: 'x' for key in ('case_id', 'question_id', 'question_version',
                                              'student_material', 'student_words', 'intent')}
        self.snapshot.update(run_id=RUN, context_revision=0,
                             student_question={'verified_stem': stem},
                             teaching_skills=[{k: x[k] for k in ('path', 'sha256')}
                                              for x in self.files[:5]],
                             attachments=[{k: x[k] for k in ('path', 'sha256')}
                                          for x in self.files[5:]])
        excerpts = {x['path']: Path(x['path']).read_text(encoding='utf-8')
                    for x in self.files[:5]}
        lines = [f'{x["name"]}：{excerpts[x["path"]]}' for x in self.files[:5]]
        lines.append('题干：' + stem)
        tree = ('UI Tree:\n└── window "DeepSeek - Microsoft Edge"\n'
                f'    ├── (1,1) 文档 "DeepSeek" [value:"{URL}"]\n'
                '    ├── 按钮 "朗读"\n'
                f'    ├── text "BEGIN_run_{RUN}"\n'
                + ''.join(f'    ├── text "{line}"\n' for line in lines)
                + f'    └── text "END_run_{RUN}"\n')
        observed = {'tool': 'Snapshot', 'is_error': False,
                    'content': [{'type': 'text', 'text': tree}]}
        self.candidate = dict(status='READBACK_CANDIDATE_REQUIRES_OPERATOR_REVIEW',
                              operator_verified=False, run_id=RUN,
                              input_fingerprint=input_fingerprint(self.snapshot),
                              files=self.files, session_url=URL,
                              uploaded_teaching_hashes={x['path']: x['sha256'] for x in self.files[:5]},
                              visible_attachment_names=[x['name'] for x in self.files],
                              all_course_excerpts_match=True, question_stem_matches=True,
                              readback_snapshot=observed, readback_text='\n'.join(lines),
                              course_readback_excerpts=excerpts,
                              events=[{'intent': 'submit readback request once',
                                       'status': 'TOOL_RETURNED'}])
        self.review = dict(reviewer='Test operator', source_verified_excerpts=excerpts,
                           reviewed_image_hashes={x['path']: x['sha256'] for x in self.files[5:]},
                           verified_question_stem=stem,
                           image_review_statement='I inspected both frozen question images and verified the question stem.')
        self.candidate_path = self.base / 'candidate.json'
        self.review_path = self.base / 'review.json'
        self.output = self.base / 'verified.json'
        self.readback = self.base / 'readback.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write_inputs(self):
        self.candidate_path.write_text(json.dumps(self.candidate), encoding='utf-8')
        self.review_path.write_text(json.dumps(self.review), encoding='utf-8')

    def approve(self):
        self.write_inputs()
        return review_preparation(self.snapshot, self.candidate_path, self.review_path,
                                  self.output, self.readback)

    def test_explicit_review_creates_generator_compatible_artifacts(self):
        approved = self.approve()
        self.assertEqual(approved['status'], 'OPERATOR_VERIFIED_UPLOAD_AND_INPUT')
        self.assertTrue(self.candidate_path.exists())
        self.assertTrue(self.review_path.exists())
        self.assertEqual(approved['readback_sha256'], sha256(self.readback.read_bytes()).hexdigest())
        generator = PreparedDeepSeekGenerator(None, self.output, self.base)
        prep, page = generator._preparation(self.snapshot)
        self.assertEqual(prep['reviewer'], 'Test operator')
        self.assertEqual(page.url, URL)
        with self.assertRaises(PreparationReviewError):
            self.approve()

    def test_missing_image_review_fails_without_verified_output(self):
        self.review['reviewed_image_hashes'].pop(self.files[5]['path'])
        with self.assertRaisesRegex(PreparationReviewError, 'Both frozen image hashes'):
            self.approve()
        self.assertFalse(self.output.exists())

    def test_filename_only_response_fails_even_with_manufactured_flags(self):
        excerpt = next(iter(self.review['source_verified_excerpts'].values()))
        self.candidate['readback_text'] = self.candidate['readback_text'].replace(excerpt, 'course0.md')
        self.candidate['readback_snapshot']['content'][0]['text'] = (
            self.candidate['readback_snapshot']['content'][0]['text'].replace(excerpt, 'course0.md'))
        with self.assertRaisesRegex(PreparationReviewError, 'Course excerpt'):
            self.approve()
        self.assertFalse(self.output.exists())

    def test_changed_frozen_file_fails(self):
        Path(self.files[0]['path']).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(PreparationReviewError, 'Frozen file hash changed'):
            self.approve()

    def test_wrong_reviewed_stem_fails(self):
        self.review['verified_question_stem'] = 'An unrelated question appeared in the picture.'
        with self.assertRaisesRegex(PreparationReviewError, 'Reviewed image stem differs'):
            self.approve()


if __name__ == '__main__':
    unittest.main()
