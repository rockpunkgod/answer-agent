"""Create a local course snapshot; no DeepSeek/web/desktop operations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.answer_teaching import TeachingSourceError, build_answer_bundle
from helpdesk.teaching_routes import ROUTES


def main(argv=None):
    parser = argparse.ArgumentParser(description='固定 ANSWER 版本的本地教学原文预览，不上传、不启用生成')
    parser.add_argument('--question-type', required=True, choices=tuple(ROUTES))
    parser.add_argument('--request-kind', choices=('answer', 'method', 'correction', 'course_basis', 'coverage_notice'), default='answer')
    parser.add_argument('--repository', type=Path, help='已核验的 ANSWER 检出目录；只读，不拉取或切换版本')
    parser.add_argument('--output-root', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data' / 'private' / 'teaching-bundles')
    args = parser.parse_args(argv)
    try:
        manifest_path = build_answer_bundle(question_type=args.question_type, request_kind=args.request_kind,
                                            output_root=args.output_root, repository=args.repository)
    except TeachingSourceError as exc:
        print(json.dumps({'status': 'REVIEW_REQUIRED', 'error': str(exc),
                          'real_deepseek_uploaded': False}, ensure_ascii=False))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    print(json.dumps({'status': 'SOURCE_PREVIEW', 'manifest': str(manifest_path), 'question_type': args.question_type,
                      'course_coverage': manifest['course_coverage'],
                      'file_count': len(manifest['files']),
                      'source_repository': {key: manifest['source_repository'][key] for key in ('url', 'commit')},
                      'missing_dependencies': manifest['missing_dependencies'],
                      'answer_generation_allowed_by_course': manifest['answer_generation_allowed_by_course'],
                      'real_deepseek_uploaded': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
