"""Project evidence and readiness inspection without business side effects."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tomllib

EXTENSIONS = {'.py', '.sql', '.js', '.css', '.html', '.md', '.ps1', '.txt', '.toml'}
EXCLUDED = {'__pycache__', '.venv', 'private', 'data', 'outputs', 'docs', '.agent', 'artifacts'}
DEVELOPMENT = {'NOT_STARTED', 'IN_PROGRESS', 'IMPLEMENTED', 'BLOCKED'}
VERIFICATION = {'UNVERIFIED', 'UNIT_VERIFIED', 'MOCK_INTEGRATION_VERIFIED', 'REAL_INTEGRATION_VERIFIED'}


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _local(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('Relative workspace path required')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path outside workspace')
    return path


def workspace_snapshot(root):
    root = Path(root).resolve(strict=True)
    paths = []
    for directory in ('helpdesk', 'tests', 'tools', 'prompts'):
        base = root / directory
        if base.is_dir() and not base.is_symlink():
            for path in base.rglob('*'):
                relative = path.relative_to(root)
                if any(part in EXCLUDED for part in relative.parts):
                    continue
                if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
                    raise ValueError('Snapshot symlink requires review')
                if path.is_file() and path.suffix.lower() in EXTENSIONS:
                    paths.append(path)
    for path in root.iterdir():
        if (path.name in {'pyproject.toml', 'config.example.toml', 'run_current_demo.ps1', 'run_demo.ps1',
                          'uv.lock', 'poetry.lock', 'Pipfile.lock'}
                or (path.name.startswith('requirements') and path.suffix in {'.txt', '.lock'})):
            if path.is_symlink():
                raise ValueError('Snapshot symlink requires review')
            if path.is_file():
                paths.append(path)
    files = {path.relative_to(root).as_posix(): _hash(path)
             for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix())}
    digest = sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'revision': 'sha256:' + digest, 'files': files, 'algorithm': 'sha256'}


def verify_evidence(root, reference, *, snapshot=None):
    root = Path(root).resolve(strict=True)
    result = {'valid': False, 'stale': True, 'needs_attention': True, 'reasons': [],
              'verification_level': 'UNVERIFIED'}
    try:
        record = json.loads(_local(root, reference).read_text(encoding='utf-8'))
        required = {'schema_version', 'id', 'kind', 'command', 'started_at', 'finished_at',
                    'exit_code', 'results', 'snapshot', 'artifacts', 'coverage', 'not_covered', 'skipped_reasons'}
        if (not isinstance(record, dict) or not required <= record.keys()
                or type(record['schema_version']) is not int or record['schema_version'] != 1):
            raise ValueError('INVALID_METADATA')
        if not all(isinstance(record[k], str) and record[k] for k in ('id', 'kind')):
            raise ValueError('INVALID_METADATA')
        if not isinstance(record['command'], list) or not record['command'] or not all(isinstance(x, str) and x for x in record['command']):
            raise ValueError('INVALID_COMMAND')
        start, end = (datetime.fromisoformat(record[k]) for k in ('started_at', 'finished_at'))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('INVALID_TIMESTAMPS')
        counts = record['results']
        if (not isinstance(counts, dict) or set(counts) != {'total', 'passed', 'failed', 'skipped'}
                or any(type(n) is not int or n < 0 for n in counts.values())
                or counts['total'] != counts['passed'] + counts['failed'] + counts['skipped']):
            raise ValueError('INVALID_COUNTS')
        if type(record['exit_code']) is not int:
            raise ValueError('INVALID_EXIT_CODE')
        for key in ('coverage', 'not_covered', 'skipped_reasons'):
            if not isinstance(record[key], list) or not all(isinstance(x, str) for x in record[key]):
                raise ValueError('INVALID_SCOPE')
        if counts['skipped'] and not record['skipped_reasons']:
            raise ValueError('MISSING_SKIP_REASONS')
        current = snapshot or workspace_snapshot(root)
        if record['snapshot'] != current or record.get('finished_snapshot', current) != current:
            result['reasons'].append('CODE_SNAPSHOT_CHANGED')
        if not isinstance(record['artifacts'], list) or not record['artifacts']:
            raise ValueError('MISSING_ARTIFACTS')
        for item in record['artifacts']:
            if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
                raise ValueError('INVALID_ARTIFACT')
            if _hash(_local(root, item['path'])) != item['sha256']:
                result['reasons'].append('ARTIFACT_HASH_CHANGED')
        if record['exit_code'] != 0 or counts['failed'] or not counts['passed']:
            result['reasons'].append('TESTS_NOT_SUCCESSFUL')
        # A label cannot prove real services were exercised. This verifier supports
        # the fixed offline/mock runner only; other evidence remains supporting data.
        if record['kind'] != 'OFFLINE_AND_MOCK_INTEGRATION':
            result['reasons'].append('UNSUPPORTED_EVIDENCE_KIND')
        if not result['reasons']:
            result.update(valid=True, stale=False, needs_attention=False,
                          verification_level='MOCK_INTEGRATION_VERIFIED')
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result['reasons'].append('MISSING_OR_MALFORMED_EVIDENCE')
    return result


def _database(root, entry):
    result = {'id': entry.get('id'), 'role': entry.get('role'), 'status': 'MISSING'}
    try:
        path = _local(root, entry['path'])
        if not path.is_file():
            return result
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            quote = lambda name: '"' + name.replace('"', '""') + '"'
            result['table_counts'] = {name: db.execute('SELECT COUNT(*) FROM ' + quote(name)).fetchone()[0] for name in tables}
            result['schema_version'] = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] if 'schema_migrations' in tables else None
            if 'outbox' in tables:
                columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
                if 'state' in columns:
                    result['outbox_attention'] = {state: db.execute('SELECT COUNT(*) FROM outbox WHERE state=?', (state,)).fetchone()[0]
                        for state in ('PENDING', 'SENDING', 'SEND_UNKNOWN', 'FAILED')}
            result['status'] = 'READ_ONLY_OK'
        finally:
            db.close()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        result['status'] = 'UNREADABLE_OR_INVALID'
    return result


def _configuration(root, entry):
    result = {'id': entry.get('id'), 'status': 'MISSING', 'present_fields': [], 'missing_required_fields': []}
    try:
        path = _local(root, entry['path'])
        if not path.is_file():
            return result
        if entry['kind'] == 'toml':
            with path.open('rb') as stream:
                value = tomllib.load(stream)
        elif entry['kind'] == 'json':
            value = json.loads(path.read_text(encoding='utf-8'))
        else:
            raise ValueError()
        if not isinstance(value, dict):
            raise ValueError()
        fields = {}
        def visit(mapping, prefix=''):
            for key, child in mapping.items():
                name = prefix + str(key)
                if isinstance(child, dict):
                    visit(child, name + '.')
                else:
                    fields[name] = child is not None and child != '' and child != 'UNCONFIGURED'
        visit(value)
        result['present_fields'] = sorted(fields)
        result['field_presence'] = {key: fields[key] for key in sorted(fields)}
        required = entry.get('required_fields', ['scheduler.provider', 'scheduler.base_url', 'scheduler.model_id']
                             if isinstance(value.get('scheduler'), dict) else [])
        if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
            raise ValueError()
        result['missing_required_fields'] = [key for key in required if not fields.get(key)]
        result['status'] = 'FIELDS_INSPECTED_NOT_CONNECTION_VERIFIED'
    except (OSError, ValueError, TypeError, KeyError):
        result['status'] = 'UNREADABLE_OR_INVALID'
    return result


def project_doctor(root, state_path='.agent/project-state.json'):
    root = Path(root).resolve(strict=True)
    snapshot = workspace_snapshot(root)
    result = {'schema_version': 1, 'read_only': True, 'snapshot': snapshot,
              'technical_status': 'LIMITED_STATE_MISSING', 'business_verification_status': 'UNVERIFIED',
              'tasks': [], 'databases': [], 'configurations': [], 'earliest_unpassed_dependency': None,
              'next_tasks': [], 'probes_executed': []}
    try:
        path = _local(root, state_path)
        if not path.is_file():
            return result
        state = json.loads(path.read_text(encoding='utf-8'))
        required = {'task_id', 'phase_id', 'title', 'owner', 'dependencies', 'allowed_paths',
                    'development_status', 'verification_level', 'gate_passed', 'integration_status',
                    'evidence_stale', 'acceptance_criteria', 'evidence_refs', 'blocker', 'next_action',
                    'base_revision', 'tested_revision'}
        if (not isinstance(state, dict) or type(state.get('schema_version')) is not int or state.get('schema_version') != 1
                or not isinstance(state.get('tasks'), list)):
            raise ValueError()
        ids = set()
        for task in state['tasks']:
            if (not isinstance(task, dict) or not required <= task.keys()
                    or not isinstance(task['task_id'], str) or not task['task_id'] or task['task_id'] in ids
                    or not isinstance(task['phase_id'], str)
                    or task['development_status'] not in DEVELOPMENT or task['verification_level'] not in VERIFICATION
                    or type(task['gate_passed']) is not bool or type(task['evidence_stale']) is not bool
                    or any(not isinstance(task[k], list) or not all(isinstance(x, str) for x in task[k])
                           for k in ('dependencies', 'allowed_paths', 'evidence_refs'))):
                raise ValueError()
            ids.add(task['task_id'])
        if any(dep not in ids for task in state['tasks'] for dep in task['dependencies']):
            raise ValueError()
        graph = {task['task_id']: task['dependencies'] for task in state['tasks']}
        remaining = set(graph)
        while remaining:
            ready = {key for key in remaining if not (set(graph[key]) & remaining)}
            if not ready:
                raise ValueError('Cyclic dependencies')
            remaining -= ready
        for key in ('runtime_databases', 'configuration_refs', 'next_tasks'):
            if not isinstance(state.get(key, []), list):
                raise ValueError()
        if any(not isinstance(entry, dict) for key in ('runtime_databases', 'configuration_refs') for entry in state.get(key, [])):
            raise ValueError()
    except (OSError, ValueError, TypeError, KeyError):
        result['technical_status'] = 'LIMITED_STATE_INVALID'
        return result
    for task in state['tasks']:
        evidence = [verify_evidence(root, ref, snapshot=snapshot) for ref in task['evidence_refs']]
        stale = task['evidence_stale'] or not evidence or any(e['stale'] for e in evidence) or task['tested_revision'] != snapshot['revision']
        effective = task['gate_passed'] and not stale and task['development_status'] == 'IMPLEMENTED'
        # REAL labels cannot be promoted by offline evidence.
        if task['verification_level'] in {'REAL_INTEGRATION_VERIFIED', 'UNVERIFIED'}:
            effective = False
        result['tasks'].append({'task_id': task['task_id'], 'phase_id': task['phase_id'],
            'title': task['title'], 'owner': task['owner'],
            'development_status': task['development_status'], 'claimed_verification_level': task['verification_level'],
            'evidence_stale': stale, 'gate_effective': effective, 'evidence': evidence,
            'integration_status': task['integration_status'], 'blocker': task['blocker'],
            'next_action': task['next_action'], 'dependencies': task['dependencies']})
    effective_ids = {t['task_id'] for t in result['tasks'] if t['gate_effective']}
    # Evaluate dependency gates, even when an individual task's evidence matches.
    changed = True
    while changed:
        changed = False
        for task in result['tasks']:
            if task['task_id'] in effective_ids and any(dep not in effective_ids for dep in task['dependencies']):
                effective_ids.remove(task['task_id']); task['gate_effective'] = False; changed = True
    unmet = [t for t in result['tasks'] if not t['gate_effective']]
    earliest = sorted(unmet, key=lambda t: (t['phase_id'], t['task_id']))
    result['earliest_unpassed_dependency'] = earliest[0]['task_id'] if earliest else None
    result['next_tasks'] = [t['task_id'] for t in state['tasks'] if t['task_id'] not in effective_ids
                            and all(dep in effective_ids for dep in t['dependencies'])]
    result['databases'] = [_database(root, entry) for entry in state.get('runtime_databases', [])]
    result['configurations'] = [_configuration(root, entry) for entry in state.get('configuration_refs', [])]
    result['technical_status'] = 'INSPECTED_WITH_ATTENTION' if (unmet or any(d['status'] != 'READ_ONLY_OK'
        or any(d.get('outbox_attention', {}).get(key, 0) for key in ('SENDING', 'SEND_UNKNOWN', 'FAILED')) for d in result['databases'])
        or any(c['status'] != 'FIELDS_INSPECTED_NOT_CONNECTION_VERIFIED' or c['missing_required_fields'] for c in result['configurations'])) else 'LOCAL_CHECKS_OK'
    result['business_verification_status'] = 'REAL_INTEGRATION_NOT_ESTABLISHED'
    return result
