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
from unittest.mock import patch

from helpdesk import answer_teaching as source
from helpdesk.teaching_bundle import verify_bundle
from helpdesk.teaching_routes import GRAMMAR, OBJECTIVE, ROUTES, WRITING
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
        # A different skill's checker exists, but is never borrowed or executed.
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
        manifest = verify_bundle(self.bundle())
        self.assertEqual(manifest['missing_dependencies'], [OBJECTIVE + '/scripts/check_lesson.py'])
        self.assertEqual(manifest['course_coverage'], 'DEPENDENCY_INCOMPLETE')
        self.assertFalse(manifest['answer_generation_allowed_by_course'])
        self.assertFalse(any(item['relative_path'].startswith(GRAMMAR)
                             for item in manifest['source_files']))

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
        self.assertTrue(report['missing_dependencies'])
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
