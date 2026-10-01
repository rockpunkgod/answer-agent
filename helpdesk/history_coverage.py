"""Coverage evidence is independent of counted units and membership status."""
from datetime import date
import json
from .domain import new_id
from .storage import encode, now


def ensure_schema(store):
    names = ('history_groups', 'history_coverage', 'history_inventory_reviews')
    if all(store.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
           for name in names):
        return
    store.connection.executescript('''
    CREATE TABLE IF NOT EXISTS history_groups(
      id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
      membership TEXT NOT NULL CHECK(membership IN ('UNKNOWN','ACTIVE','EXITED')),
      identity_status TEXT NOT NULL CHECK(identity_status IN ('OBSERVED','VERIFIED')),
      evidence TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS history_coverage(
      id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES history_groups(id),
      start_date TEXT NOT NULL,end_date TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('PARTIAL','COMPLETE','UNAVAILABLE')),
      evidence TEXT NOT NULL,actor TEXT NOT NULL,updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS history_inventory_reviews(
      id TEXT PRIMARY KEY,complete INTEGER NOT NULL CHECK(complete IN (0,1)),
      actor TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL
    );
    ''')


def record_observed_group(store, display_name, *, membership, evidence, verified=False):
    if not display_name or not evidence or membership not in ('UNKNOWN','ACTIVE','EXITED'):
        raise ValueError('Observed name, membership and evidence required')
    ensure_schema(store)
    gid = new_id()
    with store.transaction():
        store.execute('INSERT INTO history_groups VALUES(?,?,?,?,?,?)',
                      (gid, display_name, membership, 'VERIFIED' if verified else 'OBSERVED', encode(evidence), now()))
    return gid


def record_coverage(store, group_id, start_date, end_date, *, status, actor, evidence):
    a,b = date.fromisoformat(start_date),date.fromisoformat(end_date)
    if b < a or status not in ('PARTIAL','COMPLETE','UNAVAILABLE') or not actor or not evidence:
        raise ValueError('Valid coverage interval, actor and evidence required')
    ensure_schema(store)
    with store.transaction():
        store.execute('INSERT INTO history_coverage VALUES(?,?,?,?,?,?,?,?)',
                      (new_id(),group_id,a.isoformat(),b.isoformat(),status,encode(evidence),actor,now()))


def coverage_summary(store, start_date='2026-09-17', end_date=None, *, ensure=True):
    if ensure:
        ensure_schema(store)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date else start
    inventory = store.one('SELECT * FROM history_inventory_reviews ORDER BY created_at DESC,rowid DESC LIMIT 1')
    groups = []
    for row in store.all('SELECT * FROM history_groups ORDER BY created_at,id'):
        item = dict(row)
        records = [dict(r) for r in store.all('SELECT * FROM history_coverage WHERE group_id=? ORDER BY updated_at,id',(row['id'],))]
        # Do not infer full coverage from scattered screenshots or a conversation list.
        complete = row['identity_status']=='VERIFIED' and any(r['status']=='COMPLETE' and r['start_date']<=start.isoformat() and r['end_date']>=end.isoformat() for r in records)
        item.update(coverage_complete=complete,coverage_records=records)
        groups.append(item)
    inventory_complete = bool(inventory and inventory['complete'])
    return {'scope':'ALL_HISTORICAL_GROUPS','start_date':start.isoformat(),'end_date':end.isoformat(),
            'inventory_complete':inventory_complete,'complete':inventory_complete and bool(groups) and all(g['coverage_complete'] for g in groups),
            'groups':groups,'notes':['包括已退出群；群名和截图仅是观察证据，不冒充平台稳定群ID',
                                    '未核验全部群清单和完整时间段前，空白或零值不能表示真实零答疑']}
