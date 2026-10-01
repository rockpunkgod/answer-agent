"""Human-verified delivery parts in the existing Outbox/check/audit tables.

Recording past delivery has no desktop or sending capability. Part contents are
immutable, and a partial package cannot qualify as completed performance.
"""
from datetime import timezone
from contextlib import nullcontext
from hashlib import sha256
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from .domain import new_id
from .performance_rules import timestamp
from .storage import encode, now


METHOD = 'MANUAL_ATTESTATION'
PREFIX = 'manual-delivery:'


def _part_rows(store, original_id):
    return store.all('''SELECT o.*,d.evidence,d.status AS check_status FROM outbox o JOIN delivery_checks d ON d.outbox_id=o.id
        WHERE json_extract(d.evidence,'$.verification_method')=?
        AND json_extract(d.evidence,'$.original_outbox_id')=? ORDER BY o.sent_at,o.rowid''', (METHOD, original_id))


def validate_manual_package(store, row, evidence):
    """A manual part counts only when the declared original package is complete."""
    origin = store.one('SELECT * FROM outbox WHERE id=?', (evidence.get('original_outbox_id'),))
    plan = store.one("SELECT details FROM audit WHERE event='MANUAL_DELIVERY_PLAN' AND outbox_id=?",
                     (evidence.get('original_outbox_id'),))
    if not origin or not plan:
        raise ValueError('Manual delivery package has no original task or plan')
    plan = json.loads(plan['details'])
    binding = store.one('SELECT * FROM bindings WHERE id=?', (origin['binding_id'],))
    total = plan.get('total_parts')
    if (type(total) is not int or not 1 <= total <= 12 or not binding or not binding['verified']
            or origin['simulated'] != 0 or origin['purpose'] not in ('ANSWER','CORRECTION')):
        raise ValueError('Manual delivery package scope is invalid')
    seen = set()
    rows = _part_rows(store, origin['id'])
    for part in rows:
        proof = json.loads(part['evidence'])
        number = proof.get('part_number')
        if (type(number) is not int or number in seen or not 1 <= number <= total
                or proof.get('total_parts') != total or proof.get('confirmed') is not True
                or proof.get('simulated') is not False or not proof.get('reviewer') or not proof.get('verification_evidence')
                or part['check_status'] != 'SENT_UI_CONFIRMED' or proof.get('sender_role') != 'TEACHER'
                or proof.get('verification_method') != METHOD
                or proof.get('group_key') != binding['group_key'] or proof.get('student_key') != binding['student_key']
                or proof.get('body_hash') != sha256(part['body'].encode()).hexdigest()
                or proof.get('outbox_id') != part['id'] or proof.get('binding_id') != origin['binding_id']
                or part['state'] != 'SENT_UI_CONFIRMED' or part['simulated'] != 0
                or timestamp(proof.get('confirmed_at')) != timestamp(part['sent_at'])
                or any(part[k] != origin[k] for k in ('message_id','case_id','turn_id','binding_id','purpose',
                                                     'question_version','context_revision'))
                or any(plan.get(k) != origin[k] for k in ('question_version','context_revision','binding_id'))):
            raise ValueError('Manual delivery part evidence changed or is incomplete')
        seen.add(number)
        if proof.get('source_record') is not None:
            registry = ManualDeliveries(store)
            scope = registry._reply_scope(original_outbox_id=origin['id'])
            try:
                with scope['collector'].connect() as source_db:
                    source = source_db.execute('SELECT * FROM messages WHERE message_id=?',
                                              (proof['source_record'].get('collector_message_id'),)).fetchone()
                    actual = registry._reply_record(source, scope)
            except (OSError, sqlite3.Error):
                raise ValueError('保存的老师回复暂不能重新核验，请检查原消息库') from None
            if (actual['proof'] != proof['source_record'] or actual['content'] != proof['actual_content']
                    or timestamp(actual['delivered_at']) != timestamp(part['sent_at'])):
                raise ValueError('Saved teacher reply evidence changed')
    if seen != set(range(1, total + 1)) or row['id'] not in {p['id'] for p in rows}:
        raise ValueError('Only some delivery parts have been verified')
    # Completion belongs to the last actual delivery, regardless of registration order.
    last = max(rows, key=lambda p: (timestamp(p['sent_at']), json.loads(p['evidence'])['part_number']))
    if row['id'] != last['id']:
        raise ValueError('The completion record must use the last actual delivery part')
    return dict(last)


def completion_for_turn(store, turn_id):
    """Read the original task's verified package for its existing workbench card."""
    for origin in store.all("SELECT * FROM outbox WHERE turn_id=? AND state='CANCELLED' AND last_error='MANUALLY_DELIVERED'", (turn_id,)):
        parts = _part_rows(store, origin['id'])
        try:
            last = max(parts, key=lambda p: (timestamp(p['sent_at']), json.loads(p['evidence'])['part_number']))
            actual = validate_manual_package(store, last, json.loads(last['evidence']))
            current = store.one('SELECT q.current_version FROM questions q JOIN turns t ON t.question_id=q.id WHERE t.id=?', (turn_id,))
            return {'outbox_id': actual['id'], 'completed_at': actual['sent_at'],
                    'question_version': actual['question_version'], 'verification_method': METHOD,
                    'stale': not current or current['current_version'] != actual['question_version']}
        except (ValueError, TypeError, KeyError):
            continue
    return None


class ManualDeliveries:
    def __init__(self, store, *, attachment_root=None):
        self.db = store
        self.attachment_root = Path(attachment_root) if attachment_root is not None else None

    def _reply_scope(self, *, original_outbox_id=None, turn_id=None):
        from .collector_storage import CollectorStore
        from .source_question_tasks import _read_origin_receipt
        if bool(original_outbox_id) == bool(turn_id):
            raise ValueError('请选择一个原答疑任务')
        origin = None
        if original_outbox_id:
            origin = self.db.one('SELECT * FROM outbox WHERE id=?', (original_outbox_id,))
            if (not origin or origin['simulated'] != 0 or origin['purpose'] not in ('ANSWER','CORRECTION')
                    or origin['idempotency_key'].startswith(PREFIX)):
                raise ValueError('请选择原任务的正式答疑稿')
            turn_id = origin['turn_id']
        turn = self.db.one('SELECT * FROM turns WHERE id=?', (turn_id,))
        message = self.db.one('SELECT * FROM messages WHERE id=?', (turn['message_id'],)) if turn else None
        if not message or message['source'].upper() in ('OPERATOR_TEST','MOCK'):
            raise ValueError('本机练习题不能关联正式交付')
        if origin is not None and (origin['message_id'] != message['id'] or origin['case_id'] != turn['case_id']
                or origin['binding_id'] != message['binding_id'] or not self.db.one(
                    'SELECT 1 FROM question_versions WHERE id=? AND question_id=?',
                    (origin['question_version'], turn['question_id']))):
            raise ValueError('原答疑稿与学生、事项或题目版本不一致')
        if not self.db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_question_drafts'"):
            raise ValueError('该任务未绑定可核验的原消息库，请使用人工填写')
        link = self.db.one('SELECT * FROM source_question_drafts WHERE message_id=?', (message['id'],))
        if not link:
            raise ValueError('该任务未绑定可核验的原消息库，请使用人工填写')
        try:
            receipt = _read_origin_receipt(self.db, dict(link))
        except (OSError, sqlite3.Error):
            raise ValueError('原消息库不可用，请检查保存路径或使用人工填写') from None
        if receipt['turn']['id'] != turn['id']:
            raise ValueError('原消息与所选答疑轮次不一致')
        filters = json.loads(link['sender_filters'])
        senders = set(filters['teacher_sender_ids']) | set(filters['self_sender_ids'])
        if not senders or len(senders)>64 or any(not isinstance(s, str) or not 0<len(s)<=256 for s in senders):
            raise ValueError('原采集范围未配置老师身份，请使用人工填写')
        collector = CollectorStore.__new__(CollectorStore)
        collector.path = str(Path(link['collector_path']).resolve(strict=True))
        return {'collector': collector, 'receipt': receipt, 'senders': senders}

    def _reply_record(self, row, scope):
        from .message_sources import normalize_sent_time
        if not row:
            raise ValueError('保存的老师回复不存在')
        with scope['collector'].connect() as source_db:
            if source_db.execute('SELECT 1 FROM message_conflicts WHERE message_id=? LIMIT 1', (row['message_id'],)).fetchone():
                raise ValueError('老师原回复存在采集冲突，请人工核对原消息')
        student = scope['receipt']['source']
        raw = json.loads(row['raw_payload'])
        if (not isinstance(raw, dict) or row['sender_id'] not in scope['senders']
                or any(row[key] != student[key] for key in ('room_id','source_type','source_name'))
                or row['message_type'] != 'text' or row['media_id'] or row['local_media_path'] or row['media_hash']
                or raw.get('attachments') or raw.get('media')
                or row['source_confidence'] != 'high' or row['time_confidence'] != 'high' or row['parse_status'] != 'parsed'
                or row['source_type'] == 'windows_gui' and raw.get('identity_verified') is not True
                or not isinstance(row['raw_content'], str) or not row['raw_content'].strip() or len(row['raw_content'])>24000):
            raise ValueError('这条记录不是可核验的同群老师纯文字回复，请人工核对原消息')
        sent = timestamp(row['sent_at_utc'])
        raw_time, _ = normalize_sent_time(json.loads(row['sent_at_raw']), business_timezone=row['business_timezone'])
        if (sent != timestamp(raw_time) or sent != timestamp(row['sent_at_local']) or sent < timestamp(student['sent_at_utc'])
                or sent > timestamp(now())):
            raise ValueError('老师原始发送时间不完整或与任务不一致')
        quoted = [row[key] for key in ('reply_to_message_id','quoted_message_id') if row[key]]
        aliases = {student['message_id'], student['source_message_id']} - {None}
        if any(reference not in aliases for reference in quoted):
            raise ValueError('这条老师回复引用了其他消息，不能直接登记到所选题目')
        fields = ('message_id','source_type','source_message_id','source_name','room_id','sender_id','message_type',
            'raw_content','normalized_text','media_id','local_media_path','media_hash','reply_to_message_id','quoted_message_id',
            'sent_at_raw','sent_at_utc','sent_at_local','ingested_at','raw_payload','source_confidence','time_confidence','parse_status')
        proof = {'collector_message_id': row['message_id'], 'collector_path': scope['collector'].path,
                 'source_receipt_sha256': scope['receipt']['source_sha256'],
                 'source_evidence_sha256': sha256(encode({key: row[key] for key in fields}).encode()).hexdigest()}
        return {'proof': proof, 'collector_message_id': row['message_id'],
            'source_evidence_sha256': proof['source_evidence_sha256'], 'content': row['raw_content'],
            'delivered_at': sent.astimezone(timezone.utc).isoformat(), 'sender_display_name': row['sender_display_name'],
            'association': 'QUOTED_ORIGINAL' if quoted else 'MANUAL_LINK_REQUIRED'}

    def saved_replies(self, *, original_outbox_id=None, turn_id=None):
        scope = self._reply_scope(original_outbox_id=original_outbox_id, turn_id=turn_id)
        student = scope['receipt']['source']
        with scope['collector'].connect() as source_db:
            rows = source_db.execute('''SELECT * FROM messages WHERE room_id=? AND source_type=? AND source_name=?
                AND sender_id IN (''' + ','.join('?' for _ in scope['senders']) + ') ORDER BY rowid DESC LIMIT 100',
                (student['room_id'],student['source_type'],student['source_name'],*sorted(scope['senders']))).fetchall()
        result=[]
        for row in rows:
            try:
                record = self._reply_record(row, scope)
            except (ValueError, TypeError):
                continue
            result.append({key:value for key,value in record.items() if key != 'proof'})
            if len(result)>=20: break
        return {'status':'READY', 'replies':result, 'continuous_listener':False, 'sends_messages':False,
                'message':'仅列出已入库且身份、原时间可核验的老师纯文字回复；没有可选项不代表群内没有消息。'}

    def register_saved_reply(self, *, collector_message_id, source_evidence_sha256, source_verified,
                             original_outbox_id=None, turn_id=None, **record):
        if (source_verified is not True or not isinstance(collector_message_id, str) or not 0<len(collector_message_id)<=128
                or not isinstance(source_evidence_sha256, str) or not re.fullmatch('[0-9a-f]{64}', source_evidence_sha256)):
            raise ValueError('请核验保存的老师回复、完整内容、原时间与所选题目的归属')
        scope = self._reply_scope(original_outbox_id=original_outbox_id, turn_id=turn_id)
        with scope['collector'].connect() as source_db:
            source_db.execute('BEGIN IMMEDIATE')  # Same source -> business lock order as intake resolution.
            row = source_db.execute('SELECT * FROM messages WHERE message_id=?', (collector_message_id,)).fetchone()
            source = self._reply_record(row, scope)
            if source['source_evidence_sha256'] != source_evidence_sha256:
                raise ValueError('老师原回复已变化，请刷新后重新核对')
            record.update(content=source['content'], delivered_at=source['delivered_at'], attachments=[],
                          _source_record=source['proof'], _defer_counting=True)
            if original_outbox_id:
                result = self.register(original_outbox_id, **record)
            else:
                result = self.register_for_turn(turn_id, **record)
        # Shared counting takes its own source lock; finish only after releasing this one.
        return self._count_completed(result)

    def _count_completed(self, result):
        if result['completion_outbox_id']:
            from .workflow import Workflow
            flow = Workflow(self.db, desktop=SimpleNamespace(simulated=True))
            actual = self.db.one('SELECT * FROM outbox WHERE id=?', (result['completion_outbox_id'],))
            origin = self.db.one('''SELECT o.* FROM outbox o JOIN delivery_checks d
                ON o.id=json_extract(d.evidence,'$.original_outbox_id') WHERE d.outbox_id=?''', (actual['id'],))
            result['counting_status'] = flow._project_verified_manual_counting(actual, original=origin)
        return result

    def _attachments(self, files):
        if not isinstance(files, list) or len(files) > 8 or any(not isinstance(x, str) or len(x) > 240 for x in files):
            raise ValueError('最多选择8个批准目录内的附件')
        result = []
        for filename in files:
            if self.attachment_root is None:
                raise ValueError('人工交付附件目录未配置')
            try:
                root = self.attachment_root.resolve(strict=True)
                path = (root / filename).resolve(strict=True)
            except OSError:
                raise ValueError('交付附件不存在或无法读取，请检查批准目录') from None
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('附件必须位于批准目录且不超过32MB')
            digest, size = sha256(), 0
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise ValueError('附件读取超过32MB')
                    digest.update(chunk)
            result.append({'name': path.name, 'path': str(path), 'sha256': digest.hexdigest(), 'bytes': size})
        if len({x['path'] for x in result}) != len(result):
            raise ValueError('同一附件不能重复登记')
        return result

    def register(self, original_outbox_id, *, question_version, context_revision, reviewer,
                 verification_evidence, delivered_at, content, part_number=1, total_parts=1, attachments=None,
                 _defer_counting=False, _source_record=None):
        from .workflow import Workflow
        if (any(not isinstance(v, str) or not v.strip() or len(v) > limit
                for v, limit in ((reviewer,80),(verification_evidence,4000),(delivered_at,64)))
                or not isinstance(content, str) or len(content) > 24000
                or type(context_revision) is not int or type(part_number) is not int or type(total_parts) is not int
                or not 1 <= part_number <= total_parts <= 12):
            raise ValueError('请填写核验人、实际交付时间、依据及有效的交付部分')
        sent = timestamp(delivered_at)
        if sent > timestamp(now()):
            raise ValueError('实际交付时间不能在未来')
        sent = sent.astimezone(timezone.utc).isoformat()
        files = self._attachments([] if attachments is None else attachments)
        if not content.strip() and not files:
            raise ValueError('请登记实际发送内容或附件，生成草稿不算交付')
        if not files and re.fullmatch(r'\s*(?:收到|已收到|收到啦|好的|谢谢)[\s。.!！]*', content):
            raise ValueError('收到或致谢不算解答，不能登记为完成答疑')
        body = content if content.strip() else '已人工交付附件：' + '、'.join(x['name'] for x in files)
        # Reuse persistence helpers without initializing even a mock transport DB.
        flow = Workflow(self.db, desktop=SimpleNamespace(simulated=True))
        with self.db.transaction() if not self.db.connection.in_transaction else nullcontext():
            origin = self.db.one('SELECT * FROM outbox WHERE id=?', (original_outbox_id,))
            if (not origin or origin['purpose'] not in ('ANSWER','CORRECTION') or origin['simulated'] != 0
                    or origin['idempotency_key'].startswith(PREFIX)):
                raise ValueError('请选择原任务的正式答疑稿，测试副本不能登记正式交付')
            bound = flow._bound(origin)
            message = self.db.one('SELECT * FROM messages WHERE id=?', (origin['message_id'],))
            turn = self.db.one('SELECT * FROM turns WHERE id=?', (origin['turn_id'],))
            if not turn or message['source'].upper() in ('OPERATOR_TEST','MOCK'):
                raise ValueError('本机练习题不能登记正式交付')
            if (origin['question_version'], origin['context_revision']) != (question_version, context_revision):
                raise ValueError('人工交付必须绑定原任务当时的题目与上下文版本')
            if message['source_sent_at'] and timestamp(sent) < timestamp(message['source_sent_at']):
                raise ValueError('交付时间不能早于学生原始提问时间')
            key = PREFIX + origin['id'] + ':' + str(part_number)
            if _source_record is not None:
                used = self.db.one('''SELECT evidence FROM delivery_checks WHERE
                    json_extract(evidence,'$.source_record.collector_path')=? AND
                    json_extract(evidence,'$.source_record.collector_message_id')=?''',
                    (_source_record['collector_path'], _source_record['collector_message_id']))
                if used and (json.loads(used['evidence']).get('original_outbox_id') != origin['id']
                             or json.loads(used['evidence']).get('part_number') != part_number):
                    raise ValueError('这条原回复已关联其他交付部分，请人工核对，不能重复计入')
            existing = self.db.one('SELECT * FROM outbox WHERE idempotency_key=?', (key,))
            old_plan = self.db.one("SELECT details FROM audit WHERE event='MANUAL_DELIVERY_PLAN' AND outbox_id=?", (origin['id'],))
            if old_plan and json.loads(old_plan['details'])['total_parts'] != total_parts:
                raise ValueError('已开始的交付包不能静默修改总部分数')
            if origin['state'] == 'SENT_UI_CONFIRMED':
                raise ValueError('原任务已有核验交付，请核对现有记录，不重复登记')
            if existing:
                check = self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?', (existing['id'],))
                proof = json.loads(check['evidence']) if check else {}
                if (existing['body'] != body or existing['sent_at'] != sent or proof.get('actual_content') != content
                        or proof.get('attachments') != files or proof.get('total_parts') != total_parts
                        or proof.get('source_record') != _source_record):
                    raise ValueError('同一交付部分已有不同记录，请人工核对，不覆盖证据')
                recorded = dict(existing)
            else:
                if not old_plan:
                    flow._event('MANUAL_DELIVERY_PLAN', outbox=origin['id'], run=origin['run_id'], details={
                        'total_parts': total_parts, 'binding_id': origin['binding_id'], 'question_version': question_version,
                        'context_revision': context_revision, 'reviewer': reviewer, 'verification_evidence': verification_evidence})
                oid = new_id()
                self.db.execute('''INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
                    context_revision,review_status,idempotency_key,state,created_at,simulated)
                    VALUES(?,?,?,?,?,?,?,?,?,'HUMAN_VERIFIED',?,'PENDING',?,0)''',
                    (oid,origin['message_id'],origin['case_id'],origin['turn_id'],origin['binding_id'],origin['purpose'],
                     body,question_version,context_revision,key,now()))
                recorded = self.db.one('SELECT * FROM outbox WHERE id=?', (oid,))
                proof = {'verification_method': METHOD, 'confirmed': True, 'simulated': False,
                    'outbox_id': oid, 'original_outbox_id': origin['id'], 'binding_id': bound.binding_id,
                    'group_key': bound.group_key, 'student_key': bound.student_key, 'sender_role': 'TEACHER',
                    'body_hash': sha256(body.encode()).hexdigest(), 'confirmed_at': sent,
                    'reviewer': reviewer.strip(), 'verification_evidence': verification_evidence.strip(),
                    'actual_content': content, 'attachments': files, 'part_number': part_number, 'total_parts': total_parts,
                    'delivery_method': 'WECOM_MANUAL',
                    'content_form': 'TEXT_AND_ATTACHMENTS' if content.strip() and files else 'TEXT' if content.strip() else 'ATTACHMENTS',
                    'automatic_receipt': False, 'platform_message_id': None, 'recipient_read': None}
                if _source_record is not None:
                    proof['source_record'] = _source_record
                flow._record_check(recorded, proof, simulated=False, complete_turn=False)
                flow._event('MANUAL_DELIVERY_REGISTERED', outbox=oid, details={
                    'original_outbox_id': origin['id'], 'verification_method': METHOD, 'part_number': part_number,
                    'total_parts': total_parts, 'reviewer': reviewer.strip()})
                recorded = self.db.one('SELECT * FROM outbox WHERE id=?', (oid,))
            try:
                completed = validate_manual_package(self.db, recorded, proof)
            except ValueError:
                # Registration may be out of order; find the last actual delivery.
                parts = _part_rows(self.db, origin['id'])
                last = max(parts, key=lambda p: (timestamp(p['sent_at']), json.loads(p['evidence'])['part_number']))
                try:
                    completed = validate_manual_package(self.db, last, json.loads(last['evidence']))
                except ValueError:
                    completed = None
            if completed:
                self.db.execute("UPDATE outbox SET state='CANCELLED',last_error='MANUALLY_DELIVERED' WHERE id=?", (origin['id'],))
                self.db.execute("""UPDATE outbox SET state='CANCELLED',last_error='MANUALLY_DELIVERED'
                    WHERE turn_id=? AND purpose IN ('ANSWER','CORRECTION') AND state IN ('PENDING','STALE')""", (origin['turn_id'],))
                self.db.execute("UPDATE runs SET state='STALE',error='MANUALLY_DELIVERED',completed_at=? WHERE turn_id=? AND state='RUNNING'",
                                (now(),origin['turn_id']))
                self.db.execute("UPDATE human_tasks SET state='RESOLVED' WHERE message_id=? AND reason IN (?,?)",
                    (origin['message_id'],'SEND_UNKNOWN:'+origin['id'],'MANUAL_PARTIAL_DELIVERY:'+origin['id']))
                self.db.execute("UPDATE questions SET status='WAITING_FOLLOWUP' WHERE id=? AND current_version=? AND context_revision=?",
                    (turn['question_id'],question_version,context_revision))
            else:
                self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN',last_error='MANUAL_PARTIAL_DELIVERY' WHERE id=?", (origin['id'],))
                flow._human(origin['message_id'], 'MANUAL_PARTIAL_DELIVERY:' + origin['id'])
            current = self.db.one('SELECT * FROM questions WHERE id=?', (turn['question_id'],))
            if (current['current_version'],current['context_revision']) != (question_version,context_revision):
                flow._human(origin['message_id'], 'DELIVERED_OLD_VERSION_RECHECK:' + origin['id'])
        counting = ('COUNTING_PENDING' if _defer_counting else flow._project_verified_manual_counting(completed, original=origin)) if completed else 'PARTIAL_DELIVERY'
        return {'state': 'SENT_UI_CONFIRMED' if completed else 'PARTIAL_DELIVERY', 'recorded_outbox_id': recorded['id'],
            'completion_outbox_id': completed['id'] if completed else None, 'counting_status': counting,
            'verification_method': METHOD, 'automatic_receipt': False, 'replayed': existing is not None}

    def register_for_turn(self, turn_id, *, _defer_counting=False, **record):
        """Direct human reply: no generated answer or course upload is required."""
        from .workflow import Workflow
        with self.db.transaction():
            turn = self.db.one('SELECT * FROM turns WHERE id=?', (turn_id,))
            message = self.db.one('SELECT * FROM messages WHERE id=?', (turn['message_id'],)) if turn else None
            if (not turn or not message or not turn['question_version']
                    or message['source'].upper() in ('OPERATOR_TEST','MOCK')
                    or (turn['question_version'],turn['context_revision']) !=
                       (record.get('question_version'),record.get('context_revision'))):
                raise ValueError('请选择已归属并核对题目版本的真实学生任务')
            existing = self.db.all('''SELECT * FROM outbox WHERE turn_id=? AND purpose IN ('ANSWER','CORRECTION')
                AND simulated=0 AND idempotency_key NOT LIKE 'manual-delivery:%' ''', (turn_id,))
            if len(existing)>1:
                raise ValueError('该轮次有多个交付任务，请选择具体原答疑稿')
            if existing:
                origin = existing[0]
            else:
                oid = new_id()
                self.db.execute('''INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
                    context_revision,review_status,idempotency_key,state,created_at,simulated,last_error)
                    VALUES(?,?,?,?,?,'ANSWER','人工直接回复任务（未生成AI稿）',?,?,'HUMAN_VERIFIED',?,'SEND_UNKNOWN',?,0,'MANUAL_REGISTRATION_REQUIRED')''',
                    (oid,message['id'],turn['case_id'],turn['id'],message['binding_id'],turn['question_version'],
                     turn['context_revision'],'manual-task:'+turn['id'],now()))
                origin = self.db.one('SELECT * FROM outbox WHERE id=?', (oid,))
            result = self.register(origin['id'], **record, _defer_counting=True)
        return result if _defer_counting else self._count_completed(result)

    def list(self):
        result = []
        for row in self.db.all('''SELECT o.*,b.display_name,b.group_key,q.current_version,q.context_revision AS current_context,
            qv.payload FROM outbox o JOIN messages m ON m.id=o.message_id JOIN bindings b ON b.id=o.binding_id
            JOIN turns t ON t.id=o.turn_id JOIN questions q ON q.id=t.question_id
            JOIN question_versions qv ON qv.id=o.question_version
            WHERE o.purpose IN ('ANSWER','CORRECTION') AND o.simulated=0 AND upper(m.source) NOT IN ('OPERATOR_TEST','MOCK')
            AND o.idempotency_key NOT LIKE 'manual-delivery:%' ORDER BY o.rowid DESC LIMIT 100'''):
            plan = self.db.one("SELECT details FROM audit WHERE event='MANUAL_DELIVERY_PLAN' AND outbox_id=?", (row['id'],))
            parts = _part_rows(self.db, row['id'])
            completed = row['state'] == 'SENT_UI_CONFIRMED'
            if parts:
                try:
                    last = max(parts, key=lambda p: (timestamp(p['sent_at']), json.loads(p['evidence'])['part_number']))
                    validate_manual_package(self.db, last, json.loads(last['evidence']))
                    completed = True
                except (ValueError, KeyError, TypeError):
                    pass
            result.append({'outbox_id': row['id'], 'question_version': row['question_version'],
                'context_revision': row['context_revision'], 'label': row['group_key']+' · '+row['display_name']+' · 第'+json.loads(row['payload'])['number']+'题',
                'draft': row['body'], 'original_state': row['state'],
                'stale': (row['current_version'],row['current_context']) != (row['question_version'],row['context_revision']),
                'total_parts': json.loads(plan['details'])['total_parts'] if plan else None,
                'completed': completed,
                'parts': [{**json.loads(p['evidence']), 'state': p['state'], 'check_status': p['check_status']} for p in parts]})
        for row in self.db.all('''SELECT t.*,b.display_name,b.group_key FROM turns t JOIN messages m ON m.id=t.message_id
            JOIN bindings b ON b.id=m.binding_id WHERE t.question_version IS NOT NULL AND b.verified=1
            AND upper(m.source) NOT IN ('OPERATOR_TEST','MOCK')
            AND NOT EXISTS(SELECT 1 FROM outbox o WHERE o.turn_id=t.id AND o.purpose IN ('ANSWER','CORRECTION') AND o.simulated=0)
            ORDER BY t.rowid DESC LIMIT 100'''):
            question = self.db.one('SELECT * FROM questions WHERE id=?', (row['question_id'],))
            version = self.db.one('SELECT payload FROM question_versions WHERE id=?', (row['question_version'],))
            if not question or not version:
                continue
            result.append({'outbox_id': None, 'turn_id': row['id'], 'question_version': row['question_version'],
                'context_revision': row['context_revision'],
                'label': row['group_key']+' · '+row['display_name']+' · 第'+json.loads(version['payload'])['number']+'题 · 人工直接回复',
                'draft': '本轮尚未生成AI稿，可登记已实际完成的人工答复。',
                'stale': (question['current_version'],question['context_revision']) != (row['question_version'],row['context_revision']),
                'total_parts': None, 'parts': [], 'completed': False})
        return result
