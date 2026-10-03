"""Read observed task costs without starting tools, migrating or creating a DB."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from helpdesk.call_costs import report, task_cost
from helpdesk.storage import Store, ensure_local_database


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--run', help='An existing run; omit for per-passage statistics')
    parser.add_argument('--max-average-cny', default='0.50')
    args = parser.parse_args(argv)
    path = Path(ensure_local_database(args.db)).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Existing local business database required')
    store = Store.__new__(Store)
    store.path = str(path)
    store.connection = sqlite3.connect('file:' + quote(path.as_posix(), safe='/:') + '?mode=ro', uri=True)
    store.connection.row_factory = sqlite3.Row
    with closing(store):
        store.execute('BEGIN')  # Read one consistent snapshot while import/generation continues.
        value = task_cost(store, args.run) if args.run else report(store, max_average_cny=args.max_average_cny)
        print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
