"""Search the checked-out ANSWER course package without desktop operations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.course_library import search_course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('terms', nargs='+')
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] /
                        '.tools/ANSWER-reference/kaiming-english-qa')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(search_course(args.root, args.terms, limit=args.limit), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding='utf-8')
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        print(result)


if __name__ == '__main__':
    main()
