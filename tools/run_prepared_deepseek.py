"""Generate one persisted turn or resume a frozen RUNNING run in a prepared Edge session.

Does not send WeCom messages. Re-running the same turn never re-submits it.
"""
import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.mcp_transport import MCPProcess
from helpdesk.locking import resource_lock
from helpdesk.storage import Store, now
from helpdesk.workflow import Workflow


def run_existing(store, run_id, preparation_path, manifest, *,
                 evidence_dir=None, transport_factory=None):
    """Submit a frozen run once. All rejection checks precede desktop startup."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError('A frozen run ID is required')
    lock_path = (str(Path(store.path).resolve()) + '.prepared-' +
                 sha256(run_id.encode('utf-8')).hexdigest() + '.lock')
    with resource_lock(lock_path, timeout=5):
        return _run_existing_locked(store, run_id, preparation_path, manifest,
                                    evidence_dir=evidence_dir,
                                    transport_factory=transport_factory)


def _run_existing_locked(store, run_id, preparation_path, manifest, *,
                         evidence_dir=None, transport_factory=None):
    row = store.one('SELECT * FROM runs WHERE id=?', (run_id,))
    if row is None:
        raise ValueError('Unknown frozen run')
    if row['state'] != 'RUNNING':
        return {'existing_run': {'id': run_id, 'state': row['state'], 'error': row['error']},
                'resubmitted': False}
    Workflow(store)._require_confirmed_ack(row['turn_id'])
    snapshot = json.loads(row['input_json'])
    identity = PreparedDeepSeekGenerator.identity
    if (snapshot.get('run_id') != run_id or snapshot.get('generation_adapter') != identity
            or snapshot.get('simulated') is not False or snapshot.get('session_id') != row['session_id']
            or snapshot.get('question_id') != row['question_id']
            or snapshot.get('question_version') != row['question_version']
            or snapshot.get('context_revision') != row['context_revision']):
        raise ValueError('Frozen run adapter or context mismatch')
    session = store.one('SELECT * FROM sessions WHERE id=?', (row['session_id'],))
    if (session is None or session['state'] != 'ACTIVE' or session['adapter'] != identity
            or session['case_id'] != snapshot.get('case_id')):
        raise ValueError('Frozen run session is not the active prepared adapter session')
    question = store.one('SELECT current_version,context_revision FROM questions WHERE id=?',
                         (row['question_id'],))
    if (question is None or (question['current_version'], question['context_revision']) !=
            (row['question_version'], row['context_revision'])):
        raise ValueError('Frozen question context is stale')
    preparation_path = Path(preparation_path)
    preparation = json.loads(preparation_path.read_text(encoding='utf-8'))
    if (preparation.get('run_id') != run_id or
            preparation.get('input_fingerprint') != input_fingerprint(snapshot)):
        raise ValueError('Preparation belongs to a different frozen run')
    evidence_dir = Path(evidence_dir) if evidence_dir else ROOT / 'data/private/mcp-generation'
    attempt_path = evidence_dir / (run_id + '.json')
    if attempt_path.exists():
        attempt = json.loads(attempt_path.read_text(encoding='utf-8'))
        if attempt.get('run_id') != run_id:
            raise ValueError('Existing attempt belongs to another run')
        return {'existing_attempt': {'run_id': run_id, 'status': attempt.get('status'),
                                    'evidence': str(attempt_path.resolve())},
                'resubmitted': False}
    generator = PreparedDeepSeekGenerator(None, preparation_path, evidence_dir)
    generator._preparation(snapshot)
    workflow = Workflow(store, generation_adapter=generator, teaching_manifest=manifest)
    if input_fingerprint(workflow.app.context(row['turn_id'])) != input_fingerprint(snapshot):
        raise ValueError('Current turn no longer matches frozen input')
    current_skills = {x['path']: x['sha256'] for x in workflow._skills()}
    frozen_skills = {x['path']: x['sha256'] for x in snapshot['teaching_skills']}
    if current_skills != frozen_skills:
        raise ValueError('Teaching manifest changed since frozen run')
    if workflow._stopped():
        raise ValueError('Workflow is stopped')
    workflow._require_confirmed_ack(row['turn_id'])
    if transport_factory is None:
        transport_factory = MCPProcess
    try:
        with transport_factory() as transport:
            generator.transport = transport
            result = generator.generate(snapshot)
            if not isinstance(result, dict):
                raise TypeError('Generation adapter must return a result dict')
    except Exception:
        # An external action may have committed before an error surfaced.
        with store.transaction():
            store.execute("UPDATE runs SET state='REJECTED',error='GENERATION_UNCERTAIN',completed_at=? "
                          "WHERE id=? AND state='RUNNING'", (now(), run_id))
            turn = store.one('SELECT message_id FROM turns WHERE id=?', (row['turn_id'],))
            workflow._human(turn['message_id'], 'GENERATION_UNCERTAIN')
            store.execute("UPDATE questions SET status='REVIEW' WHERE id=?", (row['question_id'],))
            workflow._event('GENERATION_FINISHED', run=run_id,
                            details={'state': 'REJECTED', 'reason': 'GENERATION_UNCERTAIN',
                                     'adapter': identity, 'simulated': False})
        return {'answer_id': None, 'outbox_id': None, 'state': 'REJECTED',
                'reason': 'GENERATION_UNCERTAIN'}
    return workflow.finish(run_id, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--turn', help='Start a new run only if this turn has none')
    target.add_argument('--run', help='Resume an existing frozen RUNNING run without creating another')
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    # Avoid opening a second desktop server just to report a previously run turn.
    with closing(Store(args.database)) as store:
        if args.run:
            print(json.dumps(run_existing(store, args.run, args.preparation, args.manifest),
                             ensure_ascii=False))
            return
        prior = store.one('SELECT id,state,error FROM runs WHERE turn_id=? ORDER BY rowid DESC LIMIT 1', (args.turn,))
        if prior:
            print(json.dumps({'existing_run': dict(prior), 'resubmitted': False}))
            return
        Workflow(store)._require_confirmed_ack(args.turn)
        with MCPProcess() as transport:
            generator = PreparedDeepSeekGenerator(transport, args.preparation,
                                                  ROOT / 'data/private/mcp-generation')
            workflow = Workflow(store, generation_adapter=generator, teaching_manifest=args.manifest)
            result = workflow.generate(args.turn)
            print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
