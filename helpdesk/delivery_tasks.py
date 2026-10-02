"""Independent ACK/answer send tasks over the existing Outbox and audit records.

Outbox is the sole delivery ledger. Retry deadlines and attempts survive restart
in its existing audit stream; no second completion table or task framework.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import ClassVar

from .delivery import RetryablePreflightFailure
from .locking import resource_lock
from .workflow import Workflow


@dataclass(frozen=True)
class AckTask:
    outbox_id: str
    message_id: str
    binding_id: str
    state: str
    kind: ClassVar[str] = 'ACK'


@dataclass(frozen=True)
class AnswerTask:
    outbox_id: str
    message_id: str
    binding_id: str
    state: str
    turn_id: str
    question_version: str
    context_revision: int
    kind: ClassVar[str] = 'ANSWER'


def task_for(row):
    common = [row[k] for k in ('id', 'message_id', 'binding_id', 'state')]
    if row['purpose'] == 'ACK':
        return AckTask(*common)
    if row['purpose'] in ('ANSWER', 'CORRECTION'):
        return AnswerTask(*common, *[row[k] for k in ('turn_id', 'question_version', 'context_revision')])
    raise ValueError('Only ACK and answer tasks are eligible')


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_retry_seconds: int = 5
    max_retry_seconds: int = 60

    def __post_init__(self):
        if (type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5
                or type(self.initial_retry_seconds) is not int or not 1 <= self.initial_retry_seconds <= 60
                or type(self.max_retry_seconds) is not int
                or not self.initial_retry_seconds <= self.max_retry_seconds <= 300):
            raise ValueError('Invalid bounded delivery retry policy')


class AutomaticDelivery:
    def __init__(self, store, desktop, *, policy=None, ack_source_validator=None,
                 pause_requested=lambda: False,
                 clock=lambda: datetime.now(timezone.utc)):
        self.store = store
        self.flow = Workflow(store, desktop=desktop)
        self.policy = policy or RetryPolicy()
        self.ack_source_validator = ack_source_validator
        self.clock = clock
        self.pause_requested = pause_requested
        self.lock_path = store.path + '.automatic-delivery.lock'
        from .mcp_group_delivery import MCPGroupDesktop
        if isinstance(desktop, MCPGroupDesktop):
            desktop.guard = self._guard

    def _guard(self, message):
        if self.pause_requested() or self.flow._stopped():
            raise ValueError('AUTOMATIC_DELIVERY_PAUSED')
        row = self.store.one('SELECT * FROM outbox WHERE id=?', (message.batch_outbox_id or message.outbox_id,))
        if not row or row['state'] not in ('PENDING', 'SENDING'):
            raise ValueError('AUTOMATIC_DELIVERY_STATE_CHANGED')
        if self.flow._delivery_mode(row) != 'AUTO':
            raise ValueError('AUTOMATIC_DELIVERY_POLICY_CHANGED')
        if self.flow._validate(row) != message:
            raise ValueError('AUTOMATIC_DELIVERY_BINDING_CHANGED')
        self._source(row)
        if row['purpose'] in ('ANSWER', 'CORRECTION'):
            self.flow._require_confirmed_ack(row['turn_id'], require_real=True)

    def _clock(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError('Delivery clock must be timezone-aware')
        return value

    def _event(self, row, event, details):
        self.flow._event(event, outbox=row['id'], run=row['run_id'], details=details)

    def _retry(self, row):
        from .delivery_batches import attempt_part
        part = attempt_part(self.store, row)
        attempts = self.store.one("""SELECT COUNT(*) FROM audit WHERE outbox_id=? AND event='TASK_SEND_STARTED'
            AND COALESCE(json_extract(details,'$.part_number'),1)=?""", (row['id'], part))[0]
        latest = self.store.one("""SELECT event,details FROM audit WHERE outbox_id=? AND event LIKE 'TASK_SEND_%'
            AND COALESCE(json_extract(details,'$.part_number'),1)=? ORDER BY rowid DESC LIMIT 1""", (row['id'], part))
        value = json.loads(latest['details']) if latest else {}
        return attempts, latest['event'] if latest else None, value

    def snapshot(self):
        result = {'ack_tasks': [], 'answer_tasks': [], 'simulation': self.flow.desktop.simulated,
                  'answer_review_required': self.flow._answer_review_required()}
        for row in self.store.all("""SELECT * FROM outbox WHERE purpose IN ('ACK','ANSWER','CORRECTION')
                ORDER BY CASE state WHEN 'SEND_UNKNOWN' THEN 0 WHEN 'SENDING' THEN 1 WHEN 'PENDING' THEN 2
                WHEN 'FAILED' THEN 3 ELSE 4 END,rowid DESC LIMIT 250"""):
            task = task_for(row)
            binding = self.store.one('SELECT group_key,display_name FROM bindings WHERE id=?', (task.binding_id,))
            attempts, event, metadata = self._retry(row)
            item = {'task_id': task.outbox_id, 'kind': task.kind, 'message_id': task.message_id,
                    'binding_id': task.binding_id, 'state': task.state, 'delivery_mode': self.flow._delivery_mode(row),
                    'attempts': attempts, 'next_attempt_at': metadata.get('next_attempt_at'),
                    'last_error': row['last_error'], 'retry_state': event,
                    'review_status': row['review_status'], 'content': row['body'],
                    'question_version': row['question_version'], 'context_revision': row['context_revision'],
                    'simulated': bool(row['simulated'])}
            item.update(group_key=binding['group_key'] if binding else None,
                        student_name=binding['display_name'] if binding else None)
            from .delivery_batches import progress
            batch = progress(self.store, row)
            if batch:
                item['delivery_batch'] = {'total_parts': len(batch['plan']['parts']),
                    'verified_parts': len(batch['verified']), 'current_part': batch['next_part'],
                    'unknown_part': batch['unknown'], 'complete': batch['next_part'] is None}
            result['ack_tasks' if task.kind == 'ACK' else 'answer_tasks'].append(item)
        return result

    def _source(self, row):
        if row['purpose'] != 'ACK':
            return
        if row['body'] != '收到':
            raise ValueError('ACK_BODY_CHANGED')
        if self.ack_source_validator is not None:
            actual = self.ack_source_validator(self.store, row)
        else:
            actual = False
        if self.flow.desktop.simulated is False:
            if actual is not True:
                raise ValueError('ACTUAL_ACK_SOURCE_REQUIRED')
            if row['simulated'] != 0:
                self.store.execute('UPDATE outbox SET simulated=0 WHERE id=?', (row['id'],))
                self._event(row, 'ACK_SOURCE_CONFIRMED_ACTUAL', {'source_message_id': row['message_id']})

    def _candidate(self):
        for row in self.store.all("""SELECT * FROM outbox WHERE state IN ('PENDING','FAILED')
                AND purpose IN ('ACK','ANSWER','CORRECTION')
                ORDER BY CASE WHEN purpose='ACK' THEN 0 ELSE 1 END,created_at,rowid"""):
            if self.flow._delivery_mode(row) != 'AUTO':
                continue
            if row['purpose'] != 'ACK' and self.flow._answer_review_required() and row['review_status'] != 'APPROVED':
                continue
            attempts, event, metadata = self._retry(row)
            if attempts >= self.policy.max_attempts:
                continue
            if row['state'] == 'FAILED' and event != 'TASK_SEND_RETRY_SCHEDULED':
                continue
            if event == 'TASK_SEND_RETRY_SCHEDULED':
                due = datetime.fromisoformat(metadata['next_attempt_at'])
                if due.utcoffset() is None or self._clock() < due:
                    continue
            return row
        return None

    def _recover_interrupted(self):
        # Only our abandoned sends, never manual work or another active sender.
        rows = self.store.all("""SELECT * FROM outbox WHERE state='SENDING' AND id IN
                (SELECT outbox_id FROM audit WHERE event='TASK_SEND_STARTED')""")
        if not rows:
            return
        with resource_lock(self.flow.desktop.lock_path, timeout=.05):
            for row in rows:
                with self.store.transaction():
                    if self.store.one('SELECT state FROM outbox WHERE id=?', (row['id'],))[0] != 'SENDING':
                        continue
                    self.flow._record_check(row, {'confirmed': False, 'simulated': self.flow.desktop.simulated,
                                                   'reason': 'AUTOMATIC_SENDER_INTERRUPTED'})
                    self._event(row, 'TASK_SEND_NEEDS_ATTENTION', {'reason': 'SEND_UNKNOWN', 'automatic_retry_allowed': False})

    def _finish_attempt(self, row, attempt, state, *, retryable=False, reason=None, part_number=1):
        with self.store.transaction():
            if retryable and attempt < self.policy.max_attempts:
                seconds = min(self.policy.initial_retry_seconds * 2 ** (attempt - 1), self.policy.max_retry_seconds)
                self._event(row, 'TASK_SEND_RETRY_SCHEDULED', {
                    'attempt': attempt, 'part_number': part_number, 'reason': reason, 'submission_attempted': False,
                    'next_attempt_at': (self._clock() + timedelta(seconds=seconds)).isoformat()})
            else:
                if retryable and state == 'PENDING':
                    state = 'FAILED'
                    self.store.execute("UPDATE outbox SET state='FAILED',last_error='AUTOMATIC_SEND_RETRIES_EXHAUSTED' WHERE id=?", (row['id'],))
                event = ('TASK_SEND_CONFIRMED' if state == 'SENT_UI_CONFIRMED' else
                         'TASK_SEND_PART_CONFIRMED' if state == 'PART_DELIVERED' else 'TASK_SEND_NEEDS_ATTENTION')
                self._event(row, event, {'attempt': attempt, 'part_number': part_number, 'state': state, 'reason': reason,
                                        'automatic_retry_allowed': False})
                if retryable:
                    self.flow._human(row['message_id'], 'AUTOMATIC_SEND_RETRIES_EXHAUSTED:' + row['id'])
        return {'task_id': row['id'], 'kind': task_for(row).kind, 'state': state, 'attempt': attempt, 'part_number': part_number,
                'retry_scheduled': retryable and attempt < self.policy.max_attempts}

    def tick(self):
        """Dispatch one due task; long teaching generation never owns this loop."""
        try:
            with resource_lock(self.lock_path, timeout=.05):
                if self.pause_requested() or self.flow._stopped():
                    return {'state': 'STOPPED'}
                self._recover_interrupted()
                unknown = self.store.one("""SELECT id FROM outbox WHERE state='SEND_UNKNOWN'
                    AND id IN (SELECT outbox_id FROM audit WHERE event='TASK_SEND_STARTED') LIMIT 1""")
                if unknown:
                    return {'state': 'NEEDS_ATTENTION', 'task_id': unknown['id'], 'automatic_retry_allowed': False}
                row = self._candidate()
                if row is None:
                    return {'state': 'IDLE'}
                try:
                    with self.store.transaction():
                        self._source(row)
                except (ValueError, OSError):
                    with self.store.transaction():
                        self.store.execute("UPDATE outbox SET state='FAILED',last_error='ACK_SOURCE_REQUIRES_REVIEW' WHERE id=?", (row['id'],))
                        self.flow._human(row['message_id'], 'ACK_SOURCE_REQUIRES_REVIEW')
                        self._event(row, 'TASK_SEND_NEEDS_ATTENTION', {'reason': 'ACK_SOURCE_REQUIRES_REVIEW'})
                    return {'state': 'FAILED', 'task_id': row['id'], 'kind': 'ACK'}
                attempt = self._retry(row)[0] + 1
                from .delivery_batches import attempt_part
                part_number = attempt_part(self.store, row)
                with self.store.transaction():
                    if row['state'] == 'FAILED':
                        self.store.execute("UPDATE outbox SET state='PENDING',last_error=NULL WHERE id=?", (row['id'],))
                    self._event(row, 'TASK_SEND_STARTED', {'kind': task_for(row).kind, 'attempt': attempt, 'part_number': part_number})
                try:
                    state = self.flow.dispatch(row['id'])
                except Exception as exc:
                    current = self.store.one('SELECT * FROM outbox WHERE id=?', (row['id'],))
                    if (isinstance(exc, TimeoutError) and str(exc) == 'Resource busy; bounded wait expired'
                            and current['state'] == 'PENDING'):
                        return self._finish_attempt(row, attempt, 'PENDING', retryable=True, reason='DESKTOP_BUSY', part_number=part_number)
                    with self.store.transaction():
                        if current['state'] == 'SENDING':
                            state = self.flow._record_check(current, {'confirmed': False,
                                'simulated': self.flow.desktop.simulated, 'reason': 'AUTOMATIC_DISPATCH_INTERRUPTED'})
                        elif current['state'] == 'PENDING':
                            state = 'FAILED'
                            self.store.execute("UPDATE outbox SET state='FAILED',last_error='AUTOMATIC_DISPATCH_FAILED' WHERE id=?", (row['id'],))
                            self.flow._human(row['message_id'], 'AUTOMATIC_DISPATCH_REQUIRES_REVIEW:' + row['id'])
                        else:
                            state = current['state']
                    return self._finish_attempt(current, attempt, state, reason='AUTOMATIC_DISPATCH_FAILED', part_number=part_number)
                current = self.store.one('SELECT * FROM outbox WHERE id=?', (row['id'],))
                proof = self.store.one("SELECT details FROM audit WHERE outbox_id=? AND event='SEND_PREFLIGHT_FAILED' ORDER BY rowid DESC LIMIT 1", (row['id'],))
                evidence = json.loads(proof[0]) if proof else {}
                retryable = (state == 'FAILED' and evidence.get('retryable') is True
                             and evidence.get('submission_attempted') is False
                             and current['last_error'] in RetryablePreflightFailure.CODES)
                return self._finish_attempt(current, attempt, state, retryable=retryable, reason=current['last_error'], part_number=part_number)
        except TimeoutError as exc:
            if str(exc) != 'Resource busy; bounded wait expired':
                raise
            return {'state': 'DISPATCHER_BUSY'}


def collector_ack_validator(collector, *, self_sender_ids=(), teacher_sender_ids=()):
    """ACK admission is independent of question clarity and teaching readiness."""
    def validate(store, outbox):
        from .collector_dispatch import CollectorDispatcher
        reader = CollectorDispatcher.__new__(CollectorDispatcher)
        reader.collector, reader.business = collector, store
        reader.self_sender_ids, reader.teacher_sender_ids = frozenset(self_sender_ids), frozenset(teacher_sender_ids)
        task = store.one('SELECT * FROM collector_answer_tasks WHERE business_message_id=?', (outbox['message_id'],))
        receipt = store.one('SELECT * FROM messages WHERE id=?', (outbox['message_id'],))
        if (not task or task['mode'] != 'LIVE' or task['state'] not in ('ACK_QUEUED', 'RESOLVED')
                or not receipt or receipt['binding_id'] != outbox['binding_id'] or not receipt['source_sent_at']):
            raise ValueError('Verified LIVE original ACK receipt required')
        with collector.connect() as db:
            source = reader._checked_source(db, task, reader._receipt_transport(receipt), promotion=not task['incoming_json'])
        raw = json.loads(source['raw_payload'])
        return not (isinstance(raw, dict) and (raw.get('simulated') is True or raw.get('fixture') is True))
    return validate
