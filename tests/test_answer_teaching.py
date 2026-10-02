"""Source provenance and preview tests using local Git, never a model or GUI."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpdesk import answer_teaching as source
from helpdesk.teaching_bundle import TeachingBundleError, verify_bundle
from helpdesk.teaching_routes import GRAMMAR, OBJECTIVE, ROUTES, WRITING, OBJECTIVE_CHECKER
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tools.prepare_teaching_bundle import main


@unittest.skipUnless(shutil.which('git'), 'Local Git is required for source provenance tests')
class AnswerTeachingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'ANSWER'
        self.repo.mkdir()
        self.output = self.base / 'bundles'
        self.git('init', '--initial-branch=main')
        self.git('config', 'core.autocrlf', 'true')
        self.git('remote', 'add', 'origin', source.REPOSITORY_URL)
        (self.repo / '.gitattributes').write_bytes(b'* text\n')
        paths = {'README.md'}
        for route in ROUTES.values():
            paths.update((route.skill + '/SKILL.md', route.skill + '/' + route.module,
                          f'agents/{route.agent}.md'))
        paths.update(OBJECTIVE + '/references/' + name + '.md'
                     for name in ('delivery-contract', 'router', 'evidence-gaps'))
        paths.update(GRAMMAR + '/references/' + name + '.md'
                     for name in ('objective-revision', 'method-application-example', 'course-evidence'))
        paths.add(GRAMMAR + '/scripts/check_lesson.py')
        for name in sorted(paths):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('完整原文。条件不可摘要。\n' + name + '\n').encode('utf-8'))
        # The declared shared checker is snapshotted but never executed by a preview.
        (self.repo / GRAMMAR / 'scripts/check_lesson.py').write_bytes(
            b"raise RuntimeError('THIS_SOURCE_PREVIEW_MUST_NOT_EXECUTE_TEACHING_CODE')\n")
        self.git('add', '.')
        self.git('-c', 'user.name=Source test', '-c', 'user.email=source-test@example.invalid',
                 'commit', '-m', 'Local source fixture')
        self.commit = self.git('rev-parse', 'HEAD').decode('ascii').strip()
        self.pin = self.base / 'source.toml'
        self.write_pin()
        self.patcher = patch.object(source, 'PIN_CONFIG', self.pin)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def git(self, *args):
        completed = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True,
                                   check=True, timeout=15, shell=False)
        return completed.stdout

    def write_pin(self, **overrides):
        values = {'repository_url': source.REPOSITORY_URL, 'commit': self.commit,
                  'repository': str(self.repo)} | overrides
        self.pin.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in values.items()),
                            encoding='utf-8')

    def bundle(self, question_type='阅读理解', **kwargs):
        return source.build_answer_bundle(question_type=question_type, repository=self.repo,
                                         output_root=self.output, **kwargs)

    def write_manifest(self, path, manifest):
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    def remove_required_checker(self, *, add_legacy_copy=False):
        self.git('rm', '--', OBJECTIVE_CHECKER)
        if add_legacy_copy:
            old = self.repo / OBJECTIVE / 'scripts/check_lesson.py'
            old.parent.mkdir(parents=True)
            old.write_bytes(b'raise RuntimeError("NOT_THE_DECLARED_CHECKER")\n')
            self.git('add', '--', OBJECTIVE + '/scripts/check_lesson.py')
        self.git('-c', 'user.name=Source test', '-c', 'user.email=source-test@example.invalid',
                 'commit', '-m', 'Fixture missing required checker')
        self.commit = self.git('rev-parse', 'HEAD').decode('ascii').strip()
        self.write_pin()

    def test_real_generation_reports_required_missing_source_path(self):
        self.remove_required_checker()
        path = self.bundle();before = path.read_bytes()
        db = Store(self.base / 'source-gate.db');self.addCleanup(db.close)
        adapter = Mock(identity='REAL_SOURCE_TEST', simulated=False)
        with self.assertRaisesRegex(TeachingBundleError,
                'ANSWER_REQUIRED_DEPENDENCIES_MISSING: ' + OBJECTIVE_CHECKER):
            Workflow(db, teaching_manifest=path, generation_adapter=adapter)._skills()
        adapter.generate.assert_not_called()
        self.assertEqual(db.one('SELECT COUNT(*) FROM runs')[0], 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(verify_bundle(path)['answer_generation_allowed_by_course'])

    def test_complete_source_preview_cannot_be_used_as_generation_approval(self):
        path = self.bundle('语法填空')
        self.assertEqual(verify_bundle(path)['missing_dependencies'], [])
        with self.assertRaisesRegex(TeachingBundleError, 'ANSWER_TEACHING_NOT_ACTIVATED'):
            verify_bundle(path, for_generation=True)
        with self.assertRaisesRegex(TeachingBundleError, 'ANSWER_SOURCE_CHECK_REQUIRED'):
            verify_bundle(path, for_generation=True, check_sources=False)

    def test_valid_answer_preview_keeps_manual_workbench_available_without_queue_or_desktop(self):
        from helpdesk.demo_server import DemoHTTPServer
        self.remove_required_checker()
        path = self.bundle()
        with patch('helpdesk.mcp_transport.MCPProcess') as transport:
            server = DemoHTTPServer(('127.0.0.1', 0), db_path=self.base / 'manual.db',
                processing_mode='ACK_ONLY', source_review_manifest=path)
            try:
                self.assertTrue(server.source_review_enabled)
                self.assertFalse(server.question_auto_continue)
                self.assertIsNone(server.reviewed_queue_thread)
                self.assertIn(OBJECTIVE_CHECKER, server.teaching_blocked_reason)
                transport.assert_not_called()
            finally:
                server.server_close()

    def test_pin_rejects_another_repository_or_incomplete_commit(self):
        for overrides in ({'repository_url': 'https://example.invalid/OTHER.git'},
                          {'commit': self.commit[:8]}, {'commit': 'g' * 40}, {'extra': 'unreviewed'}):
            with self.subTest(overrides=overrides):
                self.write_pin(**overrides)
                with self.assertRaisesRegex(source.TeachingSourceError, 'SOURCE_PIN_INVALID'):
                    self.bundle()
        self.assertFalse(self.output.exists())

    def test_committed_bytes_are_preserved_with_crlf_checkout(self):
        selected = self.repo / OBJECTIVE / 'references/reading-comprehension.md'
        committed = self.git('show', self.commit + ':' + selected.relative_to(self.repo).as_posix())
        selected.write_bytes(committed.replace(b'\n', b'\r\n'))
        self.assertFalse(self.git('diff', '--name-only', 'HEAD').strip())
        manifest_path = self.bundle()
        manifest = verify_bundle(manifest_path)
        self.assertEqual(manifest['source_repository']['commit'], self.commit)
        self.assertEqual(len(manifest['workflow_teaching_paths']), 1)
        assembled = Path(manifest['workflow_teaching_paths'][0]).read_bytes()
        for item in manifest['source_files']:
            original = self.git('show', self.commit + ':' + item['relative_path'])
            snapshot = manifest_path.parent / 'sources' / item['relative_path']
            self.assertEqual(snapshot.read_bytes(), original)
            self.assertEqual(item['sha256'], sha256(original).hexdigest())
            if item['relative_path'] in manifest['required_dependencies']:
                self.assertNotIn(original, assembled)
            else:
                self.assertIn(original, assembled)
        self.assertIn(('Commit: ' + self.commit).encode(), assembled)

    def test_each_type_uses_the_readme_route_without_other_type_modules(self):
        for question_type, route in ROUTES.items():
            with self.subTest(question_type=question_type):
                manifest = verify_bundle(self.bundle(question_type))
                names = [item['relative_path'] for item in manifest['source_files']]
                self.assertEqual(manifest['skill'], route.skill)
                self.assertIn(route.skill + '/' + route.module, names)
                for other_type, other in ROUTES.items():
                    if other_type != question_type:
                        self.assertNotIn(other.skill + '/' + other.module, names)
                self.assertFalse(manifest['answer_generation_allowed_by_course'])
                self.assertFalse(manifest['real_deepseek_uploaded'])
                self.assertFalse(manifest['real_delivery_verified'])

    def test_grammar_includes_internal_instructions_but_does_not_execute_checker(self):
        manifest = verify_bundle(self.bundle('语法填空'))
        names = [item['relative_path'] for item in manifest['source_files']]
        for name in ('objective-revision', 'method-application-example', 'course-evidence'):
            self.assertIn(GRAMMAR + '/references/' + name + '.md', names)
        self.assertIn(GRAMMAR + '/scripts/check_lesson.py', names)
        self.assertEqual(manifest['missing_dependencies'], [])
        self.assertEqual(manifest['course_coverage'], 'SOURCE_VERIFIED_NOT_ACTIVATED')
        self.assertFalse(manifest['answer_generation_allowed_by_course'])
        self.assertNotIn(b'raise RuntimeError', Path(manifest['workflow_teaching_paths'][0]).read_bytes())

    def test_missing_objective_checker_is_reported_not_borrowed(self):
        self.remove_required_checker(add_legacy_copy=True)
        manifest = verify_bundle(self.bundle())
        self.assertEqual(manifest['missing_dependencies'], [OBJECTIVE_CHECKER])
        self.assertEqual(manifest['course_coverage'], 'DEPENDENCY_INCOMPLETE')
        self.assertFalse(manifest['answer_generation_allowed_by_course'])
        self.assertFalse(any(item['relative_path'].startswith(GRAMMAR)
                             for item in manifest['source_files']))
        self.assertNotIn(OBJECTIVE + '/scripts/check_lesson.py',
                         [item['relative_path'] for item in manifest['source_files']])

    def test_objective_uses_declared_shared_checker_without_grammar_methods(self):
        for kind in ('阅读理解', '七选五', '完形填空'):
            with self.subTest(kind=kind):
                manifest_path = self.bundle(kind)
                manifest = verify_bundle(manifest_path)
                self.assertEqual(manifest['required_dependencies'], [OBJECTIVE_CHECKER])
                self.assertEqual(manifest['missing_dependencies'], [])
                names = [item['relative_path'] for item in manifest['source_files']]
                self.assertEqual([name for name in names if name.startswith(GRAMMAR + '/')],
                                 [OBJECTIVE_CHECKER])
                original = self.git('show', self.commit + ':' + OBJECTIVE_CHECKER)
                self.assertEqual((manifest_path.parent / 'sources' / OBJECTIVE_CHECKER).read_bytes(), original)
                assembled = Path(manifest['workflow_teaching_paths'][0]).read_text(encoding='utf-8')
                self.assertNotIn(GRAMMAR + '/SKILL.md', assembled)
                self.assertNotIn(GRAMMAR + '/references/grammar-fill.md', assembled)
                self.assertFalse(manifest['answer_generation_allowed_by_course'])

    def test_reviewed_new_commit_gets_separate_cache_and_old_bundle_is_rejected(self):
        old = self.bundle()
        old_bytes = old.read_bytes()
        entry = self.repo / OBJECTIVE / 'SKILL.md'
        entry.write_bytes(entry.read_bytes() + '新课程原文\n'.encode('utf-8'))
        self.git('add', '--', OBJECTIVE + '/SKILL.md')
        self.git('-c', 'user.name=Source test', '-c', 'user.email=source-test@example.invalid',
                 'commit', '-m', 'Fixture reviewed source update')
        self.commit = self.git('rev-parse', 'HEAD').decode('ascii').strip()
        self.write_pin()
        new = self.bundle()
        self.assertNotEqual(old, new)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertEqual(verify_bundle(new)['source_repository']['commit'], self.commit)
        with self.assertRaisesRegex(source.TeachingSourceError, 'SOURCE_PIN_MISMATCH'):
            verify_bundle(old)

    def test_same_source_and_type_reuse_unchanged_cache(self):
        path = self.bundle()
        before = {p.relative_to(path.parent): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in path.parent.rglob('*') if p.is_file()}
        self.assertEqual(self.bundle(), path)
        after = {p.relative_to(path.parent): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in path.parent.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotEqual(self.bundle('应用文'), path)

    def test_dirty_source_and_new_head_require_review_without_overwrite(self):
        file = self.repo / OBJECTIVE / 'SKILL.md'
        changed = file.read_bytes() + '本人尚未提交的修改'.encode('utf-8')
        file.write_bytes(changed)
        with self.assertRaisesRegex(source.TeachingSourceError, 'WORKING_TREE_CHANGED'):
            self.bundle()
        self.assertEqual(file.read_bytes(), changed)
        self.assertFalse(self.output.exists())
        self.git('add', '.')
        self.git('-c', 'user.name=Source test', '-c', 'user.email=source-test@example.invalid',
                 'commit', '-m', 'Changed source')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(source.TeachingSourceError, 'COMMIT_CHANGED'):
            self.bundle()
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_remote_mismatch_never_falls_back_to_another_local_skill(self):
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/OTHER.git')
        with self.assertRaisesRegex(source.TeachingSourceError, 'SOURCE_MISMATCH'):
            self.bundle()
        self.assertFalse(self.output.exists())

    def test_input_tampering_is_rejected_even_when_hashes_are_updated(self):
        path = self.bundle()
        manifest = verify_bundle(path)
        input_path = Path(manifest['workflow_teaching_paths'][0])
        altered = input_path.read_bytes() + b'\nNew invented teaching condition\n'
        input_path.write_bytes(altered)
        manifest['files'][0].update(source_sha256=sha256(altered).hexdigest(),
                                    snapshot_sha256=sha256(altered).hexdigest(), bytes=len(altered))
        self.write_manifest(path, manifest)
        with self.assertRaisesRegex(source.TeachingSourceError, 'TEACHING_INPUT_CHANGED'):
            verify_bundle(path)
        with self.assertRaises(source.TeachingSourceError):
            self.bundle()  # A modified cache is not silently replaced.
        self.assertEqual(input_path.read_bytes(), altered)

    def test_rehashed_source_snapshot_must_still_match_git_commit(self):
        path = self.bundle()
        manifest = verify_bundle(path)
        item = manifest['source_files'][0]
        snapshot = path.parent / 'sources' / item['relative_path']
        snapshot.write_bytes(b'Invented source under a valid commit name\n')
        item.update(sha256=sha256(snapshot.read_bytes()).hexdigest(), bytes=snapshot.stat().st_size)
        self.write_manifest(path, manifest)
        with self.assertRaisesRegex(source.TeachingSourceError, 'COMMIT_CONTENT_MISMATCH'):
            verify_bundle(path)

    def test_manifest_cannot_promote_source_preview_to_real_acceptance(self):
        path = self.bundle('应用文')
        original = verify_bundle(path)
        for field, value in (('answer_generation_allowed_by_course', True),
                             ('course_coverage', 'ACTIVATED_WITH_BOUNDARIES'),
                             ('real_deepseek_uploaded', True), ('real_delivery_verified', True)):
            with self.subTest(field=field):
                self.write_manifest(path, original | {field: value})
                with self.assertRaisesRegex(source.TeachingSourceError, 'PERMISSION_OR_INPUT_CHANGED'):
                    verify_bundle(path)

    def test_task_checked_bundle_is_separate_and_does_not_claim_real_acceptance(self):
        preview = self.bundle()
        checked = self.bundle(for_generation=True)
        self.assertNotEqual(preview, checked)
        self.assertFalse(verify_bundle(preview)['answer_generation_allowed_by_course'])
        manifest = verify_bundle(checked, for_generation=True)
        self.assertEqual(manifest['format_version'], 3)
        self.assertEqual(manifest['required_task_checks'], ['SOURCE', 'DRAFT'])
        self.assertTrue(manifest['answer_generation_allowed_by_course'])
        self.assertFalse(manifest['real_deepseek_uploaded'])
        self.assertFalse(manifest['real_delivery_verified'])
        self.assertEqual(checked, self.bundle(for_generation=True))
        self.assertEqual((checked.parent / 'teaching-input.md').read_bytes(),
                         (preview.parent / 'teaching-input.md').read_bytes())

    def test_preview_cannot_be_promoted_by_editing_format_and_flags(self):
        path = self.bundle()
        manifest = verify_bundle(path)
        manifest.update(format_version=3, required_task_checks=['SOURCE', 'DRAFT'],
                        answer_generation_allowed_by_course=True, course_coverage='TASK_CHECKS_REQUIRED')
        self.write_manifest(path, manifest)
        with self.assertRaisesRegex(source.TeachingSourceError, 'BUILD_MODE_CACHE_MISMATCH'):
            verify_bundle(path, for_generation=True)

    def test_task_checked_build_blocks_missing_dependency_and_unsupported_shape(self):
        for kind in ('七选五', '语法填空', '应用文', '读后续写'):
            with self.subTest(kind=kind), self.assertRaisesRegex(source.TeachingSourceError, 'TASK_SHAPE_NOT_SUPPORTED'):
                self.bundle(kind, for_generation=True)
            self.assertIsNotNone(self.bundle(kind))
        self.remove_required_checker()
        with self.assertRaisesRegex(source.TeachingSourceError, 'REQUIRED_DEPENDENCIES_MISSING'):
            self.bundle(for_generation=True)

    def test_task_check_contract_cannot_be_removed(self):
        path = self.bundle(for_generation=True)
        manifest = verify_bundle(path)
        manifest['required_task_checks'] = []
        self.write_manifest(path, manifest)
        with self.assertRaisesRegex(source.TeachingSourceError, 'TASK_CHECK_CONTRACT_CHANGED'):
            verify_bundle(path, for_generation=True)

    def test_forged_allowlist_and_invalid_manifest_are_rejected(self):
        path = self.bundle()
        manifest = verify_bundle(path)
        manifest['source_files'][0]['relative_path'] = '../outside.md'
        self.write_manifest(path, manifest)
        with self.assertRaisesRegex(source.TeachingSourceError, 'ALLOWLIST_CHANGED'):
            verify_bundle(path)
        for value in ('{}', '[]', 'null', 'not-json'):
            path.write_text(value, encoding='utf-8')
            with self.assertRaises(source.TeachingSourceError):
                source.verify_answer_bundle(path)

    def test_source_preview_cannot_start_workflow_or_create_delivery(self):
        manifest = self.bundle('应用文')
        store = Store(self.base / 'anonymous.db')
        self.addCleanup(store.close)
        with self.assertRaisesRegex(ValueError, 'does not authorize'):
            Workflow(store, teaching_manifest=manifest)._skills()
        for table in ('runs', 'outbox', 'performance_units'):
            self.assertEqual(store.one('SELECT COUNT(*) FROM ' + table)[0], 0)

    def test_requests_outside_supplied_scope_require_review(self):
        for question_type, kind in (('听力', 'answer'), ('阅读理解', 'correction'),
                                    ('阅读理解', 'course_basis'), ('阅读理解', 'unverified')):
            with self.subTest(question_type=question_type, kind=kind):
                with self.assertRaises(source.TeachingSourceError):
                    self.bundle(question_type, request_kind=kind)
        self.assertFalse(self.output.exists())

    def test_read_only_git_contract_and_cli_report(self):
        output = io.StringIO()
        run = subprocess.run
        with patch.object(source.subprocess, 'run', wraps=run) as calls, redirect_stdout(output):
            exit_code = main(['--question-type', '阅读理解', '--repository', str(self.repo),
                              '--output-root', str(self.output)])
        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['status'], 'SOURCE_PREVIEW')
        self.assertFalse(report['answer_generation_allowed_by_course'])
        self.assertEqual(report['source_repository']['commit'], self.commit)
        self.assertEqual(report['missing_dependencies'], [])
        for call in calls.call_args_list:
            args = call.args[0]
            self.assertEqual(args[:3], ['git', '-C', str(self.repo)])
            self.assertIn(args[3], ('rev-parse', 'remote', 'diff', 'ls-files', 'ls-tree', 'show'))
            self.assertFalse(call.kwargs['shell'])

    def test_cli_failure_reports_review_required_without_credentials_or_traceback(self):
        output = io.StringIO()
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/OTHER.git')
        with redirect_stdout(output):
            exit_code = main(['--question-type', '阅读理解', '--repository', str(self.repo),
                              '--output-root', str(self.output)])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'REVIEW_REQUIRED',
            'error': 'ANSWER_REPOSITORY_SOURCE_MISMATCH', 'real_deepseek_uploaded': False})

    def test_redirected_repository_and_output_directory_are_rejected(self):
        try:
            import _winapi
        except ImportError:
            self.skipTest('Windows junction API is unavailable')
        linked = self.base / 'linked-repo'
        _winapi.CreateJunction(str(self.repo), str(linked))
        self.addCleanup(linked.rmdir)
        with self.assertRaisesRegex(source.TeachingSourceError, 'PATH_REDIRECTED'):
            source.build_answer_bundle(question_type='阅读理解', repository=linked, output_root=self.output)
        outside = self.base / 'outside-bundles'
        outside.mkdir()
        output_link = self.base / 'redirected-output'
        _winapi.CreateJunction(str(outside), str(output_link))
        self.addCleanup(output_link.rmdir)
        with self.assertRaisesRegex(source.TeachingSourceError, 'PATH_REDIRECTED'):
            source.build_answer_bundle(question_type='阅读理解', repository=self.repo, output_root=output_link)
        self.assertFalse(list(outside.iterdir()))


if __name__ == '__main__':
    unittest.main()
