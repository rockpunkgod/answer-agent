"""Durably bind an exact browser chat to one student/question generation session.

Legacy sessions are never inferred or rewritten: they require a new owned session.
Standalone page-contract fixtures omit session_store_path and have no persistence.
"""
import json
from contextlib import nullcontext

from .storage import Store, now


def claim_deepseek_chat(snapshot, session_url, *, store_path=None, reserve=True):
    """Reserve before any upload/submit; keep ownership even after failures."""
    path = store_path or snapshot.get('session_store_path')
    if path is None:
        return  # Pure offline page fixtures have no workflow database.
    store = Store(path)
    try:
        with store.transaction() if reserve else nullcontext():
            row = store.one("""SELECT r.*,s.case_id,s.state AS session_state,o.binding_id,
                o.question_id AS owned_question,c.binding_id AS case_binding
                FROM runs r JOIN sessions s ON s.id=r.session_id
                JOIN session_owners o ON o.session_id=s.id JOIN cases c ON c.id=s.case_id
                WHERE r.id=?""", (snapshot.get('run_id'),))
            if not row:
                raise ValueError('SESSION_OWNERSHIP_MISSING')
            frozen = json.loads(row['input_json'])
            if ((reserve and row['state'] != 'RUNNING') or row['session_state'] != 'ACTIVE'
                    or row['session_id'] != snapshot.get('session_id')
                    or row['case_id'] != snapshot.get('case_id')
                    or row['owned_question'] != row['question_id']
                    or row['question_id'] != snapshot.get('question_id')
                    or row['binding_id'] != row['case_binding']
                    or row['binding_id'] != snapshot.get('binding_id', row['case_binding'])
                    or frozen != snapshot):
                raise ValueError('SESSION_OWNERSHIP_MISMATCH')
            from .reference_resolution import validate_reference_snapshot
            validate_reference_snapshot(store, snapshot)
            existing = store.one('SELECT session_id FROM deepseek_chats WHERE session_url=?', (session_url,))
            if existing and existing['session_id'] != row['session_id']:
                raise ValueError('DEEPSEEK_CHAT_ALREADY_OWNED')
            owned = store.one('SELECT session_url FROM deepseek_chats WHERE session_id=?', (row['session_id'],))
            if owned and owned['session_url'] != session_url:
                raise ValueError('DEEPSEEK_SESSION_URL_CHANGED')
            if not existing and reserve:
                store.execute('INSERT INTO deepseek_chats VALUES(?,?,?,?)',
                              (session_url, row['session_id'], row['id'], now()))
    finally:
        store.close()
