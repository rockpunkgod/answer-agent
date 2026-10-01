import json
from copy import deepcopy
import shutil
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpdesk import teaching_bundle as bundle_module
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from helpdesk.teaching_bundle import (BASE_FILES, COURSE_EVIDENCE, EVIDENCE_GAPS,
                                      TYPE_MODULES, TeachingBundleError,
                                      build_bundle, verify_bundle, verify_frozen_teaching)


class TeachingBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.skill = self.base / 'gaokao-english'
        self.skill.mkdir()
        self.output = self.base / 'bundles'
        for relative in (*BASE_FILES, *TYPE_MODULES.values(), EVIDENCE_GAPS, COURSE_EVIDENCE):
            path = self.skill / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'# {relative}\n', encoding='utf-8')
        (self.skill / 'SKILL.md').write_text(
            '`references/delivery-contract.md` `references/router.md`\n'
            '`references/reading-comprehension.md` `references/seven-five.md`\n'
            '`references/evidence-gaps.md` `references/course-evidence.md`\n', encoding='utf-8')
        (self.skill / 'references' / 'reading-comprehension.md').write_text(
            '阅读理解方法，`references/evidence-gaps.md` 是停止依据。', encoding='utf-8')

    def tearDown(self):
        self.doCleanups()
        self.tmp.cleanup()

    def bundle(self, **kwargs):
        return build_bundle(question_type='阅读理解', skill_root=self.skill,
                            output_root=self.output, **kwargs)

    def test_explicit_reading_bundle_has_only_route_and_required_dependencies(self):
        path = self.bundle()
        manifest = verify_bundle(path)
        expected = (*BASE_FILES, TYPE_MODULES['阅读理解'], EVIDENCE_GAPS)
        self.assertEqual(tuple(item['relative_path'] for item in manifest['files']), expected)
        self.assertEqual(len(manifest['workflow_teaching_paths']), len(expected))
        self.assertTrue(all(Path(p).is_file() and Path(p).suffix == '.md'
                            for p in manifest['workflow_teaching_paths']))
        self.assertNotIn('references/seven-five.md', [x['relative_path'] for x in manifest['files']])
        self.assertIn(COURSE_EVIDENCE, [x['relative_path'] for x in manifest['deferred_references']])
        self.assertEqual(manifest['course_coverage'], 'ACTIVATED_WITH_BOUNDARIES')
        self.assertFalse(manifest['real_deepseek_uploaded'])

    def test_course_basis_request_adds_evidence_file_only_when_explicit(self):
        path = self.bundle(request_kind='course_basis')
        manifest = verify_bundle(path)
        self.assertIn(COURSE_EVIDENCE, [x['relative_path'] for x in manifest['files']])
        self.assertFalse(manifest['answer_generation_allowed_by_course'])

    def test_missing_dependency_and_unknown_reference_fail_closed(self):
        (self.skill / EVIDENCE_GAPS).unlink()
        with self.assertRaisesRegex(TeachingBundleError, 'Missing teaching dependency'):
            self.bundle()
        (self.skill / EVIDENCE_GAPS).write_text('缺口', encoding='utf-8')
        with (self.skill / TYPE_MODULES['阅读理解']).open('a', encoding='utf-8') as stream:
            stream.write('\n`references/new-method.md`')
        with self.assertRaisesRegex(TeachingBundleError, 'Undeclared required'):
            self.bundle()

    def test_traversal_reference_and_snapshot_escape_are_rejected(self):
        with (self.skill / TYPE_MODULES['阅读理解']).open('a', encoding='utf-8') as stream:
            stream.write('\n`references/../private.md`')
        with self.assertRaisesRegex(TeachingBundleError, 'Unsafe or unlisted'):
            self.bundle()
        (self.skill / TYPE_MODULES['阅读理解']).write_text('阅读', encoding='utf-8')
        path = self.bundle()
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['files'][0]['snapshot_path'] = str(self.base / 'elsewhere.md')
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(TeachingBundleError):
            verify_bundle(path)

    def test_hash_verification_catches_snapshot_or_source_mutation(self):
        path = self.bundle()
        manifest = json.loads(path.read_text(encoding='utf-8'))
        snapshot = Path(manifest['workflow_teaching_paths'][-1])
        snapshot.write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(TeachingBundleError, 'Snapshot hash mismatch'):
            verify_bundle(path)
        snapshot.write_bytes((self.skill / EVIDENCE_GAPS).read_bytes())
        (self.skill / EVIDENCE_GAPS).write_text('changed at source', encoding='utf-8')
        with self.assertRaisesRegex(TeachingBundleError, 'Source hash mismatch'):
            verify_bundle(path)

    def test_rehashed_tampered_snapshot_cannot_pose_as_source(self):
        path = self.bundle()
        manifest = json.loads(path.read_text(encoding='utf-8'))
        record = manifest['files'][-1]
        snapshot = Path(record['snapshot_path'])
        snapshot.write_bytes(b'x' * record['bytes'])
        record['snapshot_sha256'] = sha256(snapshot.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(TeachingBundleError, 'Snapshot differs from source policy'):
            verify_bundle(path)

    def test_reviewed_policy_pin_blocks_changed_skill_source(self):
        with patch.object(bundle_module, 'DEFAULT_SKILL_ROOT', self.skill):
            with self.assertRaisesRegex(TeachingBundleError, 're-review required'):
                self.bundle()

    def test_reviewed_coverage_notice_cannot_be_forged_into_answer_permission(self):
        hashes = {relative: sha256((self.skill / relative).read_bytes()).hexdigest()
                  for relative in bundle_module.REVIEWED_SOURCE_SHA256}
        with patch.object(bundle_module, 'DEFAULT_SKILL_ROOT', self.skill), \
                patch.object(bundle_module, 'REVIEWED_SOURCE_SHA256', hashes):
            path = build_bundle(question_type='听力', request_kind='coverage_notice',
                                skill_root=self.skill, output_root=self.output)
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['answer_generation_allowed_by_course'] = True
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(TeachingBundleError, 'authorization'):
                verify_bundle(path)

    def test_custom_source_cannot_forge_review_or_coverage(self):
        path = self.bundle()
        original = json.loads(path.read_text(encoding='utf-8'))
        for field, value in [('reviewed_policy_id', bundle_module.REVIEWED_POLICY_ID),
                             ('policy_review_status', 'PINNED_REVIEWED_SOURCE'),
                             ('course_coverage', 'FULLY_APPROVED')]:
            with self.subTest(field=field):
                path.write_text(json.dumps(original | {field: value}), encoding='utf-8')
                with self.assertRaises(TeachingBundleError):
                    verify_bundle(path)

    def test_workflow_rechecks_manifest_at_each_generation(self):
        hashes = {relative: sha256((self.skill / relative).read_bytes()).hexdigest()
                  for relative in bundle_module.REVIEWED_SOURCE_SHA256}
        db = Store(self.base / 'workflow.db')
        self.addCleanup(db.close)
        with patch.object(bundle_module, 'DEFAULT_SKILL_ROOT', self.skill), \
                patch.object(bundle_module, 'REVIEWED_SOURCE_SHA256', hashes):
            path = self.bundle()
            workflow = Workflow(db, teaching_manifest=path)
            entries = workflow._skills()
            self.assertEqual(len(entries), 5)
            self.assertTrue(all(x['source'] == 'verified_teaching_manifest' for x in entries))
            (self.skill / EVIDENCE_GAPS).write_text('changed policy', encoding='utf-8')
            with self.assertRaises(TeachingBundleError):
                workflow._skills()

    def test_workflow_refuses_unreviewed_or_notice_only_bundle(self):
        db = Store(self.base / 'workflow.db')
        self.addCleanup(db.close)
        path = self.bundle()
        with self.assertRaisesRegex(ValueError, 'does not authorize'):
            Workflow(db, teaching_manifest=path)._skills()
        with self.assertRaisesRegex(ValueError, 'not both'):
            Workflow(db, teaching_manifest=path, teaching_paths=[self.skill / 'SKILL.md'])

    def test_reviewed_legacy_bundle_is_readable_but_cannot_start_real_generation(self):
        hashes = {relative: sha256((self.skill / relative).read_bytes()).hexdigest()
                  for relative in bundle_module.REVIEWED_SOURCE_SHA256}
        db = Store(self.base / 'source-gate.db');self.addCleanup(db.close)
        adapter = Mock(identity='REAL_SOURCE_TEST', simulated=False)
        with patch.object(bundle_module, 'DEFAULT_SKILL_ROOT', self.skill), \
                patch.object(bundle_module, 'REVIEWED_SOURCE_SHA256', hashes):
            path = self.bundle();original = path.read_bytes()
            self.assertTrue(verify_bundle(path)['answer_generation_allowed_by_course'])
            with self.assertRaisesRegex(TeachingBundleError, 'ANSWER_SOURCE_REQUIRED'):
                Workflow(db, teaching_manifest=path, generation_adapter=adapter)._skills()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(db.one('SELECT COUNT(*) FROM runs')[0], 0)
            adapter.generate.assert_not_called()

    def test_legacy_frozen_input_cannot_upload_or_resume_and_is_preserved(self):
        from helpdesk.mcp_generation import PreparedDeepSeekGenerator
        from helpdesk.mcp_preparation import DeepSeekSessionPreparer
        path = self.bundle();manifest = verify_bundle(path)
        skill_path = manifest['workflow_teaching_paths'][0]
        snapshot = {'teaching_skills': [{'path': skill_path, 'manifest_path': str(path),
            'sha256': manifest['files'][0]['snapshot_sha256']}], 'run_id': '1234567890123456'}
        original = deepcopy(snapshot);transport = Mock()
        evidence = self.base / 'not-created' / 'candidate.json'
        for operation in (
            lambda: DeepSeekSessionPreparer(transport, snapshot,
                'https://chat.deepseek.com/a/chat/s/source-test', evidence, {}),
            lambda: PreparedDeepSeekGenerator(transport, self.base / 'absent.json',
                self.base / 'not-created').generate(snapshot),
        ):
            with self.assertRaisesRegex(TeachingBundleError, 'ANSWER_SOURCE_REQUIRED'):
                operation()
        transport.call.assert_not_called()
        self.assertEqual(snapshot, original)
        self.assertFalse(evidence.parent.exists())

    def test_unbound_old_snapshot_stops_before_any_external_action(self):
        for skills in (None, [], [None], [{}], [{'manifest_path': ['not-a-path']}],
                       [{'manifest_path': 'one'}, {'manifest_path': 'two'}]):
            with self.subTest(skills=skills), self.assertRaisesRegex(TeachingBundleError, 'ANSWER_SOURCE_UNBOUND'):
                verify_frozen_teaching({'teaching_skills': skills})

    def test_frozen_source_version_and_upload_bytes_must_match_verified_contract(self):
        path = self.skill / 'SKILL.md';content = path.read_bytes();digest = sha256(content).hexdigest()
        manifest = {'workflow_teaching_paths': [str(path)],
            'files': [{'snapshot_path': str(path), 'snapshot_sha256': digest}],
            'reviewed_policy_id': 'ANSWER@' + 'a' * 40, 'question_type': '阅读理解'}
        skill = {'path': str(path), 'manifest_path': str(self.base / 'approved-manifest.json'),
            'sha256': digest, 'content': content.decode('utf-8'), 'source': 'verified_teaching_manifest',
            'reviewed_policy_id': manifest['reviewed_policy_id'], 'question_type': '阅读理解'}
        # An approved contract is mocked only for frozen-input validation.
        # Actual pinned source and inactive previews are tested separately.
        with patch.object(bundle_module, 'verify_bundle', return_value=manifest) as verifier:
            self.assertEqual(verify_frozen_teaching({'teaching_skills': [skill]}), manifest)
            verifier.assert_called_with(skill['manifest_path'], for_generation=True)
            for changes, reason in (({'reviewed_policy_id': 'ANSWER@' + 'b' * 40}, 'SOURCE_CHANGED'),
                                    ({'question_type': '语法填空'}, 'SOURCE_CHANGED'),
                                    ({'sha256': '0' * 64}, 'SOURCE_CHANGED'),
                                    ({'source': 'operator_allowlist'}, 'SOURCE_CHANGED'),
                                    ({'path': str(self.base / 'other.md')}, 'FILES_CHANGED'),
                                    ({'content': 'invented summary'}, 'CONTENT_CHANGED')):
                with self.subTest(changes=changes), self.assertRaisesRegex(TeachingBundleError, reason):
                    verify_frozen_teaching({'teaching_skills': [skill | changes]})
            path.write_text('changed after freezing', encoding='utf-8')
            with self.assertRaisesRegex(TeachingBundleError, 'CONTENT_CHANGED'):
                verify_frozen_teaching({'teaching_skills': [skill]})

    def test_type_must_be_explicit_and_uncovered_answer_is_refused(self):
        with self.assertRaisesRegex(TeachingBundleError, 'Explicit supported question type'):
            build_bundle(question_type='未知题型', skill_root=self.skill, output_root=self.output)
        with self.assertRaisesRegex(TeachingBundleError, 'not supported'):
            build_bundle(question_type='听力', skill_root=self.skill, output_root=self.output)
        notice = build_bundle(question_type='听力', request_kind='coverage_notice',
                              skill_root=self.skill, output_root=self.output)
        manifest = verify_bundle(notice)
        self.assertEqual(manifest['course_coverage'], 'NOT_ACTIVATED')
        self.assertFalse(manifest['answer_generation_allowed_by_course'])

    def test_symbolic_link_dependency_is_rejected(self):
        target = self.skill / EVIDENCE_GAPS
        target.unlink()
        outside = self.base / 'outside.md'
        outside.write_text('外部内容', encoding='utf-8')
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('Filesystem cannot create symbolic links')
        with self.assertRaisesRegex(TeachingBundleError, 'Symbolic link'):
            self.bundle()

    def test_windows_junction_cannot_load_dependencies_outside_skill(self):
        # Directory junctions exercise real Windows path redirection without
        # granting the symlink privilege or changing Developer Mode settings.
        try:
            import _winapi
        except ImportError:
            self.skipTest('Windows junction API is unavailable')
        redirected = self.base / 'junction-skill'
        redirected.mkdir()
        outside = self.base / 'outside-references'
        shutil.copytree(self.skill / 'references', outside)
        for path in self.skill.iterdir():
            if path.name != 'references':
                if path.is_dir(): shutil.copytree(path, redirected / path.name)
                else: shutil.copy2(path, redirected / path.name)
        link = redirected / 'references'
        try:
            _winapi.CreateJunction(str(outside), str(link))
        except (OSError, NotImplementedError):
            self.skipTest('Filesystem cannot create directory junctions')
        try:
            self.assertTrue(link.is_junction())
            with self.assertRaisesRegex(TeachingBundleError, 'escapes skill root'):
                build_bundle(question_type='阅读理解', skill_root=redirected, output_root=self.output)
        finally:
            # Remove the junction itself, never recursively follow its target.
            link.rmdir()


if __name__ == '__main__':
    unittest.main()
