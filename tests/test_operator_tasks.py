"""Local fixtures only: no desktop, external sender, or original student database."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.operator_tasks import OperatorTasks
from helpdesk.service import Helpdesk
from helpdesk.storage import Store
from helpdesk.teaching_bundle import build_bundle
from helpdesk import teaching_bundle as teaching_module


class OperatorTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / 'local.db')
        self.intake = OperatorTasks(self.db)
        # Test only the manifest consumer here. No mutable personal Skill path
        # or real ANSWER rules are fixtures for an operator-entry unit test.
        self.skill_root = self.base / 'anonymous-course-fixture'
        fixture_hashes = {}
        for relative in (*teaching_module.BASE_FILES, teaching_module.EVIDENCE_GAPS,
                         teaching_module.TYPE_MODULES['阅读理解'], teaching_module.TYPE_MODULES['语法填空']):
            path = self.skill_root / relative;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Anonymous course contract fixture: ' + relative + '\n', encoding='utf-8')
            fixture_hashes[relative] = sha256(path.read_bytes()).hexdigest()
        for key, value in (('DEFAULT_SKILL_ROOT', self.skill_root), ('REVIEWED_SOURCE_SHA256', fixture_hashes)):
            contract = patch.object(teaching_module, key, value);contract.start();self.addCleanup(contract.stop)
        self.payload = {'passage': 'John went home to look after his mother.',
            'stem': 'Why did John go home?', 'number': '12', 'question_type': '阅读理解',
            'options': {'A': 'To visit a friend.', 'B': 'To take a holiday.',
                        'C': 'To look after his mother.', 'D': 'To find a job.'}}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def review(self, draft):
        return self.intake.review(draft['id'], expected_revision=draft['revision'],
                                 reviewer='Local reviewer', source_evidence='Operator supplied sample, page 1 Q12')

    def test_draft_revisions_do_not_ack_ingest_or_measure(self):
        draft = self.intake.create_draft({'stem': 'partial'})
        revised = self.intake.revise(draft['id'], self.payload, expected_revision=1)
        self.assertEqual([r['revision'] for r in revised['revisions']], [1, 2])
        self.assertEqual(revised['revisions'][0]['payload']['stem'], 'partial')
        for table in ('messages', 'bindings', 'outbox', 'performance_units', 'turns'):
            self.assertEqual(self.db.one(f'SELECT COUNT(*) FROM {table}')[0], 0)
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.intake.revise(draft['id'], self.payload, expected_revision=1)
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.intake.review(draft['id'], expected_revision=1, reviewer='reviewer', source_evidence='page')

    def test_review_idempotency_survives_restart_and_preserves_source_limits(self):
        draft = self.intake.create_draft(self.payload)
        task = self.review(draft)
        self.db.close()
        self.db = Store(self.base / 'local.db')
        self.intake = OperatorTasks(self.db)
        self.assertEqual(self.review(draft), task)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM messages')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM turns')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        message = self.db.one('SELECT * FROM messages')
        self.assertEqual(message['source'], 'OPERATOR_TEST')
        self.assertIsNone(message['platform_id'])
        self.assertIsNone(message['source_sent_at'])
        binding = self.db.one('SELECT * FROM bindings')
        self.assertTrue(binding['group_key'].startswith('local-operator-test:'))
        self.assertTrue(binding['student_key'].startswith('local-fixture:'))
        self.assertEqual(self.db.one('SELECT state FROM outbox')[0], 'PENDING')
        with self.assertRaisesRegex(ValueError, 'different evidence'):
            self.intake.review(draft['id'], expected_revision=1, reviewer='another', source_evidence='page')

    def test_request_id_prevents_duplicate_drafts_and_rejects_changed_retry(self):
        draft = self.intake.create_draft(self.payload, request_id='client-uuid-1')
        self.db.close()
        self.db = Store(self.base / 'local.db')
        self.intake = OperatorTasks(self.db)
        self.assertEqual(self.intake.create_draft(self.payload, request_id='client-uuid-1'), draft)
        with self.assertRaisesRegex(ValueError, 'content conflict'):
            self.intake.create_draft(self.payload | {'stem': 'Changed?'}, request_id='client-uuid-1')
        self.assertEqual(len(self.intake.list_drafts()), 1)

    def test_incomplete_content_and_missing_evidence_fail_before_ingress(self):
        for key in ('passage', 'stem', 'number', 'question_type'):
            draft = self.intake.create_draft(self.payload | {key: ''})
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.review(draft)
        draft = self.intake.create_draft(self.payload | {'options': {'A': 'one'}})
        with self.assertRaises(ValueError):
            self.review(draft)
        for reviewer, evidence in (('', 'page'), ('reviewer', ''), (' ', 'page')):
            with self.assertRaises(ValueError):
                self.intake.review(draft['id'], expected_revision=1, reviewer=reviewer, source_evidence=evidence)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM messages')[0], 0)
        with self.assertRaises(ValueError):
            self.intake.create_draft(self.payload | {'source_sent_at': 'invented'})

    def test_attachment_hash_and_provenance_revalidated_on_review(self):
        path = self.base / 'source.txt'
        path.write_text('source evidence', encoding='utf-8')
        item = {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest(), 'provenance': 'local original sample'}
        draft = self.intake.create_draft(self.payload | {'attachments': [item]})
        path.write_text('changed evidence', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.review(draft)
        with self.assertRaises(ValueError):
            self.intake.create_draft(self.payload | {'attachments': [item | {'provenance': ''}]})

    def test_review_rolls_back_all_engine_changes_if_linking_fails(self):
        draft = self.intake.create_draft(self.payload)
        with patch.object(Helpdesk, 'context', side_effect=ValueError('injected failure')):
            with self.assertRaises(ValueError):
                self.review(draft)
        for table in ('bindings', 'messages', 'cases', 'turns', 'outbox', 'operator_tasks'):
            self.assertEqual(self.db.one(f'SELECT COUNT(*) FROM {table}')[0], 0)
        self.assertEqual(self.intake.get_draft(draft['id'])['status'], 'DRAFT')
        self.review(draft)

    def test_followup_explicit_reference_and_correction_invalidate_old_turn(self):
        original = self.review(self.intake.create_draft(self.payload))
        independent = self.review(self.intake.create_draft(self.payload))
        followup = self.intake.create_draft(self.payload | {'request_text': 'Why not A?'},
                                           intent='FOLLOWUP', parent_task_id=original['id'])
        task = self.review(followup)
        self.assertEqual(task['binding_id'], original['binding_id'])
        self.assertNotEqual(task['binding_id'], independent['binding_id'])
        with self.assertRaisesRegex(ValueError, 'Stale turn'):
            Helpdesk(self.db).context(original['turn_id'])
        correction = self.intake.create_draft(self.payload | {'passage': 'John went home to visit a friend.'},
                                             intent='CORRECTION', parent_task_id=task['id'])
        changed = self.review(correction)
        self.assertEqual(Helpdesk(self.db).context(changed['turn_id'])['student_material'],
                         'John went home to visit a friend.')
        self.assertEqual(Helpdesk(self.db).context(independent['turn_id'])['student_material'], self.payload['passage'])
        with self.assertRaises(ValueError):
            self.intake.create_draft(self.payload, intent='FOLLOWUP')

    def test_pending_followup_draft_cannot_review_after_other_context_change(self):
        original = self.review(self.intake.create_draft(self.payload))
        pending = self.intake.create_draft(self.payload | {'request_text': 'Why C?'},
                                           intent='FOLLOWUP', parent_task_id=original['id'])
        competing = self.intake.create_draft(self.payload | {'request_text': 'Why not A?'},
                                             intent='FOLLOWUP', parent_task_id=original['id'])
        self.review(competing)
        with self.assertRaisesRegex(ValueError, 'Stale turn'):
            self.review(pending)
        self.assertEqual(self.intake.get_draft(pending['id'])['status'], 'DRAFT')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM messages')[0], 2)

    def test_manifest_type_mismatch_rejects_before_freezing(self):
        manifest = build_bundle(question_type='语法填空', skill_root=self.skill_root, output_root=self.base / 'bundles')
        task = self.review(self.intake.create_draft(self.payload))
        with self.assertRaisesRegex(ValueError, 'question type differs'):
            self.intake.freeze(task['id'], teaching_manifest=manifest,
                               preparation_path=self.base / 'prep.json', evidence_dir=self.base / 'evidence')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)

    def test_freeze_verified_manifest_only_no_generator_or_desktop_calls(self):
        manifest = build_bundle(question_type='阅读理解', skill_root=self.skill_root, output_root=self.base / 'bundles')
        task = self.review(self.intake.create_draft(self.payload))
        config = dict(teaching_manifest=manifest, preparation_path=self.base / 'future-preparation.json',
                      evidence_dir=self.base / 'future-evidence')
        with (patch('helpdesk.operator_tasks.PreparedDeepSeekGenerator.generate', side_effect=AssertionError('must not generate')),
              patch('helpdesk.workflow.MockDesktop', side_effect=AssertionError('must not create desktop'))):
            frozen = self.intake.freeze(task['id'], **config)
            self.assertEqual(self.intake.freeze(task['id'], **config), frozen)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (frozen['run_id'],))[0])
        self.assertEqual(snapshot['generation_adapter'], 'WINDOWS_MCP_PREPARED_DEEPSEEK')
        self.assertEqual(snapshot['operator_test']['label'], 'OPERATOR_TEST')
        self.assertFalse(snapshot['operator_test']['formal_statistics_eligible'])
        self.assertFalse(snapshot['simulated'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertFalse((self.base / 'future-evidence').exists())
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.intake.freeze(task['id'], **(config | {'evidence_dir': self.base / 'other'}))


if __name__ == '__main__':
    unittest.main()
