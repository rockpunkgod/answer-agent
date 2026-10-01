"""Create a local course snapshot; no DeepSeek/web/desktop operations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.teaching_bundle import TYPE_MODULES, build_bundle


def main():
    parser = argparse.ArgumentParser(description='显式题型的本地课程白名单快照')
    parser.add_argument('--question-type', required=True, choices=tuple(TYPE_MODULES))
    parser.add_argument('--request-kind', choices=('answer', 'course_basis', 'coverage_notice'), default='answer')
    parser.add_argument('--output-root', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data' / 'private' / 'teaching-bundles')
    args = parser.parse_args()
    manifest_path = build_bundle(question_type=args.question_type, request_kind=args.request_kind,
                                 output_root=args.output_root)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    print(json.dumps({'manifest': str(manifest_path), 'question_type': args.question_type,
                      'course_coverage': manifest['course_coverage'],
                      'file_count': len(manifest['files']),
                      'real_deepseek_uploaded': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
