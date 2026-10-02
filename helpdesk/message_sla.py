"""Read-only elapsed times from original student messages to verified delivery.

These are wall-clock measurements, not payroll or working-calendar decisions.
Missing evidence and simulated delivery never establish a real SLA result.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from .performance_rules import timestamp


ACK_LIMIT_SECONDS = 15 * 60


def _delivery_time(store, row, binding, asked, as_of):
    if row['state'] != 'SENT_UI_CONFIRMED' or row['simulated'] != 0:
        return None
    check = store.one('SELECT * FROM delivery_checks WHERE outbox_id=? ORDER BY rowid DESC LIMIT 1', (row['id'],))
    if not check or check['status'] != 'SENT_UI_CONFIRMED':
        return None
    try:
        proof = json.loads(check['evidence'])
        sent = timestamp(row['sent_at'])
        if (not isinstance(proof, dict) or proof.get('confirmed') is not True
                or proof.get('simulated') is not False or not asked <= sent <= as_of
                or timestamp(proof.get('confirmed_at')) != sent
                or proof.get('body_hash') != sha256(row['body'].encode()).hexdigest()):
            return None
        expected = dict(outbox_id=row['id'], binding_id=binding['id'],
                        group_key=binding['group_key'], student_key=binding['student_key'])
        # Batch aggregates bind the original Outbox; their part validator checks
        # group/student on every actual receipt. Other receipts carry all four.
        required = ('outbox_id', 'binding_id') if proof.get('verification_method') == 'ORDERED_TEXT_BATCH' else tuple(expected)
        if (row['binding_id'] != binding['id'] or any(proof.get(k) != expected[k] for k in required)
                or any(k in proof and proof[k] != v for k, v in expected.items())):
            return None
        if proof.get('verification_method') == 'MANUAL_ATTESTATION':
            from .manual_delivery import validate_manual_package
            validate_manual_package(store, row, proof)
        if row['purpose'] in ('ANSWER', 'CORRECTION'):
            from .delivery_batches import read_plan, validate_complete
            if read_plan(store, row) or proof.get('verification_method') == 'ORDERED_TEXT_BATCH':
                validate_complete(store, row, proof)
        return sent
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return None


def message_sla(store, *, as_of=None):
    """Keep original, observed and delivered times separate without writing data."""
    current = timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
    metrics = []
    for message in store.all("SELECT * FROM messages WHERE intent!='IRRELEVANT'"):
        item = {'message_id': message['id'], 'time_basis': 'STUDENT_ORIGINAL_SEND_TIME',
                'source_sent_at': message['source_sent_at'], 'observed_at': message['observed_at'],
                'time_status': 'TIME_REVIEW_REQUIRED', 'collection_seconds': None,
                'ack_seconds': None, 'answer_seconds': None, 'ack_overdue': None,
                'answer_overdue': None, 'ack_limit_seconds': ACK_LIMIT_SECONDS,
                'answer_limit_seconds': None, 'answer_policy_status': 'NOT_EVALUATED',
                'ack_status': 'TIME_REVIEW_REQUIRED', 'answer_status': 'TIME_REVIEW_REQUIRED'}
        metrics.append(item)
        if message['source'].upper() in ('MOCK', 'OPERATOR_TEST'):
            item.update(time_status='SIMULATED_SOURCE', ack_status='SIMULATED_SOURCE', answer_status='SIMULATED_SOURCE')
            continue
        try:
            proof = json.loads(message['source_time_evidence'] or 'null')
            asked = timestamp(message['source_sent_at'])
            if (not isinstance(proof, dict) or proof.get('source') not in ('wecom_original', 'operator_verified_original')
                    or not proof.get('message_locator') or not proof.get('evidence') or asked > current):
                continue
            if store.one("SELECT 1 FROM human_tasks WHERE message_id=? AND reason='SOURCE_TIME_CONFLICT' AND state='OPEN'", (message['id'],)):
                continue
            if store.one("SELECT 1 FROM performance_units WHERE first_message_id=? AND category_basis='原始时间冲突待核验'", (message['id'],)):
                continue
            if message['observed_at']:
                observed = timestamp(message['observed_at'])
                if observed < asked or observed > current:
                    continue
                item['collection_seconds'] = (observed - asked).total_seconds()
        except (ValueError, TypeError):
            continue
        item['time_status'] = 'VERIFIED'
        binding = store.one('SELECT * FROM bindings WHERE id=?', (message['binding_id'],))
        rows = store.all("SELECT * FROM outbox WHERE message_id=? AND purpose IN ('ACK','ANSWER','CORRECTION')", (message['id'],))
        for kind, purposes in (('ack', ('ACK',)), ('answer', ('ANSWER', 'CORRECTION'))):
            selected = [row for row in rows if row['purpose'] in purposes]
            deliveries = []
            for row in selected:
                sent = _delivery_time(store, row, binding, asked, current) if binding and binding['verified'] else None
                if sent is not None:
                    deliveries.append(sent)
            if deliveries:
                seconds = (min(deliveries) - asked).total_seconds()
                item[kind + '_seconds'] = seconds
                item[kind + '_status'] = 'VERIFIED'
                if kind == 'ack':
                    item['ack_overdue'] = seconds > ACK_LIMIT_SECONDS
            else:
                states = {row['state'] for row in selected}
                if 'SENT_UI_CONFIRMED' in states:
                    status = 'DELIVERY_REVIEW_REQUIRED'
                elif states & {'SENDING', 'SEND_UNKNOWN'}:
                    status = 'SEND_UNKNOWN'
                else:
                    status = 'PENDING' if selected or kind == 'answer' else 'NOT_QUEUED'
                item[kind + '_status'] = status
                if kind == 'ack' and selected and status != 'DELIVERY_REVIEW_REQUIRED':
                    item['ack_overdue'] = (current - asked).total_seconds() > ACK_LIMIT_SECONDS
    return metrics
