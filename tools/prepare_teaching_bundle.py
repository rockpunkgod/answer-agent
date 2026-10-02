"""Create a local course snapshot; no DeepSeek/web/desktop operations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.answer_teaching import TeachingSourceError, build_answer_bundle
from helpdesk.teaching_routes import ROUTES


def main(argv=None):
    parser = argparse.ArgumentParser(description='固定 ANSWER 原文；默认只预览，受检包要求每题核验及原脚本检查')
    parser.add_argument('--question-type', required=True, choices=tuple(ROUTES))
    parser.add_argument('--request-kind', choices=('answer', 'method', 'correction', 'course_basis', 'coverage_notice'), default='answer')
    parser.add_argument('--repository', type=Path, help='已核验的 ANSWER 检出目录；只读，不拉取或切换版本')
    parser.add_argument('--for-generation', action='store_true', help='构建阅读/完形受检包；要求先核验后教学，不代表真实网页或交付已通过')
    parser.add_argument('--output-root', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data' / 'private' / 'teaching-bundles')
    args = parser.parse_args(argv)
    try:
        manifest_path = build_answer_bundle(question_type=args.question_type, request_kind=args.request_kind,
                                            output_root=args.output_root, repository=args.repository,
                                            for_generation=args.for_generation)
    except TeachingSourceError as exc:
        print(json.dumps({'status': 'REVIEW_REQUIRED', 'error': str(exc),
                          'real_deepseek_uploaded': False}, ensure_ascii=False))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    print(json.dumps({'status': 'TASK_CHECKS_REQUIRED' if args.for_generation else 'SOURCE_PREVIEW',
                      'manifest': str(manifest_path), 'question_type': args.question_type,
                      'course_coverage': manifest['course_coverage'],
                      'file_count': len(manifest['files']),
                      'source_repository': {key: manifest['source_repository'][key] for key in ('url', 'commit')},
                      'missing_dependencies': manifest['missing_dependencies'],
                      'answer_generation_allowed_by_course': manifest['answer_generation_allowed_by_course'],
                      'real_deepseek_uploaded': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
