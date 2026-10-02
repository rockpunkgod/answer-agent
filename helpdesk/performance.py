"""Evidence-backed performance ledger. A unit is a material/scope, not a message.

This module never promotes a generated answer or a simulated delivery into an
official count. Missing source time and unresolved grouping stay reviewable.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from hashlib import sha256
import json

from .domain import new_id
from .performance_rules import RULE_VERSION, classify_night
from .storage import Store, encode, now


MATERIAL_TYPES = {'阅读理解', '阅读', '七选五', '完形填空', '完形', '写作', '应用文', '读后续写', '听力', '语法填空'}
COMPREHENSIVE_TYPES = {'阅读理解', '阅读', '七选五', '完形填空', '完形', '写作', '应用文', '读后续写'}
ACTIVITY_KINDS = {'FIRST', 'SUBQUESTION', 'FOLLOWUP', 'SUPPLEMENT', 'CORRECTION', 'DISPUTE', 'DELIVERY', 'OTHER'}
TIME_SOURCES = {'wecom_original', 'operator_verified_original'}


class PerformanceLedger:
    def __init__(self, store: Store, *, timezone_name: str = 'Asia/Shanghai', rule_version: str = RULE_VERSION,
                 night_end_hour: int | None = None, night_end_inclusive: bool | None = None):
        self.db = store
        self.timezone_name = timezone_name
        latest = self.db.one("SELECT new_version FROM performance_rule_changes WHERE key='night_end' ORDER BY changed_at DESC,rowid DESC LIMIT 1")
        self.rule_version = latest['new_version'] if latest else rule_version
        if night_end_hour is None:
            configured = self.db.one("SELECT value,status FROM performance_rules WHERE key='night_end'")
            boundary = self.db.one("SELECT value,status FROM performance_rules WHERE key='night_end_inclusive'")
            if configured and configured['status'] == 'CONFIRMED' and boundary and boundary['status'] == 'CONFIRMED':
                night_end_hour = int(configured['value'])
                night_end_inclusive = boundary['value'] == 'true'
                if not latest and (night_end_hour, night_end_inclusive) != (7, False):
                    self.rule_version = f'legacy-confirmed-night-end-{night_end_hour:02d}-{int(night_end_inclusive)}'
            elif not latest:
                self.rule_version = 'legacy-unresolved-night-end'
        self.night_end_hour = night_end_hour
        self.night_end_inclusive = night_end_inclusive

    def configure_night_end(self, end_hour: int, *, end_inclusive: bool, actor: str,
                            reason: str, evidence: str) -> str:
        """Confirm a prospective boundary; historical unit snapshots stay untouched."""
        if type(end_hour) is not int or not 0 <= end_hour < 23 or type(end_inclusive) is not bool:
            raise ValueError('Night end must be an explicit hour and inclusive boundary')
        if not actor or not reason or not evidence:
            raise ValueError('Rule change requires actor, reason and source evidence')
        with self.db.transaction():
            old_end = self.db.one("SELECT value,status FROM performance_rules WHERE key='night_end'")
            old_inc = self.db.one("SELECT value,status FROM performance_rules WHERE key='night_end_inclusive'")
            if (old_end['status'], old_end['value'], old_inc['status'], old_inc['value']) == (
                    'CONFIRMED', str(end_hour), 'CONFIRMED', 'true' if end_inclusive else 'false'):
                return self.rule_version
            old_version = self.rule_version
            change_id = new_id()
            new_version = f'{RULE_VERSION}+night-end-{end_hour:02d}-{int(end_inclusive)}-{change_id[:8]}'
            self.db.execute("UPDATE performance_rules SET value=?,status='CONFIRMED',source=?,updated_at=? WHERE key='night_end'",
                            (str(end_hour), evidence, now()))
            self.db.execute("UPDATE performance_rules SET value=?,status='CONFIRMED',source=?,updated_at=? WHERE key='night_end_inclusive'",
                            ('true' if end_inclusive else 'false', evidence, now()))
            self.db.execute('''INSERT INTO performance_rule_changes
                (id,key,old_value,new_value,old_version,new_version,actor,reason,evidence,changed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (change_id, 'night_end',
                encode({'end_hour': old_end['value'], 'end_inclusive': old_inc['value']}),
                encode({'end_hour': end_hour, 'end_inclusive': end_inclusive}), old_version,
                new_version, actor, reason, evidence, now()))
        self.night_end_hour = end_hour
        self.night_end_inclusive = end_inclusive
        self.rule_version = new_version
        return new_version

    def preview_night_reclassification(self, *, end_hour: int, end_inclusive: bool):
        """Read-only what-if list; never rewrites approved or submitted history."""
        if type(end_hour) is not int or not 0 <= end_hour < 23 or type(end_inclusive) is not bool:
            raise ValueError('Explicit boundary required')
        rows = []
        for unit in self.list_units():
            evidence = unit['question_time_source']
            result = classify_night(unit['question_time'], time_evidence=evidence,
                                    timezone_name=self.timezone_name, end_hour=end_hour,
                                    end_inclusive=end_inclusive)
            category = result['classification']
            if category == 'NIGHT' and unit['question_type'] not in MATERIAL_TYPES:
                category = 'PENDING'
            if category != unit['category']:
                rows.append({'unit_id': unit['id'], 'old_category': unit['category'],
                             'preview_category': category, 'old_rule_version': unit['rule_version'],
                             'preview_rule_version': f'{RULE_VERSION}+night-end-{end_hour:02d}-{int(end_inclusive)}',
                             'reason': result['reason']})
        return rows

    def _row(self, unit_id):
        row = self.db.one('SELECT * FROM performance_units WHERE id=?', (unit_id,))
        if row is None:
            raise ValueError('Unknown counting unit')
        return row

    def _event(self, unit_id, event, actor, reason, evidence=None, before=None, after=None):
        self.db.execute('''INSERT INTO performance_events(unit_id,event,actor,reason,evidence,before_json,after_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)''', (unit_id, event, actor, reason, encode(evidence) if evidence is not None else None,
                                         encode(before) if before is not None else None,
                                         encode(after) if after is not None else None, now()))

    def record_source_time(self, message_id: str, sent_at: str, *, source: str,
                           message_locator: str, evidence: dict, actor: str = 'system') -> None:
        """Record a traceable ORIGINAL message send time; observation time is never substituted."""
        if source not in TIME_SOURCES or not message_locator or not evidence:
            raise ValueError('Original message time requires a verified source and locator')
        try:
            stamp = datetime.fromisoformat(sent_at)
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid original message time') from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('Original message time must include an offset')
        conflict = False
        with self.db.transaction():
            row = self.db.one('SELECT id,source,source_sent_at,source_time_evidence FROM messages WHERE id=?', (message_id,))
            if not row:
                raise ValueError('Unknown message')
            if row['source'].upper() == 'OPERATOR_TEST':
                raise ValueError('Operator test messages are excluded from formal performance')
            if row['source_sent_at'] and row['source_sent_at'] != sent_at:
                conflict = True
                for unit in self.db.all('SELECT * FROM performance_units WHERE first_message_id=?', (message_id,)):
                    self.db.execute("UPDATE performance_units SET status='PENDING',category='PENDING',confirmed_quantity=0,category_basis='原始时间冲突待核验',updated_at=? WHERE id=?",
                                    (now(), unit['id']))
                    self._event(unit['id'], 'SOURCE_TIME_CONFLICT', actor, '原始消息出现冲突时间',
                                evidence={'existing': row['source_sent_at'], 'new': sent_at, 'locator': message_locator},
                                before=dict(unit), after={'status': 'PENDING', 'category': 'PENDING', 'confirmed_quantity': 0})
            else:
                proof = {'source': source, 'message_locator': message_locator, 'evidence': evidence, 'actor': actor}
                if not row['source_sent_at']:
                    self.db.execute('UPDATE messages SET source_sent_at=?,source_time_evidence=? WHERE id=?',
                                    (sent_at, encode(proof), message_id))
                # Existing units remain pending until explicitly refreshed; never silently rewrite a confirmed classification.
                for unit in self.db.all('SELECT id FROM performance_units WHERE first_message_id=? AND status=?',
                                        (message_id, 'PENDING')):
                    current = self._row(unit['id'])
                    if current['category_basis'] != '原始时间冲突待核验':
                        self._refresh_category(unit['id'], actor=actor, reason='ORIGINAL_TIME_RECORDED')
        if conflict:
            raise ValueError('Conflicting original time; units demoted for manual investigation')

    def resolve_source_time_conflict(self, message_id: str, chosen_sent_at: str, *, actor: str,
                                     reason: str, evidence: str):
        if not actor or not reason or not evidence:
            raise ValueError('Time conflict resolution requires reviewer, reason and evidence')
        stamp = datetime.fromisoformat(chosen_sent_at)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('Chosen original time requires explicit offset')
        with self.db.transaction():
            message = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
            if not message:
                raise ValueError('Unknown message')
            units = self.db.all('SELECT * FROM performance_units WHERE first_message_id=?', (message_id,))
            if not any(u['category_basis'] == '原始时间冲突待核验' for u in units):
                raise ValueError('No unresolved time conflict')
            proof = {'source': 'operator_verified_original', 'message_locator': message_id,
                     'evidence': evidence, 'actor': actor, 'reason': reason,
                     'supersedes': message['source_time_evidence']}
            self.db.execute('UPDATE messages SET source_sent_at=?,source_time_evidence=? WHERE id=?',
                            (chosen_sent_at, encode(proof), message_id))
            for unit in units:
                if unit['category_basis'] == '原始时间冲突待核验':
                    self.db.execute("UPDATE performance_units SET category_basis='时间冲突人工核准，待重新分类' WHERE id=?",
                                    (unit['id'],))
                    self._refresh_category(unit['id'], actor=actor, reason=reason)
                    self._event(unit['id'], 'SOURCE_TIME_RESOLVED', actor, reason,
                                evidence={'chosen_sent_at': chosen_sent_at, 'evidence': evidence})

    def _classification(self, message):
        if not message['source_sent_at'] or not message['source_time_evidence']:
            return 'PENDING', '时间待核验：缺少原始发送时间或消息证据', None
        result = classify_night(message['source_sent_at'],
                                time_evidence=message['source_time_evidence'],
                                timezone_name=self.timezone_name,
                                end_hour=self.night_end_hour,
                                end_inclusive=self.night_end_inclusive)
        return result['classification'], result.get('reason') or '学生首次独立提问时间', result.get('window_date')

    def _refresh_category(self, unit_id, *, actor, reason):
        unit = self._row(unit_id)
        if unit['status'] != 'PENDING':
            raise ValueError('Confirmed classification changes require audited revision')
        message = self.db.one('SELECT * FROM messages WHERE id=?', (unit['first_message_id'],))
        category, basis, window_date = self._classification(message)
        measure = '篇' if category == 'NIGHT' and unit['question_type'] in MATERIAL_TYPES else unit['measure_unit']
        if category == 'NIGHT' and unit['question_type'] not in MATERIAL_TYPES:
            category, basis, measure = 'PENDING', '夜间零散知识点的篇归属待核验', '待核验'
        before = {'category': unit['category'], 'measure_unit': unit['measure_unit']}
        if unit['question_type'] == '整套试卷':
            category, basis, measure, window_date = 'PENDING', '整套试卷批改请联系班主任，计量待核验', '待核验', None
        self.db.execute('''UPDATE performance_units SET question_time=?,question_time_source=?,category=?,category_basis=?,
            night_window_date=?,measure_unit=?,updated_at=? WHERE id=?''',
            (message['source_sent_at'], message['source_time_evidence'], category, basis,
             window_date, measure, now(), unit_id))
        self._event(unit_id, 'CATEGORY_REFRESHED', actor, reason, before=before,
                    after={'category': category, 'measure_unit': measure})

    def create_unit(self, first_message_id: str, question_type: str, *, scope_key: str,
                    grouping_reason: str, question_id: str | None = None,
                    material_id: str | None = None, new_question_parent_id: str | None = None,
                    new_question_reviewer: str | None = None, new_question_evidence: str | None = None) -> str:
        """One stable scope per student. New substantive questions require verified evidence."""
        if not scope_key or not grouping_reason:
            raise ValueError('A stable grouping identity and reason are required')
        if new_question_parent_id and not (new_question_reviewer and new_question_evidence):
            raise ValueError('Substantive new question must be reviewed before creating a unit')
        with self.db.transaction() if not self.db.connection.in_transaction else nullcontext():
            message = self.db.one('SELECT * FROM messages WHERE id=?', (first_message_id,))
            if not message or not message['case_id']:
                raise ValueError('First question must be a linked source message')
            if message['source'].upper() == 'OPERATOR_TEST':
                raise ValueError('Operator test messages are excluded from formal performance')
            if question_id:
                q = self.db.one('SELECT * FROM questions WHERE id=?', (question_id,))
                if not q or q['case_id'] != message['case_id']:
                    raise ValueError('Question does not belong to source case')
                if material_id is None:
                    material_id = q['material_id']
            if question_type in MATERIAL_TYPES and not material_id:
                raise ValueError('Material-based types require a material identity')
            if material_id:
                material = self.db.one('SELECT case_id FROM materials WHERE id=?', (material_id,))
                if not material or material['case_id'] != message['case_id']:
                    raise ValueError('Material does not belong to source case')
            if new_question_parent_id:
                parent = self._row(new_question_parent_id)
                if parent['binding_id'] != message['binding_id']:
                    raise ValueError('Linked new question crosses student identity')
            existing = self.db.one('SELECT id FROM performance_units WHERE binding_id=? AND scope_key=?',
                                   (message['binding_id'], scope_key))
            if existing:
                self._link(existing['id'], first_message_id, question_id, 'OTHER', '重复范围归入原单元')
                return existing['id']
            category, basis, window_date = self._classification(message)
            if material_id and not new_question_parent_id and (category == 'NIGHT' or question_type in COMPREHENSIVE_TYPES):
                same_material = self.db.one('''SELECT id FROM performance_units WHERE binding_id=? AND material_id=?
                    AND question_type=? AND status!='REVOKED' ORDER BY created_at LIMIT 1''',
                    (message['binding_id'], material_id, question_type))
                if same_material:
                    self._link(same_material['id'], first_message_id, question_id, 'OTHER', '同材料归并')
                    return same_material['id']
            if message['intent'] not in ('NEW', 'SUBQUESTION') and not new_question_parent_id:
                raise ValueError('Follow-up or correction cannot start an independent unit without substantive-new review')
            if question_type in MATERIAL_TYPES:
                unit = '篇' if category == 'NIGHT' or question_type in COMPREHENSIVE_TYPES else '题'
            else:
                unit = '独立知识点' if category == 'REGULAR' and question_type == '独立知识点' else '待核验'
            if category == 'NIGHT' and question_type not in MATERIAL_TYPES:
                category, basis = 'PENDING', '夜间零散知识点的篇归属待核验'
                window_date = None
            if question_type == '整套试卷':
                category, basis, unit, window_date = 'PENDING', '整套试卷批改请联系班主任，计量待核验', '待核验', None
            uid = new_id()
            self.db.execute('''INSERT INTO performance_units(id,case_id,binding_id,material_id,scope_key,question_type,
                measure_unit,first_message_id,question_time,question_time_source,category,category_basis,night_window_date,
                rule_version,grouping_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uid, message['case_id'], message['binding_id'], material_id, scope_key, question_type, unit,
                 first_message_id, message['source_sent_at'], message['source_time_evidence'], category, basis, window_date,
                 self.rule_version, grouping_reason, now(), now()))
            self._link(uid, first_message_id, question_id, 'FIRST', grouping_reason)
            self._event(uid, 'UNIT_CREATED', new_question_reviewer or 'system', grouping_reason,
                        evidence={'parent_id': new_question_parent_id, 'new_question_evidence': new_question_evidence})
            return uid

    def _link(self, unit_id, message_id, question_id, kind, reason):
        unit = self._row(unit_id)
        message = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
        if not message or message['binding_id'] != unit['binding_id']:
            raise ValueError('Activity must belong to the same student')
        if message['source'].upper() == 'OPERATOR_TEST':
            raise ValueError('Operator test messages are excluded from formal performance')
        if question_id:
            q = self.db.one('SELECT * FROM questions WHERE id=?', (question_id,))
            if not q or q['case_id'] != message['case_id']:
                raise ValueError('Question and message mismatch')
            if unit['material_id'] and q['material_id'] != unit['material_id']:
                raise ValueError('Different material requires reviewed reassignment')
        turn = self.db.one('SELECT id,question_version FROM turns WHERE message_id=?', (message_id,))
        self.db.execute('''INSERT OR IGNORE INTO performance_links
            (id,unit_id,message_id,question_id,turn_id,question_version,link_kind,reason,linked_at)
            VALUES(?,?,?,?,?,?,?,?,?)''', (new_id(), unit_id, message_id, question_id,
                                          turn['id'] if turn else None, turn['question_version'] if turn else None,
                                          kind, reason, now()))

    def link_activity(self, unit_id: str, message_id: str, *, question_id: str | None = None,
                      kind: str = 'FOLLOWUP', reason: str) -> None:
        if kind not in ACTIVITY_KINDS or kind == 'FIRST' or not reason:
            raise ValueError('Valid activity type and reason required')
        with self.db.transaction() if not self.db.connection.in_transaction else nullcontext():
            before = self.db.one('SELECT COUNT(*) FROM performance_links WHERE unit_id=?', (unit_id,))[0]
            self._link(unit_id, message_id, question_id, kind, reason)
            after = self.db.one('SELECT COUNT(*) FROM performance_links WHERE unit_id=?', (unit_id,))[0]
            if after > before:
                self._event(unit_id, 'ACTIVITY_LINKED', 'system', reason, evidence={'message_id': message_id, 'kind': kind})
                if kind == 'SUBQUESTION' and self._row(unit_id)['status'] == 'CONFIRMED':
                    self.db.execute("UPDATE performance_units SET status='PENDING',confirmed_quantity=0,updated_at=? WHERE id=?",
                                    (now(), unit_id))
                    self._event(unit_id, 'NEW_SUBQUESTION_PENDING', 'system', '同篇新增待答小题，完成状态需重新核验',
                                evidence={'message_id': message_id})

    def record_first_response(self, unit_id: str, outbox_id: str) -> None:
        with self.db.transaction():
            unit = self._row(unit_id)
            outbox = self.db.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
            if not outbox or outbox['binding_id'] != unit['binding_id'] or outbox['message_id'] != unit['first_message_id'] or outbox['purpose'] != 'ACK':
                raise ValueError('Acknowledgement does not match this student')
            if outbox['state'] != 'SENT_UI_CONFIRMED' or outbox['simulated'] or not outbox['sent_at']:
                raise ValueError('Only real confirmed acknowledgement is admissible')
            self.db.execute('UPDATE performance_units SET first_response_at=?,updated_at=? WHERE id=?',
                            (outbox['sent_at'], now(), unit_id))
            self._event(unit_id, 'FIRST_RESPONSE_RECORDED', 'system', '已确认收到', evidence={'outbox_id': outbox_id})

    def record_delivery(self, unit_id: str, outbox_id: str) -> None:
        """Capture actual delivered answer; human content/form review is still needed."""
        with self.db.transaction():
            unit = self._row(unit_id)
            eligibility = self.delivery_eligibility(unit, outbox_id=outbox_id)
            if not eligibility['eligible']:
                raise ValueError(eligibility['reason'])
            outbox = self.db.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
            self.db.execute('''UPDATE performance_units SET completed_at=?,completion_outbox_id=?,status='PENDING',
                confirmed_quantity=0,updated_at=? WHERE id=?''', (outbox['sent_at'], outbox_id, now(), unit_id))
            self._event(unit_id, 'DELIVERY_RECORDED', 'system', '已读取实际发送证据', evidence={'outbox_id': outbox_id})

    def delivery_eligibility(self, unit, *, outbox_id=None):
        """Read current delivery facts without mutating cached counts or reviews.

        Human answers retain the existing outbox/readback evidence path; machine
        answers additionally require their frozen generation evidence. Neither
        path requires another answer approval.
        """
        unit = dict(unit)
        def reject(reason):
            return {'eligible': False, 'reason': reason}
        row = self.db.one('SELECT * FROM outbox WHERE id=?',
                          (outbox_id or unit.get('completion_outbox_id'),))
        if (not row or row['purpose'] not in ('ANSWER', 'CORRECTION')
                or row['state'] != 'SENT_UI_CONFIRMED' or row['simulated'] != 0
                or not row['sent_at'] or not row['body'].strip()):
            return reject('Only a verified real answer delivery can count')
        binding = self.db.one('SELECT * FROM bindings WHERE id=?', (unit['binding_id'],))
        message = self.db.one('SELECT * FROM messages WHERE id=?', (row['message_id'],))
        first = self.db.one('SELECT * FROM messages WHERE id=?', (unit['first_message_id'],))
        case = self.db.one('SELECT * FROM cases WHERE id=?', (row['case_id'],))
        turn = self.db.one('SELECT * FROM turns WHERE id=?', (row['turn_id'],))
        question = self.db.one('SELECT * FROM questions WHERE id=?', (turn['question_id'],)) if turn else None
        if (not binding or not binding['verified'] or not message or not first or not case
                or any(m['binding_id'] != binding['id'] or m['source'].upper() == 'OPERATOR_TEST'
                       for m in (message, first))
                or row['binding_id'] != binding['id'] or case['binding_id'] != binding['id']
                or row['case_id'] != unit['case_id'] or message['case_id'] != row['case_id']
                or first['case_id'] != unit['case_id'] or not turn or not question
                or turn['message_id'] != row['message_id'] or turn['case_id'] != row['case_id']
                or question['case_id'] != row['case_id'] or message['question_id'] != question['id']
                or unit.get('material_id') and unit['material_id'] != question['material_id']):
            return reject('Delivery source or recipient binding is no longer valid')
        if not self.db.one('''SELECT 1 FROM performance_links WHERE unit_id=? AND message_id=?
            AND (question_id IS NULL OR question_id=?)''', (unit['id'], row['message_id'], question['id'])):
            return reject('Delivered turn is not linked to this counting unit')
        # The service also increments context_revision for ordinary followups.
        # They do not revoke work already delivered for the unchanged question.
        later = self.db.all('''SELECT id,intent,question_version,context_revision FROM turns
            WHERE question_id=? AND context_revision>? ORDER BY context_revision''',
            (question['id'], row['context_revision']))
        ordinary_followups = bool(later and question['current_version'] == row['question_version']
            and [t['context_revision'] for t in later] ==
                list(range(row['context_revision'] + 1, question['context_revision'] + 1))
            and all(t['intent'] == 'FOLLOWUP' and t['question_version'] == row['question_version'] for t in later))
        if ordinary_followups:
            for later_turn in later:
                audits = self.db.all("SELECT details FROM audit WHERE event='MESSAGE_LINKED' AND question_id=? AND turn_id=?",
                                     (question['id'], later_turn['id']))
                try:
                    proof = [json.loads(a['details']) for a in audits]
                    if len(proof) != 1 or any(proof[0].get(k) != later_turn[k]
                                              for k in ('intent', 'question_version', 'context_revision')):
                        ordinary_followups = False
                except (ValueError, TypeError):
                    ordinary_followups = False
        version = self.db.one('SELECT material_version FROM question_versions WHERE id=?', (row['question_version'],))
        material = self.db.one('SELECT current_version FROM materials WHERE id=?', (question['material_id'],))
        if not version or not material or version['material_version'] != material['current_version']:
            return reject('Delivered material version is no longer current')
        if (question['current_version'] != row['question_version']
                or question['context_revision'] != row['context_revision'] and not ordinary_followups
                or (turn['question_version'], turn['context_revision']) !=
                    (row['question_version'], row['context_revision'])):
            return reject('Current answer version and delivery readback must be valid')
        from .performance_rules import timestamp
        try:
            sent = timestamp(row['sent_at'])
            if first['source_sent_at'] and (not unit.get('question_time')
                    or timestamp(first['source_sent_at']) != timestamp(unit['question_time'])):
                return reject('Original question time differs from the counting evidence')
            if unit.get('question_time') and sent < timestamp(unit['question_time']):
                return reject('Delivery precedes original question time')
            if outbox_id is None and (not unit.get('completed_at') or timestamp(unit['completed_at']) != sent):
                return reject('Cached completion time differs from actual delivery')
        except (ValueError, TypeError):
            return reject('Actual delivery time must include a valid timezone')
        check = self.db.one('SELECT * FROM delivery_checks WHERE outbox_id=? ORDER BY rowid DESC LIMIT 1', (row['id'],))
        if not check or check['status'] != 'SENT_UI_CONFIRMED':
            return reject('Delivery readback evidence missing or invalidated')
        try:
            evidence = json.loads(check['evidence'])
            if not isinstance(evidence, dict) or evidence.get('simulated') or evidence.get('confirmed') is False:
                return reject('Delivery readback evidence is not a real confirmation')
            if ('body_hash' in evidence and evidence['body_hash'] != sha256(row['body'].encode('utf-8')).hexdigest()
                    or 'outbox_id' in evidence and evidence['outbox_id'] != row['id']
                    or 'binding_id' in evidence and evidence['binding_id'] != binding['id']):
                return reject('Delivery readback does not match the delivered answer')
            if 'confirmed_at' in evidence and timestamp(evidence['confirmed_at']) != sent:
                return reject('Delivery readback time differs from actual delivery')
            if evidence.get('verification_method') == 'MANUAL_ATTESTATION':
                from .manual_delivery import validate_manual_package
                validate_manual_package(self.db, row, evidence)
            from .delivery_batches import read_plan, validate_complete
            if read_plan(self.db, row) or evidence.get('verification_method') == 'ORDERED_TEXT_BATCH':
                validate_complete(self.db, row, evidence)
            if row['answer_id'] or row['run_id']:
                if (evidence.get('confirmed') is not True or evidence.get('simulated') is not False
                        or evidence.get('body_hash') != sha256(row['body'].encode('utf-8')).hexdigest()):
                    return reject('Generated answer delivery requires matching real readback')
                if ordinary_followups:
                    answer = self.db.one('SELECT * FROM answers WHERE id=?', (row['answer_id'],))
                    generated = self.db.one('SELECT * FROM answer_evidence WHERE answer_id=?', (row['answer_id'],))
                    if (not answer or not generated or answer['state'] not in ('GENERATED', 'STALE')
                            or answer['text'] != row['body'] or answer['turn_id'] != row['turn_id']
                            or (answer['question_version'], answer['context_revision']) !=
                                (row['question_version'], row['context_revision'])
                            or not generated['complete'] or not generated['uploads_confirmed']
                            or generated['simulated'] != 0):
                        return reject('Frozen generated answer evidence is no longer valid')
                else:
                    from .test_answer_queue import validate_source_answer
                    validate_source_answer(self.db, row, approval=False)
                run = self.db.one('SELECT * FROM runs WHERE id=?', (row['run_id'],))
                if (not run or run['state'] != 'GENERATED' or run['turn_id'] != row['turn_id']
                        or (run['question_version'], run['context_revision']) !=
                        (row['question_version'], row['context_revision'])):
                    return reject('Frozen generated answer run is no longer valid')
                snapshot = json.loads(run['input_json'])
                answer_evidence = self.db.one('SELECT * FROM answer_evidence WHERE answer_id=?', (row['answer_id'],))
                if (snapshot.get('simulated') is not False
                        or snapshot.get('binding_id') != binding['id'] or snapshot.get('case_id') != row['case_id']
                        or snapshot.get('question_id') != question['id'] or snapshot.get('run_id') != run['id']
                        or snapshot.get('question_version') != row['question_version']
                        or snapshot.get('context_revision') != row['context_revision']
                        or not answer_evidence or answer_evidence['session_id'] != run['session_id']
                        or answer_evidence['correct_option_id'] not in
                            {o['id'] for o in snapshot['student_question']['options']}):
                    return reject('Frozen generated answer context is no longer valid')
        except (ValueError, TypeError, KeyError, OSError):
            return reject('Answer or delivery evidence is no longer valid')
        return {'eligible': True, 'reason': None}

    def request_conversion(self, unit_id: str, requested: int, *, actor: str, reason: str):
        with self.db.transaction():
            unit = self._row(unit_id)
            if (unit['category'] != 'REGULAR' or unit['question_type'] not in ('听力', '语法填空')
                    or type(requested) is not int or not 2 <= requested <= 6):
                raise ValueError('Extension applies only to ordinary listening/grammar, 2–6 questions')
            self.db.execute('UPDATE performance_units SET requested_conversion=?,updated_at=? WHERE id=?',
                            (requested, now(), unit_id))
            if unit['status'] == 'CONFIRMED':
                self.db.execute("UPDATE performance_units SET status='PENDING',confirmed_quantity=0 WHERE id=?", (unit_id,))
            self._event(unit_id, 'CONVERSION_REQUESTED', actor, reason, after={'requested': requested})

    def approve_conversion(self, unit_id: str, approved: int, *, approver: str, authority: str, evidence: str):
        if authority not in ('教研老师', '班主任') or not approver or not evidence:
            raise ValueError('Approved extension requires designated authority and evidence')
        with self.db.transaction():
            unit = self._row(unit_id)
            if unit['category'] == 'NIGHT' or unit['question_type'] not in ('听力', '语法填空') or not unit['requested_conversion']:
                raise ValueError('No eligible extension request')
            if type(approved) is not int or not 2 <= approved <= 6:
                raise ValueError('Approved conversion must be 2–6')
            self.db.execute('''UPDATE performance_units SET approved_conversion=?,conversion_approver=?,
                conversion_approved_at=?,conversion_evidence=?,updated_at=? WHERE id=?''',
                (approved, f'{authority}:{approver}', now(), evidence, now(), unit_id))
            self._event(unit_id, 'CONVERSION_APPROVED', approver, '指定角色核准折算', evidence={'authority': authority, 'evidence': evidence},
                        after={'approved': approved})

    def confirm(self, unit_id: str, *, reviewer: str, evidence: str, actual_question_count: int = 1,
                timeliness_status: str = 'PENDING', cross_student_disposition: str | None = None) -> None:
        if not reviewer or not evidence or type(actual_question_count) is not int or actual_question_count < 1:
            raise ValueError('Human reviewer, evidence and actual work count required')
        if timeliness_status not in ('PENDING', 'PASS', 'FAIL', 'EXEMPT'):
            raise ValueError('Invalid timeliness status')
        with self.db.transaction():
            unit = self._row(unit_id)
            if unit['status'] in ('EXCLUDED', 'REVOKED') or unit['category'] == 'PENDING' or unit['measure_unit'] == '待核验':
                raise ValueError('Unresolved or withdrawn unit cannot be confirmed')
            if not unit['completed_at'] or not unit['completion_outbox_id']:
                raise ValueError('Actual answer delivery evidence required')
            eligibility = self.delivery_eligibility(unit)
            if not eligibility['eligible']:
                raise ValueError(eligibility['reason'])
            if unit['requested_conversion'] and unit['approved_conversion'] is None:
                raise ValueError('Requested extension requires designated approval before confirmation')
            if unit['material_id']:
                others = self.db.one('''SELECT u.id FROM performance_units u JOIN materials m ON m.id=u.material_id
                    JOIN material_versions mv ON mv.id=m.current_version
                    WHERE u.id!=? AND u.binding_id!=? AND mv.verified_text=(SELECT mv2.verified_text
                        FROM materials m2 JOIN material_versions mv2 ON mv2.id=m2.current_version WHERE m2.id=?)
                    AND mv.verified_text IS NOT NULL LIMIT 1''', (unit_id, unit['binding_id'], unit['material_id']))
                if others and not cross_student_disposition:
                    raise ValueError('Cross-student same-material counting requires reviewed disposition')
            # Night always one material piece, independent of number of blanks or ordinary conversion.
            quantity = 1 if unit['category'] == 'NIGHT' or unit['measure_unit'] == '篇' else (unit['approved_conversion'] or actual_question_count)
            before = dict(unit)
            self.db.execute('''UPDATE performance_units SET status='CONFIRMED',confirmed_quantity=?,actual_question_count=?,
                review_actor=?,review_at=?,review_evidence=?,timeliness_status=?,updated_at=? WHERE id=?''',
                (quantity, actual_question_count, reviewer, now(), evidence, timeliness_status, now(), unit_id))
            self._event(unit_id, 'UNIT_CONFIRMED', reviewer, '人工核验正确性及交付形式', evidence={'review': evidence},
                        before=before, after={'status': 'CONFIRMED', 'confirmed_quantity': quantity,
                                              'cross_student_disposition': cross_student_disposition})

    def revise(self, unit_id: str, *, actor: str, reason: str, evidence: str,
               status: str = 'PENDING') -> None:
        if status not in ('PENDING', 'EXCLUDED', 'REVOKED') or not actor or not reason or not evidence:
            raise ValueError('Audited revision requires actor, reason and evidence')
        with self.db.transaction():
            before = dict(self._row(unit_id))
            self.db.execute('''UPDATE performance_units SET status=?,confirmed_quantity=0,review_actor=?,review_at=?,
                review_evidence=?,updated_at=? WHERE id=?''', (status, actor, now(), evidence, now(), unit_id))
            self._event(unit_id, 'UNIT_REVISED', actor, reason, evidence={'revision': evidence},
                        before=before, after={'status': status, 'confirmed_quantity': 0})

    def merge(self, target_id: str, source_id: str, *, actor: str, reason: str, evidence: str):
        if target_id == source_id or not actor or not reason or not evidence:
            raise ValueError('Distinct units and audited reason required')
        with self.db.transaction():
            target, source = self._row(target_id), self._row(source_id)
            if target['binding_id'] != source['binding_id'] or source['status'] == 'REVOKED':
                raise ValueError('Cannot merge across students or from revoked source')
            if target['material_id'] and source['material_id'] and target['material_id'] != source['material_id']:
                raise ValueError('Different material requires separate reviewed scope')
            if not target['question_time'] or not source['question_time']:
                raise ValueError('Original question times must be verified before merging')
            if datetime.fromisoformat(source['question_time']) < datetime.fromisoformat(target['question_time']):
                raise ValueError('Merge target must hold the earliest independent question time')
            for link in self.db.all('SELECT * FROM performance_links WHERE unit_id=?', (source_id,)):
                self._link(target_id, link['message_id'], link['question_id'], link['link_kind'], reason)
            self.db.execute("UPDATE performance_units SET status='REVOKED',confirmed_quantity=0,updated_at=? WHERE id=?", (now(), source_id))
            self.db.execute("UPDATE performance_units SET status='PENDING',confirmed_quantity=0,updated_at=? WHERE id=?", (now(), target_id))
            self._event(source_id, 'MERGED_AWAY', actor, reason, evidence={'target': target_id, 'evidence': evidence}, before=dict(source))
            self._event(target_id, 'MERGE_REVIEW_REQUIRED', actor, reason, evidence={'source': source_id, 'evidence': evidence}, before=dict(target))

    def split(self, source_id: str, *, first_message_id: str, scope_key: str, actor: str,
              reason: str, evidence: str, new_question_verified: bool = False,
              move_message_ids: tuple[str, ...] = ()) -> str:
        if not new_question_verified or not actor or not evidence:
            raise ValueError('Split into a new question requires substantive-change verification')
        with self.db.transaction():
            source = self._row(source_id)
            if first_message_id == source['first_message_id']:
                raise ValueError('Split needs a later independent question message')
            linked = self.db.one('SELECT id FROM performance_links WHERE unit_id=? AND message_id=?', (source_id, first_message_id))
            if not linked:
                raise ValueError('New question message must already be linked to old scope for review')
            if self.db.one('SELECT id FROM performance_units WHERE binding_id=? AND scope_key=?',
                           (source['binding_id'], scope_key)):
                raise ValueError('New question needs a fresh distinct scope key')
            new_id_ = self.create_unit(first_message_id, source['question_type'], scope_key=scope_key,
                                       grouping_reason=reason, material_id=source['material_id'],
                                       new_question_parent_id=source_id, new_question_reviewer=actor,
                                       new_question_evidence=evidence)
            if new_id_ == source_id:
                raise ValueError('Split must create a distinct scope')
            for mid in (first_message_id, *move_message_ids):
                self.db.execute('DELETE FROM performance_links WHERE unit_id=? AND message_id=?', (source_id, mid))
                if mid != first_message_id:
                    message = self.db.one('SELECT question_id FROM messages WHERE id=?', (mid,))
                    if not message:
                        raise ValueError('Unknown moved message')
                    self._link(new_id_, mid, message['question_id'], 'OTHER', reason)
            self.db.execute("UPDATE performance_units SET status='PENDING',confirmed_quantity=0,updated_at=? WHERE id=?", (now(), source_id))
            self._event(source_id, 'SPLIT_REVIEW_REQUIRED', actor, reason, evidence={'new_unit_id': new_id_, 'evidence': evidence})
        return new_id_

    def list_units(self):
        return [dict(row) for row in self.db.all('''SELECT u.*,b.group_key,b.student_key,b.display_name FROM performance_units u
            JOIN bindings b ON b.id=u.binding_id ORDER BY u.created_at,u.id''')]

    def pending(self):
        return [u for u in self.list_units() if u['status'] == 'PENDING' or u['category'] == 'PENDING']

    def list_unlinked_messages(self):
        """Discovery queue; unlinked business work must not silently appear as zero."""
        return [dict(row) for row in self.db.all('''SELECT m.id,m.case_id,m.question_id,m.binding_id,m.intent,m.source_sent_at,
            m.source_time_evidence,m.observed_at,b.group_key,b.student_key,b.display_name FROM messages m
            JOIN bindings b ON b.id=m.binding_id
            LEFT JOIN performance_links l ON l.message_id=m.id
            WHERE m.intent!='IRRELEVANT' AND m.status!='IGNORED' AND upper(m.source)!='OPERATOR_TEST'
            AND l.id IS NULL ORDER BY m.created_at,m.id''')]

    def reconcile_stale_deliveries(self):
        """Audited demotion when a counted answer has become a superseded version."""
        changed = []
        with self.db.transaction():
            for unit in self.db.all("SELECT * FROM performance_units WHERE status='CONFIRMED' AND completion_outbox_id IS NOT NULL"):
                if not self.delivery_eligibility(unit)['eligible']:
                    self.db.execute("UPDATE performance_units SET status='PENDING',confirmed_quantity=0,updated_at=? WHERE id=?",
                                    (now(), unit['id']))
                    self._event(unit['id'], 'DELIVERY_VERSION_RECHECK', 'system', '已计入答案被替换或交付证据失效',
                                evidence={'outbox_id': unit['completion_outbox_id']}, before=dict(unit),
                                after={'status': 'PENDING', 'confirmed_quantity': 0})
                    changed.append(unit['id'])
        return changed

