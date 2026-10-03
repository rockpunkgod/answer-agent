"""Anonymous SQLite/fake browser only, including explicit synthetic UI receipts."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from PIL import Image

from helpdesk.automatic_preparation import complete_automatic_preparation
from helpdesk.delivery import MockDesktop
from helpdesk.domain import Intent
from helpdesk.followup_reuse import MATERIAL_EVENT, REUSE_EVENT
from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.operator_tasks import OperatorTasks
from helpdesk import question_matching as matching
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tests import test_question_matching as fixtures
from tests.test_mcp_preparation import URL


class FollowupReuseTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.TwoStageIntegrationTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.db, self.base, self.tasks = self.fx.db, self.fx.base, self.fx.tasks
        self.original = self.execute(self.fx.task, self.fx.snapshot, self.fx.path)

    def execute(self, task, snapshot, path, desktop=None):
        desktop = desktop or fixtures.TwoStageDesktop(snapshot)
        candidate = DeepSeekSessionPreparer(desktop, snapshot, URL, path, self.fx.controls,
                                            poll_interval=0, timeout=2).run()
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (task['run_id'],))[0])
        prep = complete_automatic_preparation(self.db, task['id'], path)
        generator = PreparedDeepSeekGenerator(desktop, task['preparation_path'], task['evidence_dir'],
                                              poll_interval=0, timeout=2)
        result = generator.generate(snapshot)
        flow = Workflow(self.db, desktop=MockDesktop(self.base / 'no-send.db'),
                        generation_adapter=generator, teaching_manifest=self.fx.manifest)
        outcome = flow.finish(task['run_id'], result)
        self.assertEqual(outcome['state'], 'GENERATED', outcome)
        return dict(snapshot=snapshot, candidate=candidate, prep=prep, desktop=desktop, outcome=outcome, flow=flow)

    def followup(self, parent=None, *, request='为什么不选B？保留学生原话。', intent='FOLLOWUP', **changes):
        parent = parent or self.fx.task
        payload = self.tasks.get_draft(parent['draft_id'])['payload'] | {'request_text': request} | changes
        draft = self.tasks.create_draft(payload, parent_task_id=parent['id'], intent=intent)
        task = self.tasks.review(draft['id'], expected_revision=draft['revision'], reviewer='SYNTHETIC REVIEWER',
                                 source_evidence='Synthetic input confirmation, no real student or desktop')
        directory = self.base / task['id']
        task = self.tasks.freeze(task['id'], teaching_manifest=self.fx.manifest,
                                 preparation_path=directory / 'prepared.json', evidence_dir=directory / 'generation')
        return task

    def prepare(self, task):
        lookup = Mock()
        snapshot = matching.prepare_input(self.db, task['run_id'], lookup)
        lookup.run_for_question.assert_not_called()
        return snapshot

    def test_two_followups_reuse_original_verification_and_upload_only_current_context(self):
        parent = self.fx.task
        original = self.original['snapshot']
        for text in ('为什么不选B？', '这一句的证据是什么？'):
            parent = self.followup(parent, request=text)
            snapshot = self.prepare(parent)
            self.assertEqual(snapshot['session_id'], original['session_id'])
            self.assertEqual(snapshot['followup_reuse']['origin_run_id'], original['run_id'])
            self.assertEqual(matching.prepare_input(self.db, parent['run_id'], None), snapshot)
            result = self.execute(parent, snapshot, Path(parent['preparation_path']).with_name('candidate.json'))
            desktop = result['desktop']
            self.assertEqual(len(desktop.uploaded), 1)
            self.assertEqual(len(desktop.submission_uploads), 1)
            self.assertNotIn('BEGIN_match_run_', desktop.prompt)
            self.assertIn('本轮为同一题目的普通追问', desktop.prompt)
            self.assertEqual(result['candidate']['effective_material_order'], 'FOLLOWUP_CONTEXT_ONLY')
            self.assertEqual(result['candidate']['uploaded_teaching_hashes'], {})
            self.assertEqual(result['prep']['reused_teaching_hashes'],
                             self.original['prep']['uploaded_teaching_hashes'])
            text_file = next(f for f in result['candidate']['files'] if f['kind'] == 'question_text')
            payload = json.loads(Path(text_file['path']).read_bytes())
            self.assertEqual(payload['turn_context']['student_words'], text)
            self.assertIsNone(payload['turn_context']['actual_delivery'])
            self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?',
                                        (result['outcome']['outbox_id'],))[0], 'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (MATERIAL_EVENT,))[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (REUSE_EVENT,))[0], 2)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='QUESTION_MATCH_ATTEMPT_STARTED'")[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_only_actual_delivery_enters_reused_context(self):
        row = self.db.one('SELECT * FROM outbox WHERE id=?', (self.original['outcome']['outbox_id'],))
        # Explicitly synthetic transport-protocol evidence, not a real send.
        self.original['flow']._record_check(row, {'confirmed': True, 'simulated': False,
            'body_hash': sha256(row['body'].encode()).hexdigest(), 'confirmed_at': '2026-10-03T08:00:00+00:00'}, simulated=False)
        task = self.followup()
        snapshot = self.prepare(task)
        self.assertEqual(snapshot['sent_history'][0]['outbox_id'], row['id'])
        self.assertEqual(snapshot['previous_sent_answer'], row['body'])
        result = self.execute(task, snapshot, self.base / 'followup-candidate.json')
        text_file = result['prep']['question_text_file']
        actual = json.loads(Path(text_file['path']).read_bytes())['turn_context']['actual_delivery']
        self.assertEqual(actual['sent_history'][0]['body'], row['body'])
        self.assertEqual(actual['sent_history'][0]['sent_at'], '2026-10-03T08:00:00+00:00')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_reused_result_survives_same_database_restart_without_search(self):
        task = self.followup()
        snapshot = self.prepare(task)
        path = self.db.path
        self.db.close()
        self.db = self.fx.db = Store(path)
        self.addCleanup(self.db.close)
        self.tasks = OperatorTasks(self.db)
        self.assertEqual(self.prepare(task), snapshot)
        self.execute(task, snapshot, self.base / 'resumed-candidate.json')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (REUSE_EVENT,))[0], 1)

    def test_missing_original_proof_stops_before_any_upload_or_search(self):
        task = self.followup()
        Path(self.original['snapshot']['question_match_result']['evidence_path']).unlink()
        lookup = Mock()
        with self.assertRaises((ValueError, OSError)):
            matching.prepare_input(self.db, task['run_id'], lookup)
        lookup.run_for_question.assert_not_called()
        self.assertFalse(self.db.one('SELECT 1 FROM audit WHERE event=?', (REUSE_EVENT,)))

    def test_uncertain_or_changed_original_generation_cannot_be_reused(self):
        task = self.followup()
        material = json.loads(self.db.one('SELECT details FROM audit WHERE event=?', (MATERIAL_EVENT,))[0])
        proof = Path(material['generation_path'])
        original = proof.read_bytes()
        for field, value in (('status', 'OUTCOME_REQUIRES_REVIEW'), ('text', 'Changed answer'),
                             ('correct_option_id', 'another-option'), ('uploads_confirmed', False)):
            with self.subTest(field=field):
                changed = json.loads(original)
                if field == 'status':
                    changed[field] = value
                else:
                    changed['result'][field] = value
                proof.write_text(json.dumps(changed), encoding='utf-8')
                lookup = Mock()
                try:
                    with self.assertRaisesRegex(ValueError, 'REUSE_.*EVIDENCE_'):
                        matching.prepare_input(self.db, task['run_id'], lookup)
                    lookup.run_for_question.assert_not_called()
                    self.assertFalse(self.db.one('SELECT 1 FROM audit WHERE event=?', (REUSE_EVENT,)))
                finally:
                    proof.write_bytes(original)

    def test_original_preparation_changed_after_reuse_stops_before_desktop(self):
        task = self.followup()
        snapshot = self.prepare(task)
        path = Path(self.fx.task['preparation_path'])
        path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        desktop = Mock()
        with self.assertRaisesRegex(ValueError, 'REUSE_PREPARATION_CHANGED'):
            DeepSeekSessionPreparer(desktop, snapshot, URL, self.base / 'changed-proof.json', self.fx.controls)
        desktop.call.assert_not_called()

    def test_correction_during_reused_generation_preserves_old_output_without_send(self):
        task = self.followup()
        snapshot = self.prepare(task)
        desktop = fixtures.TwoStageDesktop(snapshot)
        path = self.base / 'before-correction.json'
        DeepSeekSessionPreparer(desktop, snapshot, URL, path, self.fx.controls).run()
        complete_automatic_preparation(self.db, task['id'], path)
        original_call = desktop.call

        def correct_after_submit(tool, arguments):
            observed = original_call(tool, arguments)
            if tool == 'Shortcut' and arguments.get('shortcut') == 'enter':
                material = self.db.one('SELECT material_id FROM questions WHERE id=?', (task['question_id'],))[0]
                Helpdesk(self.db).correct_material(material, task['message_id'],
                    'SYNTHETIC corrected passage.', 'SYNTHETIC corrected passage.')
            return observed

        desktop.call = correct_after_submit
        generator = PreparedDeepSeekGenerator(desktop, task['preparation_path'], task['evidence_dir'], poll_interval=0, timeout=2)
        result = generator.generate(snapshot)
        flow = Workflow(self.db, generation_adapter=generator, teaching_manifest=self.fx.manifest)
        outcome = flow.finish(task['run_id'], result)
        self.assertEqual(outcome['state'], 'STALE')
        self.assertEqual(len(desktop.submission_uploads), 1)
        self.assertEqual(self.db.one('SELECT state FROM answers WHERE id=?', (outcome['answer_id'],))[0], 'STALE')
        self.assertFalse(self.db.one('SELECT 1 FROM outbox WHERE answer_id=?', (outcome['answer_id'],)))
        self.assertTrue(Path(result['web_session_evidence']).is_file())

    def test_collected_text_followup_reuses_original_images_and_preserves_source_time(self):
        from helpdesk.collector_dispatch import CollectorDispatcher
        from helpdesk.collector_storage import CollectorStore
        from helpdesk.source_question_tasks import SourceQuestionTasks
        from tests import test_source_question_tasks as source_tests

        # Exercise the production source entry with anonymous local messages.
        # Its isolated database cannot see the OPERATOR_TEST setup above.
        self.db = Store(self.base / 'source-business.db')
        self.addCleanup(self.db.close)
        source = source_tests.SourceQuestionTasksTests()
        source.db = self.db
        source.raw = CollectorStore(self.base / 'source-messages.db')
        source.tasks = SourceQuestionTasks(self.db)
        source.ack = CollectorDispatcher(source.raw, self.db)
        source.resolver = CollectorDispatcher(source.raw, self.db, processing_mode='CASE_RESOLUTION')
        Helpdesk(self.db).bind('fixture-room', 'fixture-student', 'fixture student', verified=True)
        original_task = None
        original_snapshot = None
        for index, sent in enumerate(('2026-09-30T22:58:00+08:00', '2026-09-30T23:01:00+08:00')):
            _, receipt, incoming = source.receive('source-' + str(index), sent=sent,
                intent=Intent.NEW if index == 0 else Intent.FOLLOWUP,
                question_id=None if index == 0 else original_task['question_id'],
                photo=Path(self.fx.snapshot['attachments'][0]['path']) if index == 0 else None)
            task = source.review(source.create(receipt))
            directory = self.base / ('source-turn-' + str(index))
            task = source.tasks.freeze(task['id'], teaching_manifest=self.fx.manifest,
                preparation_path=directory / 'prepared.json', evidence_dir=directory / 'generation')
            lookup = Mock(run_for_question=Mock(return_value={'retrieval_status': 'NO_RESULTS', 'top_candidates': []}))
            snapshot = matching.prepare_input(self.db, task['run_id'], lookup)
            self.assertIn('source_clarity_review', snapshot)
            self.assertNotIn('operator_test', snapshot)
            self.assertEqual(snapshot['source_clarity_review']['original_sent_at'], sent)
            result = self.execute(task, snapshot, directory / 'candidate.json')
            if index == 0:
                original_task, original_snapshot = task, snapshot
                lookup.run_for_question.assert_called_once()
            else:
                lookup.run_for_question.assert_not_called()
                self.assertEqual(snapshot['attachments'], [])
                self.assertEqual(snapshot['session_id'], original_snapshot['session_id'])
                self.assertEqual(snapshot['question_version'], original_snapshot['question_version'])
                self.assertEqual(len(result['desktop'].uploaded), 1)
                self.assertEqual(len(result['desktop'].submission_uploads), 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM questions')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM question_versions')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_new_image_disguised_as_followup_requires_verification(self):
        image = self.base / 'new-synthetic-image.png'
        Image.new('RGB', (21, 21), 'red').save(image)
        original = self.fx.task
        incoming = Helpdesk(self.db).ingest(Incoming(original['binding_id'], 'See this different image', Intent.FOLLOWUP,
            source='anonymous-test', question_id=original['question_id'], case_id=original['case_id'],
            attachments=({'path': str(image), 'sha256': sha256(image.read_bytes()).hexdigest()},)))
        run = self.original['flow'].start(incoming.turn_id)
        lookup = Mock()
        with self.assertRaisesRegex(ValueError, 'NEW_IMAGE_REQUIRES_VERIFICATION'):
            matching.prepare_input(self.db, run, lookup)
        lookup.run_for_question.assert_not_called()

    def test_substantive_correction_uses_new_verification_and_session(self):
        task = self.followup(intent='CORRECTION', stem='Why did he NOT return home?')
        lookup = Mock(run_for_question=Mock(return_value={'retrieval_status': 'NO_RESULTS', 'top_candidates': []}))
        snapshot = matching.prepare_input(self.db, task['run_id'], lookup)
        self.assertNotEqual(snapshot['question_version'], self.original['snapshot']['question_version'])
        self.assertNotEqual(snapshot['session_id'], self.original['snapshot']['session_id'])
        self.assertNotIn('followup_reuse', snapshot)
        self.assertNotIn('question_match_result', snapshot)
        lookup.run_for_question.assert_called_once()
        self.assertEqual(lookup.run_for_question.call_args.kwargs['trigger'], 'version_difference')

    def test_wrong_session_or_rehashed_reuse_provenance_never_reaches_desktop(self):
        task = self.followup()
        snapshot = self.prepare(task)
        for wrong in ('session_id', 'binding_id', 'question_version'):
            changed = deepcopy(snapshot)
            changed[wrong] = 'another-owned-scope'
            desktop = Mock()
            with self.subTest(field=wrong), self.assertRaises(ValueError):
                DeepSeekSessionPreparer(desktop, changed, URL, self.base / ('bad-' + wrong + '.json'), self.fx.controls)
            desktop.call.assert_not_called()
        changed = deepcopy(snapshot)
        changed['followup_reuse']['materials_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'UPLOAD_EVIDENCE_CHANGED'):
            matching.validate_receipt(self.db, changed)

    def test_unknown_followup_upload_cannot_retry_with_new_filename(self):
        task = self.followup()
        snapshot = self.prepare(task)
        desktop = fixtures.TwoStageDesktop(snapshot)
        desktop.fail_open = True
        with self.assertRaises(TimeoutError):
            DeepSeekSessionPreparer(desktop, snapshot, URL, self.base / 'unknown-candidate.json', self.fx.controls).run()
        before = len(desktop.calls)
        with self.assertRaisesRegex(ValueError, 'FOLLOWUP_UPLOAD_REQUIRES_REVIEW'):
            DeepSeekSessionPreparer(desktop, snapshot, URL, self.base / 'different-name.json', self.fx.controls).run()
        self.assertEqual(len(desktop.calls), before)
        self.assertEqual(desktop.submission_uploads, [])


if __name__ == '__main__':
    unittest.main()
