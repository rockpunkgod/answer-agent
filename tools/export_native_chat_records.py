"""Export native English chat acquisitions without creating business records."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.native_export import export_native_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path,
                        help='Explicit destination; existing different contents are rejected')
    parser.add_argument('--scope-start-date', default='2026-09-17')
    args = parser.parse_args()
    try:
        result = export_native_records(args.root, args.output, scope_start_date=args.scope_start_date)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(1, f'Export rejected: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
