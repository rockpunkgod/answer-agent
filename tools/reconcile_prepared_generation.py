"""Recover one completed DeepSeek answer from a saved Windows-MCP Snapshot.

This command reads existing evidence only. It never starts MCP or sends input.
Approval restores the original uncertain run and lets Workflow.finish record its
answer and pending review draft in one database transaction.
"""
import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.locking import resource_lock
from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


class RecoveryRejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RecoveryRejected(message)


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def frozen_payload(snapshot):
    """Rebuild literal frozen question fields independent of prompt wording."""
    def field(value):
        value = ' '.join(str(value).split())
        require(not any(char in value for char in '{}'),
                'Frozen question cannot use the prepared input method')
        return value

    question = snapshot['student_question']
    return ('题号：' + field(question.get('number', '')) + '。题干：' +
               field(question.get('verified_stem', '')) + '。' +
               ' '.join('选项' + field(option['label']) + '：' +
                        field(option.get('verified_text', ''))
                        for option in question['options']) +
               '。原文：' + field(snapshot['student_material']) +
               '。学生疑问：' + field(snapshot['student_words']))


class _SavepointStore:
    """Store proxy letting Workflow.finish nest inside one approval transaction."""

    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    @contextmanager
    def transaction(self):
        self.store.execute('SAVEPOINT recovered_finish')
        try:
            yield
        except BaseException:
            self.store.execute('ROLLBACK TO SAVEPOINT recovered_finish')
            self.store.execute('RELEASE SAVEPOINT recovered_finish')
            raise
        else:
            self.store.execute('RELEASE SAVEPOINT recovered_finish')


def _answer(page, observed, run_id):
    tree = page.inspect(observed)
    require('正在思考' not in tree and '按钮 "朗读"' in tree,
            'Snapshot does not show a completed answer')
    nodes = re.findall(r'^[ \t│├└─]*text "(.*)"\s*$', tree, re.M)
    begin = 'BEGIN_answer_run_' + run_id
    end = 'END_answer_run_' + run_id
    starts = [i for i, node in enumerate(nodes) if node == begin]
    require(len(starts) == 1, 'Final answer BEGIN marker is not unique')
    after = [i for i, node in enumerate(nodes) if node == end and i > starts[0]]
    require(len(after) == 1 and after[0] > starts[0] + 1,
            'Final answer END marker is missing or ambiguous')
    raw = '\n'.join(nodes[starts[0] + 1:after[0]]).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecoveryRejected('Captured final answer is not valid JSON') from exc
    require(isinstance(data, dict) and set(data) == {'option_label', 'text'} and
            isinstance(data['text'], str) and data['text'].strip(),
            'Captured final answer does not meet the JSON contract')
    return data


def reconcile(store, run_id, attempt_path, staged_type_result_path, snapshot_result_path, preparation_path,
              manifest_path, *, approved=False):
    require(approved, 'Explicit --approve-captured-output is required')
    require(isinstance(run_id, str) and re.fullmatch(r'[a-zA-Z0-9]{12,64}', run_id),
            'Invalid frozen run ID')
    lock = str(Path(store.path).resolve()) + '.prepared-' + sha256(run_id.encode()).hexdigest() + '.lock'
    with resource_lock(lock, timeout=5):
        return _reconcile_locked(store, run_id, attempt_path, staged_type_result_path, snapshot_result_path,
                                 preparation_path, manifest_path)


def _reconcile_locked(store, run_id, attempt_path, staged_type_result_path, snapshot_result_path,
                      preparation_path, manifest_path):
    run = store.one('SELECT * FROM runs WHERE id=?', (run_id,))
    require(run is not None, 'Unknown frozen run')
    prior = store.one('SELECT id,answer_id,state FROM outbox WHERE run_id=?', (run_id,))
    saved = store.one('SELECT a.id,a.state FROM answers a JOIN answer_evidence e ON e.answer_id=a.id '
                      'WHERE e.run_id=?', (run_id,))
    if prior or saved:
        return {'existing_answer': saved['id'] if saved else prior['answer_id'],
                'existing_outbox': prior['id'] if prior else None, 'resubmitted': False}
    require(run['state'] == 'REJECTED' and run['error'] == 'GENERATION_UNCERTAIN',
            'Run is not rejected with uncertain generation outcome')
    snapshot = json.loads(run['input_json'])
    identity = PreparedDeepSeekGenerator.identity
    require(snapshot.get('run_id') == run_id and snapshot.get('generation_adapter') == identity and
            snapshot.get('simulated') is False and snapshot.get('session_id') == run['session_id'] and
            snapshot.get('question_id') == run['question_id'] and
            snapshot.get('question_version') == run['question_version'] and
            snapshot.get('context_revision') == run['context_revision'],
            'Frozen run identity or context mismatch')
    session = store.one('SELECT * FROM sessions WHERE id=?', (run['session_id'],))
    require(session is not None and session['state'] == 'ACTIVE' and
            session['adapter'] == identity and session['case_id'] == snapshot['case_id'],
            'Prepared session is no longer active for this run')
    question = store.one('SELECT current_version,context_revision FROM questions WHERE id=?',
                         (run['question_id'],))
    require(question is not None and
            (question['current_version'], question['context_revision']) ==
            (run['question_version'], run['context_revision']),
            'Frozen question version is stale')
    prep_path = Path(preparation_path).resolve(strict=True)
    prep = json.loads(prep_path.read_text(encoding='utf-8'))
    require(prep.get('run_id') == run_id and
            prep.get('input_fingerprint') == input_fingerprint(snapshot),
            'Preparation belongs to another frozen run')
    generator = PreparedDeepSeekGenerator(None, prep_path, prep_path.parent)
    verified_prep, page = generator._preparation(snapshot)
    attempt_path = Path(attempt_path).resolve(strict=True)
    require(attempt_path.name == run_id + '.json', 'Original attempt filename does not match run')
    attempt = json.loads(attempt_path.read_text(encoding='utf-8'))
    require(attempt.get('run_id') == run_id and
            attempt.get('session_url') == page.url and
            attempt.get('input_fingerprint') == input_fingerprint(snapshot) and
            attempt.get('status') == 'OUTCOME_REQUIRES_REVIEW' and
            attempt.get('automatic_retry_allowed') is False and
            re.fullmatch(r'[0-9a-f]{64}', str(attempt.get('prompt_sha256', ''))) is not None,
            'Original single-submission attempt is not verified')
    type_path = Path(staged_type_result_path).resolve(strict=True)
    typed = json.loads(type_path.read_text(encoding='utf-8'))
    type_id = typed.get('attempt_id')
    require(isinstance(type_id, str) and re.fullmatch(r'[0-9a-f]{32}', type_id),
            'Staged Type result lacks a Windows-MCP attempt ID')
    type_journal_path = type_path.parent / ('attempt-' + type_id + '.json')
    type_journal = json.loads(type_journal_path.read_text(encoding='utf-8'))
    type_args = type_journal.get('arguments')
    require(type_journal.get('attempt_id') == type_id and type_journal.get('tool') == 'Type' and
            type_journal.get('status') == 'TOOL_RETURNED' and
            Path(type_journal.get('result_path', '')).resolve() == type_path and
            typed.get('tool') == 'Type' and typed.get('is_error') is False and
            isinstance(type_args, dict) and type_args.get('press_enter') is False and
            isinstance(type_args.get('text'), str),
            'Staged Type provenance is not verified')
    staged_text = type_args['text']
    token = 'answer_run_' + run_id
    require(sha256(staged_text.encode()).hexdigest() == attempt['prompt_sha256'] and
            frozen_payload(snapshot) in staged_text and
            'BEGIN_' + token in staged_text and 'END_' + token in staged_text and
            '\n' not in staged_text and '\r' not in staged_text,
            'Staged prompt does not match original attempt and frozen input')
    result_path = Path(snapshot_result_path).resolve(strict=True)
    observed = json.loads(result_path.read_text(encoding='utf-8'))
    attempt_id = observed.get('attempt_id')
    require(isinstance(attempt_id, str) and re.fullmatch(r'[0-9a-f]{32}', attempt_id),
            'Snapshot lacks a Windows-MCP attempt ID')
    journal_path = result_path.parent / ('attempt-' + attempt_id + '.json')
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    require(journal.get('attempt_id') == attempt_id and journal.get('tool') == 'Snapshot' and
            journal.get('arguments') == {'use_dom': True, 'use_vision': False} and
            journal.get('status') == 'TOOL_RETURNED' and
            Path(journal.get('result_path', '')).resolve() == result_path and
            observed.get('tool') == 'Snapshot' and observed.get('is_error') is False,
            'Snapshot provenance or read-only arguments are not verified')
    data = _answer(page, observed, run_id)
    matches = [x for x in snapshot['student_question']['options']
               if x['label'] == data['option_label']]
    require(len(matches) == 1, 'Captured option is absent from frozen question')
    workflow = Workflow(_SavepointStore(store), generation_adapter=generator,
                        teaching_manifest=manifest_path)
    current_skills = {x['path']: x['sha256'] for x in workflow._skills()}
    require(current_skills == verified_prep['uploaded_teaching_hashes'],
            'Teaching manifest changed from prepared session')
    require(input_fingerprint(workflow.app.context(run['turn_id'])) ==
            input_fingerprint(snapshot), 'Current input differs from frozen run')
    result = {'adapter': identity, 'simulated': False, 'run_id': run_id,
              'session_id': run['session_id'], 'complete': True, 'uploads_confirmed': True,
              'uploaded_teaching_hashes': verified_prep['uploaded_teaching_hashes'],
              'web_session_evidence': str(attempt_path),
              'correct_option_id': matches[0]['id'], 'text': data['text']}
    proof = {'source': 'WINDOWS_MCP_SNAPSHOT_RECOVERY', 'attempt': str(attempt_path),
             'attempt_sha256': digest(attempt_path), 'snapshot_result': str(result_path),
             'snapshot_sha256': digest(result_path), 'snapshot_journal': str(journal_path),
             'snapshot_journal_sha256': digest(journal_path), 'preparation': str(prep_path),
             'preparation_sha256': digest(prep_path), 'prompt_sha256': attempt['prompt_sha256'],
             'staged_type_result': str(type_path), 'staged_type_sha256': digest(type_path),
             'staged_type_journal': str(type_journal_path),
             'staged_type_journal_sha256': digest(type_journal_path),
             'option_label': data['option_label'], 'reviewer': verified_prep.get('reviewer'),
             'review_approved': True}
    with store.transaction():
        changed = store.execute("UPDATE runs SET state='RUNNING',error=NULL,completed_at=NULL "
                                "WHERE id=? AND state='REJECTED' AND error='GENERATION_UNCERTAIN'",
                                (run_id,)).rowcount
        require(changed == 1, 'Run changed during recovery')
        finished = workflow.finish(run_id, result)
        require(finished.get('state') == 'GENERATED',
                'Recovered output failed workflow validation')
        workflow._event('GENERATION_RECOVERED_FROM_SNAPSHOT', run=run_id,
                        outbox=finished['outbox_id'], details=proof)
        turn = store.one('SELECT message_id FROM turns WHERE id=?', (run['turn_id'],))
        store.execute("UPDATE human_tasks SET state='RESOLVED' WHERE message_id=? "
                      "AND reason='GENERATION_UNCERTAIN' AND state='OPEN'", (turn['message_id'],))
    return {**finished, 'recovered_run': run_id, 'capture_proof': proof,
            'resubmitted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--attempt', type=Path, required=True)
    parser.add_argument('--staged-type-result', type=Path, required=True)
    parser.add_argument('--snapshot-result', type=Path, required=True)
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--approve-captured-output', action='store_true', required=True)
    args = parser.parse_args()
    with closing(Store(args.database)) as store:
        result = reconcile(store, args.run, args.attempt, args.staged_type_result,
                           args.snapshot_result,
                           args.preparation, args.manifest,
                           approved=args.approve_captured_output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
