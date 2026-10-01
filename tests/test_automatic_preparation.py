"""Offline projection of the first question review; fake upload observations only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from helpdesk.automatic_preparation import complete_automatic_preparation
from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.operator_tasks import OperatorTasks
from helpdesk.storage import Store, encode
from tests.test_mcp_preparation import FakeDesktop, URL


class AutomaticPreparationTests(unittest.TestCase):
    def setUp(self):
        source = patch('helpdesk.mcp_preparation.verify_frozen_teaching', return_value={})
        source.start();self.addCleanup(source.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / 'business.db')
        self.tasks = OperatorTasks(self.db)
        self.course = self.base / 'course.md'
        self.course.write_bytes(b'# Approved course\r\nRead the complete source and compare all options.\r\n')
        self.manifest_path = self.base / 'manifest.json'
        self.manifest_path.write_text('{}', encoding='utf-8')
        self.manifest = dict(question_type='阅读理解', answer_generation_allowed_by_course=True,
            workflow_teaching_paths=[str(self.course)],
            files=[dict(snapshot_path=str(self.course), snapshot_sha256=sha256(self.course.read_bytes()).hexdigest())],
            reviewed_policy_id='original-course-policy')
        self.payload = dict(passage='John went home to look after his mother.',
            stem='Why did John go home?', number='12', question_type='阅读理解',
            options={'A': 'To visit a friend.', 'B': 'To take a holiday.',
                     'C': 'To look after his mother.', 'D': 'To find a job.'})
        self.patches = [patch(name, return_value=self.manifest) for name in (
            'helpdesk.operator_tasks.verify_bundle', 'helpdesk.workflow.verify_bundle',
            'helpdesk.automatic_preparation.verify_bundle')]
        for stub in self.patches:
            stub.start()
        self.task, self.snapshot = self.frozen(self.payload)
        self.candidate = self.base / 'candidate.json'
        self.make_candidate()

    def tearDown(self):
        for stub in reversed(self.patches):
            stub.stop()
        self.db.close()
        self.tmp.cleanup()

    def frozen(self, payload):
        draft = self.tasks.create_draft(payload)
        task = self.tasks.review(draft['id'], expected_revision=draft['revision'],
            reviewer='Initial source reviewer', source_evidence='Original worksheet page 1; passage and all options verified')
        task = self.tasks.freeze(task['id'], teaching_manifest=self.manifest_path,
            preparation_path=self.base / task['id'] / 'preparation.json',
            evidence_dir=self.base / task['id'] / 'generation')
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (task['run_id'],))[0])
        return task, snapshot

    def make_candidate(self):
        fake = FakeDesktop([dict(path=str(self.course), name=self.course.name,
                                 content=self.course.read_text(encoding='utf-8'))])
        controls = dict(preparation_mode='FAST_UPLOAD_THEN_GENERATE', material_order='COURSE_THEN_QUESTION',
                        upload_button='Upload files', picker_window='Open', file_input='File name', open_button='Open')
        self.record = DeepSeekSessionPreparer(fake, self.snapshot, URL, self.candidate, controls,
                                              poll_interval=0, timeout=2).run()
        self.assertFalse(any(tool == 'Shortcut' and args.get('shortcut') == 'enter' for tool, args in fake.calls))

    def complete(self):
        return complete_automatic_preparation(self.db, self.task['id'], self.candidate)

    def assert_no_authorization(self):
        self.assertFalse(Path(self.task['preparation_path']).exists())
        self.assertFalse(Path(self.task['preparation_path']).with_name('source-question-review.json').exists())
        self.assertFalse(Path(self.task['preparation_path']).with_name('readiness.json').exists())

    def test_one_original_review_produces_valid_contract_and_idempotent_restart(self):
        before_audit = [dict(r) for r in self.db.all('SELECT * FROM audit')]
        before_tasks = [dict(r) for r in self.db.all('SELECT * FROM operator_tasks')]
        with patch.object(PreparedDeepSeekGenerator, 'generate', side_effect=AssertionError('no generation')):
            preparation = self.complete()
        self.assertEqual(preparation['status'], 'ATTACHMENTS_READY_SOURCE_REVIEWED')
        self.assertFalse(preparation['model_readback_performed'])
        source = json.loads(Path(preparation['operator_review_evidence']).read_text(encoding='utf-8'))
        self.assertEqual(source['reviewer'], self.task['reviewer'])
        self.assertEqual(source['reviewed_at'], self.task['reviewed_at'])
        self.assertEqual(source['source_review_evidence'], self.task['source_evidence'])
        self.assertEqual(source['review_origin'], 'INITIAL_OPERATOR_INPUT_REVIEW')
        self.assertFalse(source['new_human_review'])
        self.assertFalse(source['formal_statistics_eligible'])
        files = [Path(self.task['preparation_path']), Path(preparation['operator_review_evidence']), Path(preparation['readiness_evidence'])]
        saved = [(f.read_bytes(), f.stat().st_mtime_ns) for f in files]
        self.db.close()
        self.db = Store(self.base / 'business.db')
        self.assertEqual(self.complete(), preparation)
        self.assertEqual([(f.read_bytes(), f.stat().st_mtime_ns) for f in files], saved)
        verified, _ = PreparedDeepSeekGenerator(None, self.task['preparation_path'], self.base)._preparation(self.snapshot)
        self.assertEqual(verified, preparation)
        self.assertEqual([dict(r) for r in self.db.all('SELECT * FROM audit')], before_audit)
        self.assertEqual([dict(r) for r in self.db.all('SELECT * FROM operator_tasks')], before_tasks)
        for table in ('reviews', 'answers', 'performance_units'):
            self.assertEqual(self.db.one(f'SELECT COUNT(*) FROM {table}')[0], 0)

    def test_changed_first_review_fields_are_not_replaced_with_new_approval(self):
        for key, changed in (('source_evidence', 'different source'), ('reviewer', 'another person'),
                             ('reviewed_at', '2999-01-01T00:00:00+00:00')):
            with self.subTest(field=key):
                self.db.execute(f'UPDATE operator_tasks SET {key}=? WHERE id=?', (changed, self.task['id']))
                with self.assertRaises(ValueError):
                    self.complete()
                self.assert_no_authorization()
                self.db.execute(f'UPDATE operator_tasks SET {key}=? WHERE id=?', (self.task[key], self.task['id']))

    def test_unreviewed_changed_revision_stale_run_and_missing_audit_fail_closed(self):
        changes = [('operator_drafts', 'status', 'DRAFT', 'id', self.task['draft_id']),
                   ('operator_drafts', 'revision', 2, 'id', self.task['draft_id']),
                   ('runs', 'state', 'STALE', 'id', self.task['run_id'])]
        for table, key, changed, id_key, identity in changes:
            old = self.db.one(f'SELECT {key} FROM {table} WHERE {id_key}=?', (identity,))[0]
            with self.subTest(table=table, key=key):
                self.db.execute(f'UPDATE {table} SET {key}=? WHERE {id_key}=?', (changed, identity))
                with self.assertRaises(ValueError):
                    self.complete()
                self.assert_no_authorization()
                self.db.execute(f'UPDATE {table} SET {key}=? WHERE {id_key}=?', (old, identity))
        self.db.execute("DELETE FROM audit WHERE event='OPERATOR_TEST_INPUT_REVIEWED'")
        with self.assertRaisesRegex(ValueError, 'INITIAL_REVIEW_AUDIT_REQUIRED'):
            self.complete()
        self.assert_no_authorization()

    def test_source_revision_tampering_rejected_even_after_rehash(self):
        row = self.db.one('SELECT * FROM operator_draft_revisions WHERE draft_id=?', (self.task['draft_id'],))
        payload = json.loads(row['payload'])
        payload['passage'] = 'Different source passage.'
        raw = encode(payload)
        self.db.execute('UPDATE operator_draft_revisions SET payload=?,payload_sha256=? WHERE draft_id=?',
            (raw, sha256(raw.encode('utf-8')).hexdigest(), self.task['draft_id']))
        with self.assertRaisesRegex(ValueError, 'REVIEWED_SOURCE_FIELDS_CHANGED'):
            self.complete()
        self.assert_no_authorization()

    def test_snapshot_and_current_context_change_rejected(self):
        old = self.db.one('SELECT input_json FROM runs WHERE id=?', (self.task['run_id'],))[0]
        snapshot = deepcopy(self.snapshot)
        snapshot['student_question']['verified_stem'] = 'Changed frozen stem?'
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(snapshot), self.task['run_id']))
        with self.assertRaisesRegex(ValueError, 'REVIEWED_INPUT_CHANGED'):
            self.complete()
        self.assert_no_authorization()
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (old, self.task['run_id']))
        self.db.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?', (self.task['question_id'],))
        with self.assertRaises(ValueError):
            self.complete()
        self.assert_no_authorization()

    def test_unknown_upload_wrong_candidate_url_and_postvalidation_failure_publish_nothing(self):
        original = self.candidate.read_bytes()
        for mutation in ('uncertain_upload', 'wrong_url', 'wrong_order', 'extra_readback'):
            changed = deepcopy(self.record)
            if mutation == 'uncertain_upload':
                next(e for e in changed['events'] if e.get('intent') == 'submit file picker once')['status'] = 'OUTCOME_UNCONFIRMED'
            elif mutation == 'wrong_url':
                changed['session_url'] = URL + '-other'
            elif mutation == 'wrong_order':
                staged = [e for e in changed['events'] if e.get('intent') == 'stage frozen file path']
                staged[0]['arguments']['text'], staged[1]['arguments']['text'] = staged[1]['arguments']['text'], staged[0]['arguments']['text']
            else:
                changed['events'].append({'intent': 'submit readback request once', 'status': 'TOOL_RETURNED'})
            self.candidate.write_text(json.dumps(changed), encoding='utf-8')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.complete()
            self.assert_no_authorization()
        self.candidate.write_bytes(original)
        with patch.object(PreparedDeepSeekGenerator, '_preparation', side_effect=ValueError('postvalidation failed')):
            with self.assertRaisesRegex(ValueError, 'postvalidation failed'):
                self.complete()
        self.assert_no_authorization()

    def test_url_owned_by_other_question_rejected(self):
        other, _ = self.frozen(self.payload)
        other_run = self.db.one('SELECT session_id FROM runs WHERE id=?', (other['run_id'],))
        self.db.execute('UPDATE deepseek_chats SET session_id=? WHERE session_url=?', (other_run[0], URL))
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_CHAT_ALREADY_OWNED'):
            self.complete()
        self.assert_no_authorization()

    def test_image_hashes_and_provenance_project_initial_review(self):
        image = self.base / 'worksheet.png'
        Image.new('RGB', (2, 2), 'white').save(image)
        item = dict(path=str(image), sha256=sha256(image.read_bytes()).hexdigest(), provenance='Original worksheet page 1')
        self.task, self.snapshot = self.frozen(self.payload | {'attachments': [item]})
        self.candidate = self.base / 'image-candidate.json'
        # Each fresh task receives its own separately owned browser conversation.
        with patch('tests.test_mcp_preparation.URL', URL + '-image'):
            fake = FakeDesktop([dict(name=self.course.name, content=self.course.read_text(encoding='utf-8'))])
            self.record = DeepSeekSessionPreparer(fake, self.snapshot, URL + '-image', self.candidate,
                dict(preparation_mode='FAST_UPLOAD_THEN_GENERATE', upload_button='Upload files',
                     picker_window='Open', file_input='File name', open_button='Open'), poll_interval=0, timeout=2).run()
        before = image.read_bytes()
        image.write_bytes(before + b'changed')
        with self.assertRaisesRegex(ValueError, 'REVIEWED_SOURCE_IMAGE_CHANGED'):
            self.complete()
        self.assert_no_authorization()
        image.write_bytes(before)
        preparation = self.complete()
        self.assertEqual(preparation['reviewed_image_hashes'], {str(image): item['sha256']})
        source = json.loads(Path(preparation['operator_review_evidence']).read_text(encoding='utf-8'))
        self.assertEqual(source['reviewed_attachments'], [item])
        image.write_bytes(before + b'changed')
        with self.assertRaisesRegex(ValueError, 'REVIEWED_SOURCE_IMAGE_CHANGED'):
            self.complete()
        image.write_bytes(before)
        self.assertEqual(self.complete(), preparation)

    def test_changed_successful_evidence_or_course_cannot_be_reapproved(self):
        preparation = self.complete()
        review = Path(preparation['operator_review_evidence'])
        before = review.read_bytes()
        changed = json.loads(before)
        changed['source_evidence'] = 'changed after projection'
        review.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'AUTOMATIC_SOURCE_REVIEW_CHANGED'):
            self.complete()
        review.write_bytes(before)
        self.course.write_bytes(self.course.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'FROZEN_TEACHING_SOURCE_CHANGED'):
            self.complete()


if __name__ == '__main__':
    unittest.main()
