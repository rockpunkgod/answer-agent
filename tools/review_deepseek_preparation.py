"""Approve a completed DeepSeek preparation candidate after explicit human review.

This command is offline. The review JSON must name the reviewer and contain:
source_verified_excerpts (course absolute path -> exact excerpt),
reviewed_image_hashes (both image absolute paths -> frozen SHA-256),
verified_question_stem, and image_review_statement exactly equal to
"I inspected both frozen question images and verified the question stem."
For image counts other than two, replace "both" with "all".
For zero images, omit image attestations and independently inspect the original
question source. Supply reviewed_question_text {passage,number,stem,options A-D},
reviewed_input_fingerprint, source_review_evidence (original source locator), and
question_text_review_statement exactly:
"I independently reviewed the original question source and verified the frozen passage, stem, number and A-D options."
The immutable question-text attachment must exist under the candidate folder;
model readback is never a substitute for this independent source review.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.mcp_preparation_review import review_preparation
from helpdesk.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path, help='Frozen Workflow input_json')
    source.add_argument('--database', type=Path, help='Database containing the frozen RUNNING run')
    parser.add_argument('--run', help='Run ID; required with --database')
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--preparation', type=Path, required=True, help='New verified preparation JSON path')
    parser.add_argument('--readback-evidence', type=Path, required=True, help='New raw snapshot JSON path')
    parser.add_argument('--approve-verified-upload-and-input', action='store_true', required=True,
                        help='Explicitly approve checked uploads and independently reviewed question input')
    args = parser.parse_args()
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
    result = review_preparation(snapshot, args.candidate, args.review,
                                args.preparation, args.readback_evidence)
    print(json.dumps({'status': result['status'], 'preparation': str(args.preparation.resolve()),
                      'readback_evidence': result['readback_evidence']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
