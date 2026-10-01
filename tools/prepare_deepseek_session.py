"""One-shot attachment upload and readback in an already open Edge DeepSeek chat.

The output is review evidence, not a verified preparation for generation.
Never rerun with the same evidence path after an uncertain desktop action.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.mcp_transport import MCPProcess
from helpdesk.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path, help='Frozen Workflow input_json exported to a private JSON file')
    source.add_argument('--database', type=Path, help='Database containing a RUNNING frozen Workflow run')
    parser.add_argument('--run', help='Run ID; required with --database')
    parser.add_argument('--operator-task', help='Reuse this task\'s initial question-clarity review after FAST preparation; no second human approval')
    parser.add_argument('--session-url', required=True, help='Exact open DeepSeek conversation URL')
    parser.add_argument('--controls', type=Path, required=True,
                        help='JSON with reviewed upload/picker controls; preparation_mode=FAST_UPLOAD_THEN_GENERATE skips readback (candidate only); STRICT_READBACK preserves review contract')
    parser.add_argument('--evidence', type=Path, required=True,
                        help='New private JSON path; must not exist')
    args = parser.parse_args()
    if args.operator_task and (not args.database or not args.run):
        parser.error('--operator-task requires --database and --run')
    if args.snapshot:
        if args.run:
            parser.error('--run can only be used with --database')
        snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    else:
        if not args.run:
            parser.error('--run is required with --database')
        with closing(Store(args.database)) as store:
            row = store.one('SELECT input_json,state FROM runs WHERE id=?', (args.run,))
            if row is None or row['state'] != 'RUNNING':
                raise ValueError('Requires an existing RUNNING frozen generation run')
            snapshot = json.loads(row['input_json'])
    controls = json.loads(args.controls.read_text(encoding='utf-8'))
    if args.operator_task:
        if controls.get('preparation_mode') != 'FAST_UPLOAD_THEN_GENERATE':
            raise ValueError('Automatic continuation requires FAST_UPLOAD_THEN_GENERATE')
        from helpdesk.operator_tasks import OperatorTasks
        with closing(Store(args.database)) as store:
            task = OperatorTasks(store).get_task(args.operator_task)
            if task['run_id'] != args.run or snapshot.get('operator_test', {}).get('task_id') != args.operator_task:
                raise ValueError('Initial reviewed task and frozen run differ')
            expected_candidate = Path(task['preparation_path']).with_name('preparation-candidate.json').resolve()
            if args.evidence.resolve() != expected_candidate:
                raise ValueError('Automatic task candidate must use its own preparation-candidate.json')
    if args.evidence.exists():
        raise FileExistsError(f'Preparation evidence already exists: {args.evidence}')
    with MCPProcess(ROOT / '.venv-windows-mcp/Scripts/python.exe') as transport:
        result = DeepSeekSessionPreparer(transport, snapshot, args.session_url,
                                         args.evidence, controls).run()
    preparation_path = None
    if args.operator_task:
        from helpdesk.automatic_preparation import complete_automatic_preparation
        with closing(Store(args.database)) as store:
            result = complete_automatic_preparation(store, args.operator_task, args.evidence)
            preparation_path = OperatorTasks(store).get_task(args.operator_task)['preparation_path']
    print(json.dumps({'status': result['status'], 'evidence': str(args.evidence.resolve()),
                      'operator_verified': result.get('operator_verified', False),
                      'initial_input_review_reused': bool(args.operator_task),
                      'new_human_review': False,
                      'preparation': preparation_path,
                      'preparation_mode': result.get('preparation_mode'),
                      'generation_authorized': result.get('generation_authorized', False),
                      'generation_blocked_reason': result.get('generation_blocked_reason'),
                      'all_course_excerpts_match': result.get('all_course_excerpts_match'),
                      'question_stem_matches': result.get('question_stem_matches')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
