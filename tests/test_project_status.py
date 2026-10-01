"""Anonymous project/DB fixtures only; no runtime databases or services."""
from contextlib import closing, redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.project_status import project_doctor, verify_evidence, workspace_snapshot
from tools.project_doctor import main
from tools.verify_project import run_verification


class ProjectStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'helpdesk').mkdir()
        (self.root / 'helpdesk' / 'sample.py').write_text('VALUE = 1\n')

    def tearDown(self):
        self.temp.cleanup()

    def evidence(self):
        log = self.root / 'artifacts' / 'verification' / 'fixture' / 'test.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('fixture only\n')
        record = {'schema_version': 1, 'id': 'fixture', 'kind': 'OFFLINE_AND_MOCK_INTEGRATION',
            'command': ['python', '-m', 'unittest'], 'started_at': '2026-10-01T00:00:00+00:00',
            'finished_at': '2026-10-01T00:00:01+00:00', 'exit_code': 0,
            'results': {'total': 1, 'passed': 1, 'failed': 0, 'skipped': 0},
            'snapshot': workspace_snapshot(self.root),
            'artifacts': [{'path': log.relative_to(self.root).as_posix(), 'sha256': sha256(log.read_bytes()).hexdigest()}],
            'coverage': ['anonymous fixture'], 'not_covered': ['real services'], 'skipped_reasons': []}
        path = log.parent / 'evidence.json'
        path.write_text(json.dumps(record))
        return path.relative_to(self.root).as_posix(), record

    def state(self, reference=None, **extra):
        revision = workspace_snapshot(self.root)['revision']
        state = {'schema_version': 1, 'tasks': [{'task_id': 'P1-A', 'phase_id': 'P1', 'title': 'fixture',
            'owner': 'fixture', 'dependencies': [], 'allowed_paths': ['helpdesk/sample.py'],
            'development_status': 'IMPLEMENTED', 'verification_level': 'MOCK_INTEGRATION_VERIFIED',
            'gate_passed': True, 'integration_status': 'INTEGRATED', 'evidence_stale': False,
            'acceptance_criteria': ['fixture only'], 'evidence_refs': [reference] if reference else [],
            'blocker': None, 'next_action': 'none', 'base_revision': revision, 'tested_revision': revision}],
            'runtime_databases': [], 'configuration_refs': [], 'business_rules': [], 'next_tasks': [], **extra}
        path = self.root / '.agent' / 'project-state.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(state))
        return state, path

    def test_snapshot_changes_for_add_modify_delete_and_ignores_private_artifacts(self):
        first = workspace_snapshot(self.root)
        (self.root / 'data').mkdir()
        (self.root / 'data' / 'secret.txt').write_text('must not read')
        (self.root / 'helpdesk' / '__pycache__').mkdir()
        (self.root / 'helpdesk' / '__pycache__' / 'ignore.py').write_text('ignored')
        self.assertEqual(first, workspace_snapshot(self.root))
        source = self.root / 'helpdesk' / 'new.py'
        source.write_text('x=1')
        added = workspace_snapshot(self.root)
        self.assertNotEqual(first['revision'], added['revision'])
        source.write_text('x=2')
        self.assertNotEqual(added['revision'], workspace_snapshot(self.root)['revision'])
        source.unlink()
        self.assertEqual(first, workspace_snapshot(self.root))

    def test_matching_evidence_and_source_changes(self):
        reference, _ = self.evidence()
        self.assertTrue(verify_evidence(self.root, reference)['valid'])
        source = self.root / 'helpdesk' / 'other.py'
        source.write_text('other=1')
        self.assertIn('CODE_SNAPSHOT_CHANGED', verify_evidence(self.root, reference)['reasons'])
        source.unlink()
        (self.root / 'helpdesk' / 'sample.py').write_text('VALUE = 2')
        self.assertTrue(verify_evidence(self.root, reference)['stale'])
        (self.root / 'helpdesk' / 'sample.py').unlink()
        self.assertTrue(verify_evidence(self.root, reference)['stale'])

    def test_artifact_changes_missing_malformed_failed_and_real_label_fail_closed(self):
        reference, record = self.evidence()
        artifact = self.root / record['artifacts'][0]['path']
        artifact.write_text('modified')
        self.assertIn('ARTIFACT_HASH_CHANGED', verify_evidence(self.root, reference)['reasons'])
        reference, record = self.evidence()
        path = self.root / reference
        for change in ({'exit_code': 1}, {'kind': 'REAL_INTEGRATION'},
                       {'results': {'total': 1, 'passed': 2, 'failed': 0, 'skipped': 0}}):
            path.write_text(json.dumps({**record, **change}))
            self.assertFalse(verify_evidence(self.root, reference)['valid'])
        path.write_text('invalid json')
        self.assertTrue(verify_evidence(self.root, reference)['needs_attention'])
        path.unlink()
        self.assertTrue(verify_evidence(self.root, reference)['stale'])

    def test_missing_and_invalid_state_are_limited(self):
        self.assertEqual(project_doctor(self.root)['technical_status'], 'LIMITED_STATE_MISSING')
        _, path = self.state()
        path.write_text('{"schema_version": 1, "tasks": [{}]}')
        self.assertEqual(project_doctor(self.root)['technical_status'], 'LIMITED_STATE_INVALID')

    def test_database_is_read_only_missing_database_not_created(self):
        reference, _ = self.evidence()
        database = self.root / 'fixture.db'
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE schema_migrations(version INTEGER)')
            db.execute('INSERT INTO schema_migrations VALUES(6)')
            db.execute('CREATE TABLE outbox(state TEXT, body TEXT)')
            db.execute('INSERT INTO outbox VALUES(?,?)', ('SEND_UNKNOWN', 'PRIVATE_SENTINEL'))
            db.commit()
        before, mtime = database.read_bytes(), database.stat().st_mtime_ns
        self.state(reference, runtime_databases=[{'id': 'fixture', 'path': 'fixture.db', 'role': 'business'},
            {'id': 'missing', 'path': 'missing.db', 'role': 'raw'}])
        status = project_doctor(self.root)
        self.assertEqual(status['databases'][0]['schema_version'], 6)
        self.assertEqual(status['databases'][0]['outbox_attention']['SEND_UNKNOWN'], 1)
        self.assertEqual(status['databases'][1]['status'], 'MISSING')
        self.assertFalse((self.root / 'missing.db').exists())
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(mtime, database.stat().st_mtime_ns)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(status))

    def test_configuration_secrets_never_print_and_doctor_does_not_run_services(self):
        reference, _ = self.evidence()
        config = self.root / 'fixture.toml'
        config.write_text('[scheduler]\napi_key="SECRET_SENTINEL"\nprovider="fixture"\n')
        self.state(reference, configuration_refs=[{'id': 'fixture', 'path': 'fixture.toml', 'kind': 'toml',
            'required_fields': ['scheduler.provider', 'scheduler.model_id']}])
        with patch('subprocess.run', side_effect=AssertionError('tests must not execute')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('network must not execute')):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(['--root', str(self.root), '--json']), 0)
        text = stream.getvalue()
        self.assertNotIn('SECRET_SENTINEL', text)
        status = json.loads(text)
        self.assertEqual(status['configurations'][0]['missing_required_fields'], ['scheduler.model_id'])
        self.assertEqual(status['probes_executed'], [])

    def test_effective_gate_stale_and_dependency_validation(self):
        reference, _ = self.evidence()
        state, path = self.state(reference)
        self.assertTrue(project_doctor(self.root)['tasks'][0]['gate_effective'])
        (self.root / 'helpdesk' / 'sample.py').write_text('changed')
        self.assertFalse(project_doctor(self.root)['tasks'][0]['gate_effective'])
        self.assertEqual(project_doctor(self.root)['earliest_unpassed_dependency'], 'P1-A')
        state['tasks'][0]['dependencies'] = ['missing']
        path.write_text(json.dumps(state))
        self.assertEqual(project_doctor(self.root)['technical_status'], 'LIMITED_STATE_INVALID')
        state['tasks'][0]['dependencies'] = ['P1-A']
        path.write_text(json.dumps(state))
        self.assertEqual(project_doctor(self.root)['technical_status'], 'LIMITED_STATE_INVALID')

    def test_runner_uses_fixed_command_records_hash_and_rejects_overwrite(self):
        def fixture(command, **kwargs):
            self.assertEqual(command[1:], ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
            kwargs['stdout'].write(b"test_fixture ... ok\ntest_skip ... skipped 'fixture only'\nRan 2 tests in 0.1s\nOK (skipped=1)\n")
            return type('Completed', (), {'returncode': 0})()
        with patch('tools.verify_project.subprocess.run', side_effect=fixture):
            code, record = run_verification(self.root, 'artifacts/verification/run')
        self.assertEqual(code, 0)
        self.assertEqual(record['results'], {'total': 2, 'passed': 1, 'failed': 0, 'skipped': 1})
        self.assertTrue(verify_evidence(self.root, 'artifacts/verification/run/evidence.json')['valid'])
        with self.assertRaises(FileExistsError):
            run_verification(self.root, 'artifacts/verification/run')

    def test_runner_does_not_record_success_when_code_changes_or_process_fails(self):
        def changed(command, **kwargs):
            kwargs['stdout'].write(b'Ran 1 test in 0.1s\nOK\n')
            (self.root / 'helpdesk' / 'sample.py').write_text('changed during test')
            return type('Completed', (), {'returncode': 0})()
        with patch('tools.verify_project.subprocess.run', side_effect=changed):
            code, record = run_verification(self.root, 'artifacts/verification/changed')
        self.assertEqual(code, 2)
        self.assertFalse(record['successful'])
        self.assertFalse(verify_evidence(self.root, 'artifacts/verification/changed/evidence.json')['valid'])
        with patch('tools.verify_project.subprocess.run', side_effect=OSError('fixture failure')):
            code, record = run_verification(self.root, 'artifacts/verification/failed')
        self.assertEqual(code, 127)
        self.assertFalse(record['successful'])


if __name__ == '__main__':
    unittest.main()
