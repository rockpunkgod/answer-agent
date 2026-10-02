"""Ordered text delivery using the original Outbox, audit and receipt tables.

The answer body stays immutable. A plan stores offsets, hashes and transport IDs;
part receipts never create another completion record or performance unit.
"""
from dataclasses import replace
from hashlib import sha256
import json

from .domain import new_id
from .performance_rules import timestamp
from .storage import encode, now


PLAN_EVENT = 'DELIVERY_BATCH_PLANNED'
START_EVENT = 'DELIVERY_PART_STARTED'
PART_METHOD = 'ORDERED_TEXT_PART'
COMPLETE_METHOD = 'ORDERED_TEXT_BATCH'
FORMATTER = 'lossless-utf16-v1'
DEFAULT_LIMIT = 2000
SCOPE = ('id', 'answer_id', 'run_id', 'message_id', 'turn_id', 'case_id',
         'binding_id', 'question_version', 'context_revision', 'purpose', 'simulated')


def digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


def split_text(body, limit=DEFAULT_LIMIT):
    """Preserve every character; prefer line boundaries within a UTF-16 limit."""
    if (not isinstance(body, str) or not body.strip() or len(body) > 120000
            or type(limit) is not int or not 128 <= limit <= 30000):
        raise ValueError('DELIVERY_TEXT_FORMAT_INVALID')
    parts, start = [], 0
    while start < len(body):
        end, units = start, 0
        while end < len(body):
            width = 2 if ord(body[end]) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end += 1
        if end < len(body):
            line = body.rfind('\n', start + (end - start) // 2, end)
            if line >= 0:
                end = line + 1
            elif body[end - 1:end + 1] == '\r\n':
                end -= 1
        part = body[start:end]
        if not part.strip():
            raise ValueError('DELIVERY_WHITESPACE_PART_REQUIRES_REVIEW')
        parts.append(part)
        start = end
    if len(parts) > 64:
        raise ValueError('DELIVERY_TOO_MANY_PARTS')
    return parts


def _plan(row, limit):
    parts, offset = [], 0
    texts = split_text(row['body'], limit)
    for number, text in enumerate(texts, 1):
        parts.append({'number': number, 'start': offset, 'end': offset + len(text),
                      'body_hash': digest(text),
                      'transport_id': row['id'] if len(texts) == 1 else row['id'] + ':part:' + str(number)})
        offset += len(text)
    return {'scope': {k: row[k] for k in SCOPE}, 'formatter': FORMATTER,
            'max_utf16_units': limit, 'body_hash': digest(row['body']), 'parts': parts}


def read_plan(store, row):
    records = store.all('SELECT details FROM audit WHERE event=? AND outbox_id=?', (PLAN_EVENT, row['id']))
    if not records:
        return None
    if len(records) != 1:
        raise ValueError('DELIVERY_BATCH_PLAN_AMBIGUOUS')
    plan = json.loads(records[0]['details'])
    if plan != _plan(row, plan.get('max_utf16_units')):
        raise ValueError('DELIVERY_BATCH_CONTENT_CHANGED')
    answer = store.one('SELECT * FROM answers WHERE id=?', (row['answer_id'],))
    if not answer or answer['text'] != row['body']:
        raise ValueError('DELIVERY_BATCH_ANSWER_CHANGED')
    return plan


def ensure_plan(flow, row):
    """Called inside the existing transaction, after the full-answer send gate."""
    existing = read_plan(flow.db, row)
    if existing:
        return existing
    if (row['purpose'] not in ('ANSWER', 'CORRECTION') or not row['answer_id']
            or row['state'] != 'PENDING'):
        raise ValueError('DELIVERY_BATCH_ORIGINAL_ANSWER_REQUIRED')
    if flow.db.one("SELECT 1 FROM audit WHERE event=? AND json_extract(details,'$.scope.answer_id')=?",
                   (PLAN_EVENT, row['answer_id'])):
        raise ValueError('DELIVERY_BATCH_ALREADY_EXISTS')
    plan = _plan(row, DEFAULT_LIMIT)
    flow._event(PLAN_EVENT, outbox=row['id'], run=row['run_id'], details=plan)
    return plan


def _receipt_valid(store, row, part, receipt):
    if (not isinstance(receipt, dict) or receipt.get('confirmed') is not True
            or receipt.get('simulated') is not bool(row['simulated'])
            or receipt.get('body_hash') != part['body_hash']
            or not isinstance(receipt.get('confirmed_at'), str)):
        return False
    try:
        timestamp(receipt['confirmed_at'])
    except (ValueError, TypeError):
        return False
    binding = store.one('SELECT group_key,student_key FROM bindings WHERE id=?', (row['binding_id'],))
    if not binding:
        return False
    expected = dict(outbox_id=part['transport_id'], binding_id=row['binding_id'],
                    group_key=binding['group_key'], student_key=binding['student_key'])
    return all(receipt.get(k, v if row['simulated'] else None) == v for k, v in expected.items())


def progress(store, row, plan=None):
    plan = plan or read_plan(store, row)
    if plan is None:
        return None
    verified, receipt_order, unknown = {}, {}, None
    for check in store.all("SELECT rowid AS recorded_order,* FROM delivery_checks WHERE outbox_id=? AND status LIKE 'PART_%' ORDER BY rowid", (row['id'],)):
        proof = json.loads(check['evidence'])
        number = proof.get('part_number')
        if (type(number) is not int or not 1 <= number <= len(plan['parts'])
                or number != len(verified) + 1 or proof.get('verification_method') != PART_METHOD
                or proof.get('plan_hash') != digest(encode(plan))):
            raise ValueError('DELIVERY_PART_ORDER_OR_PLAN_CHANGED')
        part = plan['parts'][number - 1]
        receipt = proof.get('receipt', {})
        starts = store.all('SELECT details FROM audit WHERE event=? AND outbox_id=?', (START_EVENT, row['id']))
        if not any(json.loads(x['details']) == part for x in starts):
            raise ValueError('DELIVERY_PART_START_MISSING')
        if check['status'] == 'PART_UI_CONFIRMED':
            if not _receipt_valid(store, row, part, receipt):
                raise ValueError('DELIVERY_PART_RECEIPT_INVALID')
            if verified and timestamp(receipt['confirmed_at']) < timestamp(verified[number - 1]['confirmed_at']):
                raise ValueError('DELIVERY_PART_TIME_ORDER_INVALID')
            verified[number], unknown = receipt, None
            receipt_order[number] = check['recorded_order']
        elif check['status'] == 'PART_SEND_UNKNOWN':
            unknown = number
        else:
            raise ValueError('DELIVERY_PART_STATE_INVALID')
    return {'plan': plan, 'verified': verified, 'receipt_order': receipt_order, 'unknown': unknown,
            'next_part': len(verified) + 1 if len(verified) < len(plan['parts']) else None}


def part_bound(store, row, bound, *, reconcile=False):
    state = progress(store, row)
    if state is None:
        return bound
    if state['next_part'] is None:
        raise ValueError('DELIVERY_BATCH_ALREADY_COMPLETE')
    if not reconcile and (state['unknown'] or row['state'] in ('SEND_UNKNOWN', 'SENT_UI_CONFIRMED')):
        raise ValueError('DELIVERY_BATCH_REQUIRES_RECONCILIATION')
    part = state['plan']['parts'][state['next_part'] - 1]
    return replace(bound, outbox_id=part['transport_id'], body=row['body'][part['start']:part['end']],
                   batch_outbox_id=row['id'] if len(state['plan']['parts']) > 1 else None)


def start_part(flow, row):
    state = progress(flow.db, row)
    if state:
        if state['unknown'] or state['next_part'] is None:
            raise ValueError('DELIVERY_PART_NOT_SENDABLE')
        flow._event(START_EVENT, outbox=row['id'], run=row['run_id'],
                    details=state['plan']['parts'][state['next_part'] - 1])


def attempt_part(store, row):
    state = progress(store, row)
    return state['next_part'] if state else 1


def _aggregate(row, state):
    receipts = state['verified']
    # Actual timestamps can be observed after an interruption. Completion is the
    # latest verified delivery time, never the time the aggregate was computed.
    completed = max((r['confirmed_at'] for r in receipts.values()), key=timestamp)
    return {'verification_method': COMPLETE_METHOD, 'confirmed': True,
            'simulated': bool(row['simulated']), 'body_hash': digest(row['body']),
            'outbox_id': row['id'], 'binding_id': row['binding_id'], 'confirmed_at': completed,
            'plan_hash': digest(encode(state['plan'])), 'total_parts': len(receipts),
            'platform_message_id': None, 'read_by_student': None}


def record_part(flow, row, receipt, simulated):
    """Return a partial/unknown state or the validated aggregate for _record_check."""
    state = progress(flow.db, row)
    if state['next_part'] is None:
        return row['state'], None
    number = state['next_part']
    part = state['plan']['parts'][number - 1]
    confirmed = simulated is bool(row['simulated']) and _receipt_valid(flow.db, row, part, receipt)
    if confirmed and state['verified']:
        confirmed = timestamp(receipt['confirmed_at']) >= timestamp(state['verified'][number - 1]['confirmed_at'])
    proof = {'verification_method': PART_METHOD, 'part_number': number,
             'plan_hash': digest(encode(state['plan'])), 'receipt': receipt}
    flow.db.execute('INSERT INTO delivery_checks VALUES(?,?,?,?,?)',
                    (new_id(), row['id'], 'PART_UI_CONFIRMED' if confirmed else 'PART_SEND_UNKNOWN', encode(proof), now()))
    if not confirmed:
        flow.db.execute("UPDATE outbox SET state='SEND_UNKNOWN',sent_at=NULL,last_error='DELIVERY_PART_UNKNOWN' WHERE id=?", (row['id'],))
        flow._human(row['message_id'], 'SEND_UNKNOWN:' + row['id'])
        flow._event('DELIVERY_PART_UNKNOWN', outbox=row['id'], run=row['run_id'], details={'part_number': number})
        return 'SEND_UNKNOWN', None
    state = progress(flow.db, row)
    flow._event('DELIVERY_PART_CONFIRMED', outbox=row['id'], run=row['run_id'], details={'part_number': number})
    if state['next_part'] is None:
        return None, _aggregate(row, state)
    current = flow.db.one('SELECT q.current_version,q.context_revision FROM questions q JOIN turns t ON t.question_id=q.id WHERE t.id=?', (row['turn_id'],))
    pending = bool(current and tuple(current) == (row['question_version'], row['context_revision']))
    flow.db.execute('UPDATE outbox SET state=?,sent_at=NULL,last_error=? WHERE id=?',
                    ('PENDING' if pending else 'STALE', None if pending else 'DELIVERED_OLD_VERSION_RECHECK', row['id']))
    if not pending:
        flow._human(row['message_id'], 'DELIVERED_OLD_VERSION_RECHECK:' + row['id'])
    return 'PART_DELIVERED' if pending else 'STALE', None


def validate_complete(store, row, evidence):
    state = progress(store, row)
    if not state or state['next_part'] is not None or evidence != _aggregate(row, state):
        raise ValueError('DELIVERY_BATCH_NOT_COMPLETELY_VERIFIED')


def partial_history(store, question_id, current_turn):
    history = []
    rows = store.all("""SELECT o.* FROM outbox o JOIN turns t ON t.id=o.turn_id
        WHERE t.question_id=? AND t.id<>? AND o.state<>'SENT_UI_CONFIRMED'
        AND o.id IN (SELECT outbox_id FROM audit WHERE event=?)""", (question_id, current_turn, PLAN_EVENT))
    for row in rows:
        state = progress(store, row)
        for number, receipt in state['verified'].items():
            part = state['plan']['parts'][number - 1]
            history.append({'outbox_id': part['transport_id'], 'batch_outbox_id': row['id'],
                'body': row['body'][part['start']:part['end']], 'question_version': row['question_version'],
                'sent_at': receipt['confirmed_at'], 'simulated': row['simulated'],
                '_delivery_order': state['receipt_order'][number],
                'delivery_method': PART_METHOD, 'part_number': number, 'total_parts': len(state['plan']['parts'])})
    return history
