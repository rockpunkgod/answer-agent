"""Local OPERATOR_TEST drafts/reviews/frozen runs; never submits or sends anything."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.operator_tasks import OperatorTasks
from helpdesk.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('draft')
    create.add_argument('--payload', required=True, help='UTF-8 JSON transcription file')
    create.add_argument('--intent', choices=['NEW', 'FOLLOWUP', 'CORRECTION'], default='NEW')
    create.add_argument('--parent-task')
    create.add_argument('--request-id', help='Stable client request ID for duplicate-safe creation')
    revise = commands.add_parser('revise')
    revise.add_argument('--draft', required=True)
    revise.add_argument('--payload', required=True)
    revise.add_argument('--revision', required=True, type=int)
    review = commands.add_parser('review')
    review.add_argument('--draft', required=True)
    review.add_argument('--revision', required=True, type=int)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--source-evidence', required=True)
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--task', required=True)
    freeze.add_argument('--manifest', required=True)
    freeze.add_argument('--preparation', required=True)
    freeze.add_argument('--evidence-dir', required=True)
    commands.add_parser('list')
    args = parser.parse_args()
    with closing(Store(Path(args.database).resolve())) as store:
        intake = OperatorTasks(store)
        if args.command == 'draft':
            result = intake.create_draft(json.loads(Path(args.payload).read_text(encoding='utf-8-sig')),
                                         intent=args.intent, parent_task_id=args.parent_task,
                                         request_id=args.request_id)
        elif args.command == 'revise':
            result = intake.revise(args.draft, json.loads(Path(args.payload).read_text(encoding='utf-8-sig')),
                                   expected_revision=args.revision)
        elif args.command == 'review':
            result = intake.review(args.draft, expected_revision=args.revision, reviewer=args.reviewer,
                                   source_evidence=args.source_evidence)
        elif args.command == 'freeze':
            result = intake.freeze(args.task, teaching_manifest=args.manifest,
                                   preparation_path=args.preparation, evidence_dir=args.evidence_dir)
        else:
            result = {'label': 'OPERATOR_TEST', 'drafts': intake.list_drafts(), 'tasks': intake.list_tasks()}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
