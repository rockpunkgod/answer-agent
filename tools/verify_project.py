"""Explicit fixed offline/mock regression runner and immutable evidence output."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from helpdesk.project_status import workspace_snapshot


def _now():
    return datetime.now(timezone.utc).isoformat()


def run_verification(root, output):
    root = Path(root).resolve(strict=True)
    output = Path(output)
    output = (root / output).resolve() if not output.is_absolute() else output.resolve()
    if output == root / 'artifacts' / 'verification' or not output.is_relative_to(root / 'artifacts' / 'verification'):
        raise ValueError('Output must be under artifacts/verification')
    output.mkdir(parents=True, exist_ok=False)
    before, started = workspace_snapshot(root), _now()
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    log = output / 'unittest.log'
    try:
        with log.open('wb') as stream:
            completed = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
        exit_code = completed.returncode
    except OSError:
        exit_code = 127
        log.write_text('RUNNER_PROCESS_START_FAILED\n', encoding='utf-8')
    finished, after = _now(), workspace_snapshot(root)
    text = log.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^Ran (\d+) tests? in ', text, re.M)
    total = int(match[1]) if match else 0
    skipped = len(re.findall(r'^test.* \.\.\. skipped ', text, re.M))
    summary = re.search(r'^FAILED \(([^\n]+)\)', text, re.M)
    failures = sum(int(n) for n in re.findall(r'(?:failures|errors)=(\d+)', summary[1])) if summary else 0
    # Subtest errors can exceed test count; preserve a valid failed-test count.
    failed = min(failures, max(0, total - skipped))
    if exit_code and not failed and total > skipped:
        failed = 1
    passed = max(0, total - failed - skipped)
    skip_reasons = re.findall(r'^test.* \.\.\. skipped (.*)$', text, re.M)
    successful = exit_code == 0 and total > 0 and not failed and before == after
    effective_exit = exit_code if exit_code else (0 if successful else 2)
    record = {'schema_version': 1, 'id': output.name, 'kind': 'OFFLINE_AND_MOCK_INTEGRATION',
        'command': command, 'started_at': started, 'finished_at': finished, 'exit_code': effective_exit,
        'process_exit_code': exit_code, 'results': {'total': total, 'passed': passed, 'failed': failed, 'skipped': skipped},
        'snapshot': before, 'finished_snapshot': after,
        'artifacts': [{'path': log.relative_to(root).as_posix(), 'sha256': sha256(log.read_bytes()).hexdigest()}],
        'coverage': ['Local unittest discover suite; anonymous fixtures and mocked adapters'],
        'not_covered': ['Real WeCom ingress or sending', 'Live DeepSeek/Luna execution', 'Paid provider capabilities', 'Formal report submission'],
        'skipped_reasons': skip_reasons, 'successful': successful,
        'code_changed_during_run': before != after}
    (output / 'evidence.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return effective_exit, record


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    code, record = run_verification(args.root, args.output)
    print(json.dumps({'id': record['id'], 'exit_code': code, 'results': record['results'],
                      'code_changed_during_run': record['code_changed_during_run']}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
