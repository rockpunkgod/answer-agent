import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from helpdesk.mcp_generation import input_fingerprint
from helpdesk.mcp_preparation import DeepSeekSessionPreparer, PreparationUnconfirmed


URL = 'https://chat.deepseek.com/a/chat/s/preparation-test'


def snap(tree, image=None):
    content = [{'type': 'text', 'text':
                ('Screenshot Original Size: (100,100)\n' if image else '') + 'UI Tree:\n' + tree}]
    if image:
        content.append({'type': 'image', 'path': str(image)})
    return {'tool': 'Snapshot', 'is_error': False,
            'content': content}


class FakeDesktop:
    def __init__(self, files, *, wrong_url=False, ambiguous_button=False,
                 fail_open=False, filename_only_readback=False, visual_images=None):
        self.files = files
        self.calls = []
        self.picker = False
        self.picker_value = ''
        self.uploaded = []
        self.prompt = ''
        self.submitted = False
        self.wrong_url = wrong_url
        self.ambiguous_button = ambiguous_button
        self.fail_open = fail_open
        self.filename_only_readback = filename_only_readback
        self.visual_images = visual_images
        self.clipboard = ''

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == 'Screenshot':
            if not self.visual_images:
                raise AssertionError('No visual fixture')
            return {'tool': 'Screenshot', 'is_error': False,
                    'content': [{'type': 'text', 'text': 'Screenshot Original Size: (100,100)'},
                                {'type': 'image', 'path': str(self.visual_images[1])}]}
        if tool == 'Snapshot':
            if self.picker:
                value = f' [value:"{self.picker_value}"]' if self.picker_value else ''
                return snap('desktop\n└── window "Open"\n'
                            f'    ├── (2,3) 编辑 "File name"{value}\n'
                            '    └── (4,5) 按钮 "Open"\n',
                            self.visual_images[1] if self.visual_images and args.get('use_vision') else None)
            url = URL + 'x' if self.wrong_url else URL
            attached = ''.join(f'    ├── text "{x}"\n' for x in self.uploaded)
            editor = (f' [focused] [value:"{self.prompt}"]' if self.prompt and not self.submitted else '')
            upload = '    ├── (6,7) 按钮 "Upload files"\n'
            if self.ambiguous_button:
                upload += upload
            answer = ''
            if self.submitted:
                answer = '    ├── 按钮 "朗读"\n    ├── text "BEGIN_run_1234567890123456"\n'
                for x in self.files[:5]:
                    excerpt = x['name'] if self.filename_only_readback else x['content']
                    answer += f'    ├── text "{x["name"]}：{excerpt}"\n'
                answer += ('    ├── text "题干：What did the character do after school?"\n'
                           '    └── text "END_run_1234567890123456"\n')
            return snap('desktop\n└── window "DeepSeek - Microsoft Edge"\n'
                        f'    ├── (1,1) 文档 "DeepSeek" [value:"{url}"]\n'
                        + attached + upload
                        + f'    ├── (8,9) 编辑 "给 DeepSeek 发送消息"{editor}\n'
                        + '    ├── (20,19) 组 "智能搜索"\n' + answer,
                        self.visual_images[0] if self.visual_images and args.get('use_vision') else None)
        if tool == 'Click':
            if self.picker:
                if self.visual_images and args['loc'] == [30, 70]:
                    return {}
                if self.fail_open:
                    raise TimeoutError('Open click uncertain')
                self.uploaded.append(Path(self.picker_value).name)
                self.picker = False
                self.picker_value = ''
            else:
                self.picker = True
            return {}
        if tool == 'Type':
            if self.picker:
                self.picker_value = args['text']
            else:
                self.prompt = args['text']
            return {}
        if tool == 'Shortcut':
            if args['shortcut'] == 'ctrl+c':
                self.clipboard = self.picker_value
            if args['shortcut'] != 'enter':
                return {}
            self.submitted = True
            return {}
        if tool == 'Clipboard':
            if args['mode'] == 'set':
                self.clipboard = args['text']
                return {}
            return {'content': [{'type': 'text', 'text': 'Clipboard content:\n' + self.clipboard}]}
        raise AssertionError(tool)


class SequentialDesktop(FakeDesktop):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submission_uploads = []

    def call(self, tool, args):
        if tool == 'Type' and not self.picker:
            self.submitted = False
        if tool == 'Shortcut' and args.get('shortcut') == 'enter':
            self.submission_uploads.append(list(self.uploaded))
        result = super().call(tool, args)
        if tool == 'Snapshot' and 'BEGIN_course_run_' in self.prompt:
            for item in result['content']:
                if item.get('type') == 'text':
                    item['text'] = item['text'].replace('BEGIN_run_', 'BEGIN_course_run_').replace('END_run_', 'END_course_run_')
        return result


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.files = []
        for i in range(7):
            suffix = '.md' if i < 5 else '.png'
            p = self.base / f'file{i}{suffix}'
            content = f'Course excerpt number {i} is independently checkable.' if i < 5 else 'image bytes'
            p.write_text(content, encoding='utf-8')
            self.files.append({'path': str(p), 'name': p.name, 'sha256': sha256(p.read_bytes()).hexdigest(),
                               'content': content})
        self.snapshot = {k: 'x' for k in ('case_id', 'question_id', 'question_version',
                                           'student_material', 'student_words', 'intent')}
        self.snapshot.update(context_revision=0, run_id='1234567890123456',
                             student_question={'verified_stem': 'What did the character do after school?'},
                             attachments=[{k: x[k] for k in ('path', 'sha256')} for x in self.files[5:]],
                             teaching_skills=[{k: x[k] for k in ('path', 'sha256')} for x in self.files[:5]])
        self.controls = {'upload_button': 'Upload files', 'picker_window': 'Open',
                         'file_input': 'File name', 'open_button': 'Open'}
        self.evidence = self.base / 'evidence.json'

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, desktop):
        return DeepSeekSessionPreparer(desktop, self.snapshot, URL, self.evidence,
                                       self.controls, poll_interval=0, timeout=2)

    def test_full_upload_and_readback_stays_unverified(self):
        fake = FakeDesktop(self.files)
        result = self.make(fake).run()
        self.assertEqual(result['status'], 'READBACK_CANDIDATE_REQUIRES_OPERATOR_REVIEW')
        self.assertTrue(result['all_course_excerpts_match'])
        self.assertTrue(result['question_stem_matches'])
        self.assertFalse(result['operator_verified'])
        self.assertEqual(len(fake.uploaded), 7)
        self.assertEqual(sum(t == 'Shortcut' for t, _ in fake.calls), 1)
        self.assertEqual(result['input_fingerprint'], input_fingerprint(self.snapshot))
        with self.assertRaises(FileExistsError):
            self.make(fake).run()

    def test_courses_verified_before_any_question_upload(self):
        self.controls['material_order'] = 'COURSE_THEN_QUESTION'
        fake = SequentialDesktop(self.files)
        result = self.make(fake).run()
        self.assertEqual(fake.submission_uploads, [[x['name'] for x in self.files[:5]],
                                                 [x['name'] for x in self.files]])
        self.assertTrue(result['course_stage_verified'])
        self.assertEqual(result['material_order'], 'COURSE_THEN_QUESTION')
        self.assertEqual([x['phase'] for x in result['material_stages']],
                         ['COURSE_READBACK', 'QUESTION_AND_COURSE_READBACK'])
        self.assertTrue(all(x['all_course_excerpts_match'] for x in result['material_stages']))
        self.assertTrue(result['question_stem_matches'])
        self.assertFalse(result['operator_verified'])
        prompts = [a['text'] for t, a in fake.calls if t == 'Type' and a.get('loc') == [8, 9]]
        self.assertFalse(any(x['name'] in prompts[0] for x in self.files[5:]))
        self.assertTrue(all(x['name'] in prompts[1] for x in self.files))

    def test_course_mismatch_blocks_question_upload_and_retry(self):
        self.controls['material_order'] = 'COURSE_THEN_QUESTION'
        fake = SequentialDesktop(self.files, filename_only_readback=True)
        with self.assertRaisesRegex(PreparationUnconfirmed, 'question upload prohibited'):
            self.make(fake).run()
        self.assertEqual(fake.uploaded, [x['name'] for x in self.files[:5]])
        self.assertEqual(len(fake.submission_uploads), 1)
        record = json.loads(self.evidence.read_text(encoding='utf-8'))
        self.assertFalse(record['course_stage_verified'])
        self.assertFalse(record['automatic_retry_allowed'])
        self.assertEqual(record['status'], 'OUTCOME_REQUIRES_REVIEW')
        self.assertEqual(record['material_stages'][0]['status'], 'CONTENT_MISMATCH_REQUIRES_REVIEW')
        previous_calls = len(fake.calls)
        with self.assertRaises(FileExistsError):
            self.make(fake).run()
        self.assertEqual(len(fake.calls), previous_calls)

    def test_unknown_course_submit_never_uploads_question(self):
        self.controls['material_order'] = 'COURSE_THEN_QUESTION'
        class UncertainDesktop(SequentialDesktop):
            def call(self, tool, args):
                result = super().call(tool, args)
                if tool == 'Shortcut' and args.get('shortcut') == 'enter':
                    raise TimeoutError('Submission unknown')
                return result
        fake = UncertainDesktop(self.files)
        with self.assertRaises(TimeoutError):
            self.make(fake).run()
        self.assertEqual(fake.uploaded, [x['name'] for x in self.files[:5]])
        record = json.loads(self.evidence.read_text(encoding='utf-8'))
        self.assertFalse(record['automatic_retry_allowed'])
        self.assertEqual(record['material_stages'][0]['status'], 'SUBMISSION_UNCONFIRMED')
        self.assertEqual(record['events'][-1]['status'], 'OUTCOME_UNCONFIRMED')

    def test_material_order_is_explicit_and_validated(self):
        self.assertEqual(self.make(FakeDesktop(self.files)).record['material_order'], 'ALL_THEN_READBACK')
        self.controls['material_order'] = 'QUESTION_FIRST'
        fake = FakeDesktop(self.files)
        with self.assertRaisesRegex(ValueError, 'material_order'):
            self.make(fake)
        self.assertEqual(fake.calls, [])

    def test_wrong_session_and_ambiguous_upload_button_stop_without_click(self):
        for flag in ('wrong_url', 'ambiguous_button'):
            evidence = self.base / (flag + '.json')
            fake = FakeDesktop(self.files, **{flag: True})
            prep = DeepSeekSessionPreparer(fake, self.snapshot, URL, evidence,
                                           self.controls, poll_interval=0, timeout=2)
            with self.assertRaises((PreparationUnconfirmed, ValueError)):
                prep.run()
            self.assertFalse(any(t == 'Click' for t, _ in fake.calls))
            self.assertEqual(json.loads(evidence.read_text(encoding='utf-8'))['status'],
                             'OUTCOME_REQUIRES_REVIEW')

    def test_uncertain_picker_click_is_not_replayed(self):
        fake = FakeDesktop(self.files, fail_open=True)
        with self.assertRaises(TimeoutError):
            self.make(fake).run()
        self.assertEqual(sum(t == 'Click' and a['loc'] == [4, 5] for t, a in fake.calls), 1)
        record = json.loads(self.evidence.read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'OUTCOME_REQUIRES_REVIEW')
        self.assertFalse(record['automatic_retry_allowed'])
        self.assertEqual(record['events'][-1]['status'], 'OUTCOME_UNCONFIRMED')

    def test_changed_file_rejected_before_desktop(self):
        Path(self.files[0]['path']).write_text('changed', encoding='utf-8')
        fake = FakeDesktop(self.files)
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.make(fake)
        self.assertEqual(fake.calls, [])

    def test_filename_only_model_response_is_not_content_evidence(self):
        fake = FakeDesktop(self.files, filename_only_readback=True)
        result = self.make(fake).run()
        self.assertFalse(result['all_course_excerpts_match'])
        self.assertEqual(result['course_readback_excerpts'], {})
        self.assertFalse(result['operator_verified'])

    def test_reviewed_visual_upload_and_picker(self):
        page = self.base / 'page.png'
        picker = self.base / 'picker.png'
        Image.new('RGB', (100, 100), (240, 240, 240)).save(page)
        Image.new('RGB', (100, 100), (80, 80, 80)).save(picker)
        def digest(path, box):
            with Image.open(path) as image:
                return sha256(image.crop(box).convert('RGB').tobytes()).hexdigest()
        controls = {
            'visual_upload': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                              'editor_offset': [10, 10], 'crop_size': [10, 10],
                              'crop_sha256': digest(page, (13, 14, 23, 24))},
            'visual_picker': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                              'file_input_point': [30, 70], 'open_point': [70, 75],
                              'regions': {name: {'box': list(box), 'sha256': digest(picker, box)}
                                          for name, box in {'header': (1, 1, 20, 10),
                                                            'file_label': (5, 40, 25, 50),
                                                            'open_button': (60, 70, 80, 80)}.items()}}}
        fake = FakeDesktop(self.files, visual_images=(page, picker))
        result = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.evidence,
                                         controls, poll_interval=0, timeout=2).run()
        self.assertEqual(result['status'], 'READBACK_CANDIDATE_REQUIRES_OPERATOR_REVIEW')
        self.assertEqual(len(fake.uploaded), 7)
        self.assertEqual(result['last_visual_upload_check']['click_point'], [18, 19])
        self.assertEqual(sum(t == 'Clipboard' and a['mode'] == 'get' for t, a in fake.calls), 7)
        self.assertTrue(all(a.get('use_annotation') is False for t, a in fake.calls
                            if t == 'Snapshot' and a.get('use_vision')))
        self.assertEqual(sum(t == 'Screenshot' for t, _ in fake.calls), 21)
        self.assertTrue(all(a == {'use_annotation': False} for t, a in fake.calls if t == 'Screenshot'))

    def test_stable_named_group_anchor_for_visual_upload(self):
        page = self.base / 'page.png'
        Image.new('RGB', (100, 100), (240, 240, 240)).save(page)
        with Image.open(page) as image:
            digest = sha256(image.crop((13, 14, 23, 24)).convert('RGB').tobytes()).hexdigest()
        controls = {'visual_upload': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                                      'anchor_role': '组', 'anchor_name': '智能搜索',
                                      'editor_offset': [-2, 0], 'crop_size': [10, 10],
                                      'crop_sha256': digest},
                    'picker_window': 'Open', 'file_input': 'File name', 'open_button': 'Open'}
        fake = FakeDesktop(self.files, visual_images=(page, page))
        result = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.evidence,
                                         controls, poll_interval=0, timeout=2).run()
        self.assertEqual(result['last_visual_upload_check']['click_point'], [18, 19])

    def test_changed_visual_picker_region_stops_before_typing(self):
        page = self.base / 'page.png'
        picker = self.base / 'picker.png'
        Image.new('RGB', (100, 100), (240, 240, 240)).save(page)
        Image.new('RGB', (100, 100), (80, 80, 80)).save(picker)
        controls = {'upload_button': 'Upload files',
                    'visual_picker': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                                      'file_input_point': [30, 70], 'open_point': [70, 75],
                                      'regions': {name: {'box': list(box), 'sha256': '0'*64}
                                                  for name, box in {'header': (1, 1, 20, 10),
                                                                    'file_label': (5, 40, 25, 50),
                                                                    'open_button': (60, 70, 80, 80)}.items()}}}
        fake = FakeDesktop(self.files, visual_images=(page, picker))
        with self.assertRaisesRegex(PreparationUnconfirmed, 'region changed'):
            DeepSeekSessionPreparer(fake, self.snapshot, URL, self.evidence,
                                     controls, poll_interval=0, timeout=2).run()
        self.assertFalse(any(t == 'Type' for t, _ in fake.calls))


if __name__ == '__main__':
    unittest.main()
