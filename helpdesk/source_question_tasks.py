"""One clarity review of a trusted, already resolved original student question.

The resolver supplies intent/version before this gate. This module never infers
intent, changes source facts, creates another message/case or calls a desktop.
It reuses existing draft/task/queue storage instead of a parallel workflow.
"""
from hashlib import sha256
import json
from pathlib import Path

from .collector_dispatch import CollectorDispatcher
from .collector_storage import CollectorStore
from .domain import new_id
from .mcp_generation import input_fingerprint
from .mcp_preparation import question_text_fields
from .operator_tasks import OperatorTasks, _payload, _text
from .service import Helpdesk
from .storage import encode, now

LABEL = 'SOURCE_MESSAGE'
REVIEW_EVENT = 'SOURCE_QUESTION_INPUT_REVIEWED'
SEMANTIC_BOUND_EVENT = 'SOURCE_SEMANTIC_DECISION_BOUND'
SEMANTIC_EVIDENCE_PREFIX = 'shared-semantic-decision:'


def draft_source_info(store, draft_id):
    """Read-only display/preview projection, verified from the current source."""
    if not all(store.one("SELECT name FROM sqlite_master WHERE name=? AND type='table'", (name,))
               for name in ('operator_drafts', 'source_question_drafts')):
        raise ValueError('Original source draft required')
    draft = store.one('SELECT label FROM operator_drafts WHERE id=?', (draft_id,))
    link = store.one('SELECT * FROM source_question_drafts WHERE draft_id=?', (draft_id,))
    if not draft or draft[0] != LABEL or not link:
        raise ValueError('Original source draft required')
    origin = _read_origin(store, dict(link))
    binding = store.one('SELECT display_name FROM bindings WHERE id=?', (origin['message']['binding_id'],))
    return {'group_name': origin['source']['room_name'] or origin['source']['room_id'],
            'student_display_name': binding[0], 'student_sent_at': origin['message']['source_sent_at'],
            'image_count': len(origin['attachments'])}


def source_image(store, draft_id, index):
    """Only trusted original media; browser supplies an ID/index, never a path."""
    if type(index) is not int or index < 0:
        raise ValueError('Original image index required')
    draft_source_info(store, draft_id)
    link = store.one('SELECT * FROM source_question_drafts WHERE draft_id=?', (draft_id,))
    images = _read_origin(store, dict(link))['attachments']
    if index >= len(images):
        raise ValueError('Original image does not exist')
    image = images[index]
    path = Path(image['path'])
    types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    if path.suffix.lower() not in types or path.stat().st_size > 25_000_000:
        raise ValueError('Original question image type is unsupported')
    data = path.read_bytes()
    if sha256(data).hexdigest() != image['sha256']:
        raise ValueError('Original question image changed')
    return types[path.suffix.lower()], data


def canonical_attachments(items):
    """Project upload fields; preserve complete original media metadata in messages."""
    result = []
    for item in items:
        if not isinstance(item, dict) or not all(item.get(key) for key in ('path', 'sha256', 'provenance')):
            raise ValueError('Original question media is not ready')
        result.append({key: item[key] for key in ('path', 'sha256', 'provenance')})
    return result


def _read_origin_receipt(store, link):
    """Verify immutable transport without requiring the historical turn to be current."""
    path = Path(link['collector_path']).resolve(strict=True)
    collector = CollectorStore.__new__(CollectorStore)
    collector.path = str(path)
    filters = json.loads(link['sender_filters'])
    origin = CollectorDispatcher.read_verified_received(collector, store, link['collector_task_id'],
        self_sender_ids=filters['self_sender_ids'], teacher_sender_ids=filters['teacher_sender_ids'])
    source, message = origin['source'], origin['message']
    if message['id'] != link['message_id']:
        raise ValueError('Source review no longer identifies the original message')
    immutable = ('source_type', 'source_message_id', 'message_id', 'room_id', 'sender_id', 'message_type',
                 'raw_content', 'normalized_text', 'media_id', 'local_media_path', 'media_hash',
                 'reply_to_message_id', 'quoted_message_id', 'sent_at_raw', 'sent_at_utc', 'sent_at_local',
                 'ingested_at', 'raw_payload', 'business_timezone')
    proof = {'collector_path': str(path), 'collector_task_id': link['collector_task_id'],
             'message_id': message['id'], 'transport': CollectorDispatcher._receipt_transport(message),
             'source': {key: source[key] for key in immutable}, 'sender_filters': filters}
    digest = sha256(encode(proof).encode()).hexdigest()
    if link.get('source_sha256') and digest != link['source_sha256']:
        raise ValueError('Original source evidence changed after intake')
    origin['source_sha256'] = digest
    return origin


def _read_origin(store, link):
    origin = _read_origin_receipt(store, link)
    source, message = origin['source'], origin['message']
    filters = json.loads(link['sender_filters'])
    collector = CollectorStore.__new__(CollectorStore)
    collector.path = str(Path(link['collector_path']).resolve(strict=True))
    origin['attachments'] = canonical_attachments(json.loads(message['attachments']))
    if source['message_type'] == 'image' and not origin['attachments']:
        raise ValueError('Original question media is not ready')
    context = Helpdesk(store).context(origin['turn']['id'])
    current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (context['question_id'],))
    if tuple(current) != (origin['turn']['question_version'], origin['turn']['context_revision']):
        raise ValueError('Original question context is stale')
    case = store.one('SELECT binding_id FROM cases WHERE id=?', (context['case_id'],))
    if not case or case[0] != message['binding_id'] or context['student_words'] != message['raw_text']:
        raise ValueError('Source question context does not match the original student')
    origin['context'] = context
    if str(link.get('resolver_evidence', '')).startswith(SEMANTIC_EVIDENCE_PREFIX):
        from .semantic_decisions import SharedSemanticDecisions
        decision_id = link['resolver_evidence'][len(SEMANTIC_EVIDENCE_PREFIX):]
        shared = SharedSemanticDecisions(store, collector, **filters)
        answer = shared.answer_input(decision_id)
        bindings = [json.loads(row['details']) for row in store.all(
            'SELECT details FROM audit WHERE event=? AND turn_id=?', (SEMANTIC_BOUND_EVENT, origin['turn']['id']))
            if json.loads(row['details']).get('draft_id') == link['draft_id']]
        if (len(bindings) != 1 or any(bindings[0].get(key) != value for key, value in answer.items())
                or answer['message_id'] != message['id'] or answer['turn_id'] != origin['turn']['id']):
            raise ValueError('Shared semantic source binding changed or is missing')
        unit = store.one('SELECT * FROM performance_units WHERE id=?', (bindings[0].get('unit_id'),))
        linked = store.one('SELECT id FROM performance_links WHERE unit_id=? AND message_id=? AND question_id=?',
                           (bindings[0].get('unit_id'), message['id'], answer['question_id']))
        if (not unit or not linked or unit['binding_id'] != message['binding_id']
                or unit['material_id'] != answer['material_id'] or unit['question_type'] != answer['question_type']
                or unit['status'] in ('REVOKED', 'EXCLUDED')):
            raise ValueError('Shared semantic counting relation changed')
        origin['semantic_decision'] = {**answer, 'unit_id': unit['id']}
    return origin


def source_marker(task, link, origin):
    marker = {'label': LABEL, 'task_id': task['id'], 'draft_id': task['draft_id'],
            'draft_revision': task['draft_revision'], 'reviewer': task['reviewer'],
            'source_evidence': task['source_evidence'], 'formal_statistics_eligible': False,
            'original_sender_known': True, 'original_sent_at': origin['message']['source_sent_at'],
            'original_message_id': origin['message']['id'], 'collector_task_id': link['collector_task_id'],
            'source_sha256': link['source_sha256']}
    if origin.get('semantic_decision'):
        marker['semantic_decision_id'] = origin['semantic_decision']['decision_id']
        marker['counting_unit_id'] = origin['semantic_decision']['unit_id']
    return marker


def validate_reviewed_source_task(store, task_id):
    """Read-only verification used before freezing/preparing/executing a source task."""
    task = store.one('SELECT * FROM operator_tasks WHERE id=?', (task_id,))
    if not task or task['label'] != LABEL:
        raise ValueError('Reviewed original source task required')
    task = dict(task)
    draft = store.one('SELECT * FROM operator_drafts WHERE id=?', (task['draft_id'],))
    revision = store.one('SELECT * FROM operator_draft_revisions WHERE draft_id=? AND revision=?',
                         (task['draft_id'], task['draft_revision']))
    link = store.one('SELECT * FROM source_question_drafts WHERE draft_id=?', (task['draft_id'],))
    if (not draft or not revision or not link or draft['label'] != LABEL or draft['status'] != 'REVIEWED'
            or draft['revision'] != task['draft_revision']
            or sha256(revision['payload'].encode()).hexdigest() != revision['payload_sha256']):
        raise ValueError('Original clarity review revision changed')
    link = dict(link)
    origin = _read_origin(store, link)
    context = origin['context']
    if (task['message_id'] != origin['message']['id'] or task['binding_id'] != origin['message']['binding_id']
            or any(task[key] != context[key] for key in ('case_id', 'question_id', 'question_version', 'context_revision'))
            or task['turn_id'] != origin['turn']['id'] or task['input_fingerprint'] != input_fingerprint(context)):
        raise ValueError('Original clarity review context changed')
    payload = json.loads(revision['payload'])
    if origin.get('semantic_decision') and payload.get('question_type') != origin['semantic_decision']['question_type']:
        raise ValueError('Source question type differs from the shared semantic decision')
    if (question_text_fields(context) != {key: payload[key] for key in ('passage', 'stem', 'number', 'options')}
            or context['student_words'] != payload['request_text'] or payload['attachments'] != origin['attachments']
            or draft['intent'] != context['intent']):
        raise ValueError('Original reviewed source fields changed')
    _payload(payload, complete=True)
    audits = [dict(row) for row in store.all('SELECT * FROM audit WHERE event=? AND turn_id=?',
                                            (REVIEW_EVENT, task['turn_id']))
              if json.loads(row['details']).get('task_id') == task_id]
    marker = source_marker(task, link, origin)
    if len(audits) != 1 or json.loads(audits[0]['details']) != marker:
        raise ValueError('Original clarity review audit changed')
    if audits[0]['case_id'] != task['case_id'] or audits[0]['question_id'] != task['question_id']:
        raise ValueError('Original clarity review audit context changed')
    return task, dict(draft), dict(revision), audits[0], link, origin, marker


class SourceQuestionTasks(OperatorTasks):
    def __init__(self, store):
        super().__init__(store)
        with store.transaction():
            store.execute('''CREATE TABLE IF NOT EXISTS source_question_drafts (
                draft_id TEXT PRIMARY KEY REFERENCES operator_drafts(id),
                collector_task_id TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
                collector_path TEXT NOT NULL, sender_filters TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, resolver_evidence TEXT NOT NULL)''')

    def create_from_semantic(self, collector, decision_id, *, self_sender_ids=(), teacher_sender_ids=()):
        """Use one fixed decision for this source card and its counting candidate.

        No independent type/scope argument, second clarity gate or completion.
        Each durable step is idempotent if interrupted before source binding.
        """
        from .semantic_decisions import SharedSemanticDecisions
        shared = SharedSemanticDecisions(self.db, collector, self_sender_ids=self_sender_ids,
                                          teacher_sender_ids=teacher_sender_ids)
        answer = shared.answer_input(decision_id)
        unit_id = shared.counting_unit(decision_id)
        draft = self.create_from_received(collector, answer['collector_task_id'],
            question_type=answer['question_type'], resolver_evidence=SEMANTIC_EVIDENCE_PREFIX + decision_id,
            self_sender_ids=self_sender_ids, teacher_sender_ids=teacher_sender_ids)
        with collector.connect() as source_lock:
            source_lock.execute('BEGIN IMMEDIATE')
            with self.db.transaction():
                current = shared.answer_input(decision_id)
                if current != answer:
                    raise ValueError('Shared semantic decision changed during source intake')
                details = {**answer, 'draft_id': draft['id'], 'unit_id': unit_id}
                existing = [json.loads(row['details']) for row in self.db.all(
                    'SELECT details FROM audit WHERE event=? AND turn_id=?', (SEMANTIC_BOUND_EVENT, answer['turn_id']))
                    if json.loads(row['details']).get('draft_id') == draft['id']]
                if existing and existing != [details]:
                    raise ValueError('Source already has a different shared semantic binding')
                if not existing:
                    Helpdesk(self.db)._audit(SEMANTIC_BOUND_EVENT, case=answer['case_id'],
                        question=answer['question_id'], turn=answer['turn_id'], details=details)
        return self.get_draft(draft['id'])

    def create_from_received(self, collector, collector_task_id, *, question_type, resolver_evidence,
                             self_sender_ids=(), teacher_sender_ids=()):
        """Trusted backend intake only. UI cannot supply original identity or files.

        Question type/association come from the trusted resolver. This snapshots
        its existing version for one human clarity check, without running a model.
        """
        _text(resolver_evidence, 'resolver evidence', required=True)
        link = {'collector_task_id': collector_task_id, 'collector_path': str(Path(collector.path).resolve(strict=True)),
                'sender_filters': encode({'self_sender_ids': sorted(self_sender_ids),
                                          'teacher_sender_ids': sorted(teacher_sender_ids)})}
        receipt = self.db.one('SELECT business_message_id FROM collector_answer_tasks WHERE id=?', (collector_task_id,))
        if not receipt or not receipt[0]:
            raise ValueError('Resolved original message required')
        link['message_id'] = receipt[0]
        origin = _read_origin(self.db, link)
        context = origin['context']
        payload = _payload({**question_text_fields(context), 'question_type': question_type,
                            'request_text': context['student_words'], 'attachments': origin['attachments']}, complete=True)
        with self.db.transaction():
            old = self.db.one('SELECT * FROM source_question_drafts WHERE collector_task_id=?', (collector_task_id,))
            if old:
                draft = self.get_draft(old['draft_id'])
                if (old['source_sha256'] != origin['source_sha256'] or old['resolver_evidence'] != resolver_evidence
                        or draft['payload'] != payload):
                    raise ValueError('Original message already has a different source draft')
                return draft
            draft_id = new_id()
            self.db.execute('INSERT INTO operator_drafts VALUES(?,?,?,?,?,?,?,?,?)',
                (draft_id, LABEL, 'DRAFT', 1, context['intent'], None,
                 context['question_version'], context['context_revision'], now()))
            self._save_revision(draft_id, 1, payload)
            self.db.execute('INSERT INTO source_question_drafts VALUES(?,?,?,?,?,?,?)',
                (draft_id, collector_task_id, link['message_id'], link['collector_path'], link['sender_filters'],
                 origin['source_sha256'], resolver_evidence))
        return self.get_draft(draft_id)

    def create_draft(self, *args, **kwargs):
        raise ValueError('Use the trusted original-message intake')

    def revise(self, *args, **kwargs):
        raise ValueError('Source corrections use the existing question/version workflow')

    def review(self, draft_id, *, expected_revision, reviewer, source_evidence):
        """Only confirm clarity; preserve the resolver's case, version and original ACK."""
        reviewer = _text(reviewer, 'reviewer', required=True)
        source_evidence = _text(source_evidence, 'source evidence', required=True)
        with self.db.transaction():
            draft = self.get_draft(draft_id)
            if draft['label'] != LABEL or draft['revision'] != expected_revision:
                raise ValueError('Original source draft revision required')
            link = self.db.one('SELECT * FROM source_question_drafts WHERE draft_id=?', (draft_id,))
            if not link:
                raise ValueError('Original source draft provenance required')
            link = dict(link)
            origin = _read_origin(self.db, link)
            context = origin['context']
            if (draft['base_version'], draft['base_context_revision']) != (context['question_version'], context['context_revision']):
                raise ValueError('Source input changed; use the question/version workflow')
            payload = _payload(draft['payload'], complete=True)
            if origin.get('semantic_decision') and payload.get('question_type') != origin['semantic_decision']['question_type']:
                raise ValueError('Source question type differs from the shared semantic decision')
            if (question_text_fields(context) != {key: payload[key] for key in ('passage', 'stem', 'number', 'options')}
                    or payload['request_text'] != context['student_words'] or payload['attachments'] != origin['attachments']):
                raise ValueError('Original source fields changed before clarity review')
            old = self.db.one('SELECT id,reviewer,source_evidence FROM operator_tasks WHERE draft_id=?', (draft_id,))
            if old:
                if (old['reviewer'], old['source_evidence']) != (reviewer, source_evidence):
                    raise ValueError('Clarity review already recorded with different evidence')
                validate_reviewed_source_task(self.db, old['id'])
                return self.get_task(old['id'])
            if draft['status'] != 'DRAFT':
                raise ValueError('Original source draft is not awaiting clarity review')
            task_id = new_id()
            self.db.execute('''INSERT INTO operator_tasks(id,draft_id,draft_revision,label,reviewer,
                source_evidence,reviewed_at,binding_id,message_id,case_id,question_id,turn_id,
                question_version,context_revision,input_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (task_id, draft_id, expected_revision, LABEL, reviewer, source_evidence, now(), origin['message']['binding_id'],
                 origin['message']['id'], context['case_id'], context['question_id'], origin['turn']['id'],
                 context['question_version'], context['context_revision'], input_fingerprint(context)))
            self.db.execute("UPDATE operator_drafts SET status='REVIEWED' WHERE id=?", (draft_id,))
            task = self.get_task(task_id)
            Helpdesk(self.db)._audit(REVIEW_EVENT, case=task['case_id'], question=task['question_id'],
                turn=task['turn_id'], details=source_marker(task, link, origin))
        return self.get_task(task_id)

    def _freeze_marker(self, task, draft):
        *_, marker = validate_reviewed_source_task(self.db, task['id'])
        return 'source_clarity_review', marker
