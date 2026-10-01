"""One confirmed relation consumed by answering and candidate counting.

No model, source inference, content approval, delivery or parallel schema. The
authority is an existing linked collector receipt and the existing audit log.
"""
from hashlib import sha256
import json

from .collector_dispatch import CollectorDispatcher
from .performance import COMPREHENSIVE_TYPES, MATERIAL_TYPES, PerformanceLedger
from .service import Helpdesk
from .storage import encode, now

CONFIRMED = 'SEMANTIC_DECISION_CONFIRMED'
COUNTED = 'SEMANTIC_DECISION_COUNTING_PROJECTED'
CONTINUATIONS = {'FOLLOWUP', 'SUPPLEMENT', 'CORRECTION', 'DISPUTE'}


def _digest(value):
    return sha256(encode(value).encode('utf-8')).hexdigest()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


class SharedSemanticDecisions:
    def __init__(self, business_store, collector_store, *, self_sender_ids=(), teacher_sender_ids=()):
        self.business, self.collector = business_store, collector_store
        self.filters = {'self_sender_ids': tuple(sorted(self_sender_ids)),
                        'teacher_sender_ids': tuple(sorted(teacher_sender_ids))}

    def _origin(self, task_id):
        origin = CollectorDispatcher.read_verified_received(self.collector, self.business, task_id, **self.filters)
        message, turn, source, task = (origin[key] for key in ('message', 'turn', 'source', 'task'))
        saved = json.loads(task['decision_json'])
        key = Helpdesk.received_decision_key(saved['decision'])
        _require(saved.get('decision_key', key) == key, 'SEMANTIC_RESOLVER_KEY_CHANGED')
        _require(isinstance(saved.get('reviewer'), str) and saved['reviewer'].strip()
                 and isinstance(saved.get('rationale'), str) and saved['rationale'].strip(), 'SEMANTIC_RESOLVER_EVIDENCE_MISSING')
        _require(saved['decision']['intent'] == turn['intent'] == message['intent'], 'SEMANTIC_INTENT_CHANGED')
        context = Helpdesk(self.business).context(turn['id'])
        question = self.business.one('SELECT * FROM questions WHERE id=?', (turn['question_id'],))
        material = self.business.one('SELECT * FROM materials WHERE id=?', (question['material_id'],))
        version = self.business.one('SELECT * FROM question_versions WHERE id=?', (turn['question_version'],))
        mv = self.business.one('SELECT * FROM material_versions WHERE id=?', (version['material_version'],))
        case = self.business.one('SELECT * FROM cases WHERE id=?', (turn['case_id'],))
        _require(case is not None and case['binding_id'] == message['binding_id'], 'SEMANTIC_BINDING_CHANGED')
        _require(material is not None and mv is not None and material['case_id'] == case['id']
                 and mv['material_id'] == material['id'] and material['current_version'] == version['material_version'],
                 'SEMANTIC_MATERIAL_VERSION_CHANGED')
        _require(context['student_words'] == message['raw_text'], 'SEMANTIC_ORIGINAL_TEXT_CHANGED')
        immutable = ('source_type', 'source_message_id', 'message_id', 'room_id', 'sender_id', 'message_type',
                     'raw_content', 'normalized_text', 'media_id', 'local_media_path', 'media_hash',
                     'reply_to_message_id', 'quoted_message_id', 'sent_at_raw', 'sent_at_utc', 'sent_at_local',
                     'ingested_at', 'raw_payload', 'business_timezone')
        proof = {'source': {k: source[k] for k in immutable},
                 'transport': CollectorDispatcher._receipt_transport(message), 'filters': self.filters}
        return {'collector_task_id': task_id, 'message_id': message['id'], 'binding_id': message['binding_id'],
            'case_id': case['id'], 'question_id': question['id'], 'turn_id': turn['id'],
            'question_version': version['id'], 'context_revision': turn['context_revision'],
            'material_id': material['id'], 'material_version': mv['id'], 'intent': turn['intent'],
            'resolver_decision_key': key, 'resolver_result': saved['result'],
            'source_sha256': _digest(proof), 'question_sha256': _digest(dict(version)),
            'material_sha256': _digest(dict(mv))}

    def _rows(self, event, field, value):
        result = []
        for row in self.business.all('SELECT case_id,question_id,turn_id,details FROM audit WHERE event=?', (event,)):
            details = json.loads(row['details'])
            _require(isinstance(details, dict), 'SEMANTIC_AUDIT_MALFORMED')
            if details.get(field) == value:
                _require(all(row[key] == details.get(key) for key in ('case_id', 'question_id', 'turn_id')),
                         'SEMANTIC_AUDIT_RELATION_CHANGED')
                result.append(details)
        return result

    def _checked_unit(self, unit_id, origin, question_type, *, continuation=False):
        unit = self.business.one('SELECT * FROM performance_units WHERE id=?', (unit_id,))
        _require(unit is not None and unit['status'] not in ('REVOKED', 'EXCLUDED'), 'SEMANTIC_UNIT_UNAVAILABLE')
        _require(unit['binding_id'] == origin['binding_id'] and unit['case_id'] == origin['case_id']
                 and unit['material_id'] == origin['material_id'] and unit['question_type'] == question_type,
                 'SEMANTIC_UNIT_RELATION_MISMATCH')
        if continuation:
            _require(self.business.one('SELECT id FROM performance_links WHERE unit_id=? AND question_id=?',
                                      (unit_id, origin['question_id'])) is not None, 'SEMANTIC_PRIOR_QUESTION_LINK_REQUIRED')
        return unit

    def _grouping(self, origin, question_type, existing_unit_id):
        _require(isinstance(question_type, str) and question_type in MATERIAL_TYPES | {'独立知识点'},
                 'SEMANTIC_QUESTION_TYPE_UNSUPPORTED')
        intent = origin['intent']
        _require(intent in CONTINUATIONS | {'NEW', 'SUBQUESTION'}, 'SEMANTIC_INTENT_UNSUPPORTED')
        ledger = PerformanceLedger(self.business)
        message = self.business.one('SELECT * FROM messages WHERE id=?', (origin['message_id'],))
        category, _, _ = ledger._classification(message)
        _require(category in ('NIGHT', 'REGULAR'), 'SEMANTIC_TIME_CLASSIFICATION_UNRESOLVED')
        by_material = category == 'NIGHT' or question_type in COMPREHENSIVE_TYPES
        scope = 'semantic:' + _digest({'binding_id': origin['binding_id'], 'question_type': question_type,
                                     'material_id': origin['material_id'],
                                     'question_id': None if by_material else origin['question_id']})
        if intent in CONTINUATIONS:
            if existing_unit_id is None:
                units = self.business.all('''SELECT DISTINCT u.id FROM performance_units u JOIN performance_links l
                    ON l.unit_id=u.id WHERE l.question_id=? AND u.binding_id=? AND u.material_id=?
                    AND u.question_type=? AND u.status NOT IN ('REVOKED','EXCLUDED')''',
                    (origin['question_id'], origin['binding_id'], origin['material_id'], question_type))
                _require(len(units) == 1, 'SEMANTIC_EXISTING_SCOPE_REQUIRED_OR_AMBIGUOUS')
                existing_unit_id = units[0]['id']
            unit = self._checked_unit(existing_unit_id, origin, question_type, continuation=True)
            return {'mode': 'EXISTING', 'unit_id': existing_unit_id, 'scope_key': unit['scope_key'],
                    'basis': 'Verified existing question link; preserve independent first-question time'}
        if existing_unit_id:
            unit = self._checked_unit(existing_unit_id, origin, question_type)
            _require(by_material or unit['scope_key'] == scope, 'SEMANTIC_DISTINCT_DAYTIME_QUESTION_SCOPE')
            return {'mode': 'EXISTING', 'unit_id': existing_unit_id, 'scope_key': unit['scope_key'],
                    'basis': 'Verified same material/type counting range'}
        return {'mode': 'CANDIDATE', 'unit_id': None, 'scope_key': scope,
                'basis': 'Verified material range' if by_material else 'Verified question identity; quantity requires actual-question review'}

    def _append(self, event, details):
        self.business.execute('INSERT INTO audit(case_id,question_id,turn_id,event,details,created_at) VALUES(?,?,?,?,?,?)',
            (details['case_id'], details['question_id'], details['turn_id'], event, encode(details), now()))

    def confirm(self, task_id, *, question_type, actor, evidence, existing_unit_id=None):
        _require(isinstance(actor, str) and actor.strip() and isinstance(evidence, str) and evidence.strip(),
                 'SEMANTIC_ACTOR_AND_EVIDENCE_REQUIRED')
        with self.collector.connect() as source_lock:
            source_lock.execute('BEGIN IMMEDIATE')
            with self.business.transaction():
                origin = self._origin(task_id)
                records = self._rows(CONFIRMED, 'collector_task_id', task_id)
                _require(len(records) <= 1, 'SEMANTIC_DECISION_AMBIGUOUS')
                if records:
                    record = self._verify(records[0])
                    _require(record['question_type'] == question_type
                             and (existing_unit_id is None or record['grouping']['unit_id'] == existing_unit_id),
                             'SEMANTIC_DECISION_ALREADY_FIXED')
                    return record['decision_id']
                record = {**origin, 'question_type': question_type, 'actor': actor, 'evidence': evidence,
                          'grouping': self._grouping(origin, question_type, existing_unit_id)}
                record['decision_id'] = 'semantic:' + _digest(record)
                self._append(CONFIRMED, record)
                return record['decision_id']

    def _verify(self, record):
        payload = {key: value for key, value in record.items() if key != 'decision_id'}
        _require(record.get('decision_id') == 'semantic:' + _digest(payload), 'SEMANTIC_DECISION_AUDIT_CHANGED')
        _require(all(record.get(key) for key in ('actor', 'evidence')), 'SEMANTIC_DECISION_EVIDENCE_MISSING')
        origin = self._origin(record['collector_task_id'])
        _require(all(record.get(key) == value for key, value in origin.items()), 'SEMANTIC_FACT_OR_VERSION_CHANGED')
        records = self._rows(CONFIRMED, 'collector_task_id', record['collector_task_id'])
        _require(len(records) == 1 and records[0] == record, 'SEMANTIC_DECISION_AMBIGUOUS')
        if record['grouping']['mode'] == 'EXISTING':
            self._checked_unit(record['grouping']['unit_id'], origin, record['question_type'],
                               continuation=record['intent'] in CONTINUATIONS)
        return record

    def _decision(self, decision_id):
        rows = self._rows(CONFIRMED, 'decision_id', decision_id)
        _require(len(rows) == 1, 'SEMANTIC_DECISION_MISSING_OR_AMBIGUOUS')
        return self._verify(rows[0])

    def answer_input(self, decision_id):
        record = self._decision(decision_id)
        keys = ('collector_task_id', 'message_id', 'case_id', 'question_id', 'turn_id', 'question_version',
                'context_revision', 'material_id', 'material_version', 'question_type', 'decision_id')
        return {key: record[key] for key in keys}

    def counting_unit(self, decision_id):
        # Source policy cannot change during local projection. No model/UI waits.
        with self.collector.connect() as source_lock:
            source_lock.execute('BEGIN IMMEDIATE')
            with self.business.transaction():
                record = self._decision(decision_id)
                previous = self._rows(COUNTED, 'decision_id', decision_id)
                _require(len(previous) <= 1, 'SEMANTIC_COUNTING_AUDIT_AMBIGUOUS')
                if previous:
                    unit_id = previous[0]['unit_id']
                    self._checked_unit(unit_id, record, record['question_type'])
                    _require(previous[0] == {**self.answer_input(decision_id), 'unit_id': unit_id},
                             'SEMANTIC_COUNTING_AUDIT_CHANGED')
                    _require(self.business.one('SELECT id FROM performance_links WHERE unit_id=? AND message_id=? AND question_id=?',
                                              (unit_id, record['message_id'], record['question_id'])) is not None,
                             'SEMANTIC_COUNTING_LINK_CHANGED')
                    return unit_id
                ledger = PerformanceLedger(self.business)
                if record['grouping']['mode'] == 'EXISTING':
                    unit_id = record['grouping']['unit_id']
                    ledger.link_activity(unit_id, record['message_id'], question_id=record['question_id'],
                        kind=record['intent'], reason='Shared semantic decision ' + decision_id)
                else:
                    unit_id = ledger.create_unit(record['message_id'], record['question_type'],
                        scope_key=record['grouping']['scope_key'], grouping_reason='Shared semantic decision ' + decision_id,
                        question_id=record['question_id'], material_id=record['material_id'])
                self._append(COUNTED, {**self.answer_input(decision_id), 'unit_id': unit_id})
                return unit_id
