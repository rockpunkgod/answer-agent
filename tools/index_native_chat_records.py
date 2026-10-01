"""Index native chat acquisitions locally; no messages or performance are created."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.native_intake import index_native_records
from helpdesk.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Use an existing explicit database; this command does not create a business database')
    with closing(Store(args.database)) as store:
        result = index_native_records(store, args.root, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['rejected']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
