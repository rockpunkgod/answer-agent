"""Local operator test intake. This module never opens a desktop or sends a message.

Drafts are source transcription, not student ingress. Only explicit review creates
an isolated local test binding and feeds the ordinary versioned question engine.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path

from .domain import Intent, Option, Question, new_id
from .mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from .service import Helpdesk, Incoming
from .storage import encode, now
from .teaching_bundle import TYPE_MODULES, verify_bundle
from .workflow import Workflow


class _NestedStore:
    """Use savepoints for existing engine transactions within our atomic review."""
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    @contextmanager
    def transaction(self):
        name = 'operator_' + new_id()
        self.store.execute('SAVEPOINT ' + name)
        try:
            yield
        except BaseException:
            self.store.execute('ROLLBACK TO ' + name)
            self.store.execute('RELEASE ' + name)
            raise
        else:
            self.store.execute('RELEASE ' + name)


class _NoDesktop:
    simulated = True


def _text(value, field, *, required=False):
    if not isinstance(value, str) or len(value) > 200_000:
        raise ValueError('Invalid ' + field)
    if required and not value.strip():
        raise ValueError('Required ' + field)
    return value


def _payload(payload, *, complete=False):
    if not isinstance(payload, dict):
        raise ValueError('Draft payload must be an object')
    allowed = {'passage', 'stem', 'number', 'options', 'question_type', 'request_text', 'attachments'}
    if set(payload) - allowed:
        raise ValueError('Unsupported draft fields; original sender/time cannot be invented')
    result = {key: _text(payload.get(key, ''), key, required=complete)
              for key in ('passage', 'stem', 'number', 'question_type')}
    if result['question_type'] and result['question_type'] not in TYPE_MODULES:
        raise ValueError('Unsupported question_type')
    result['request_text'] = _text(payload.get('request_text', ''), 'request_text')
    options = payload.get('options', {})
    if not isinstance(options, dict) or set(options) - set('ABCD'):
        raise ValueError('Options must be an A-D object')
    result['options'] = {label: _text(options.get(label, ''), 'option ' + label, required=complete)
                         for label in 'ABCD'}
    attachments = payload.get('attachments', [])
    if not isinstance(attachments, list) or len(attachments) > 20:
        raise ValueError('Invalid attachments')
    result['attachments'] = []
    for item in attachments:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'provenance'}:
            raise ValueError('Attachment requires local path, sha256 and provenance')
        path = Path(_text(item['path'], 'attachment path', required=True)).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 25_000_000:
            raise ValueError('Invalid local attachment')
        digest = sha256(path.read_bytes()).hexdigest()
        if item['sha256'] != digest:
            raise ValueError('Attachment hash mismatch')
        result['attachments'].append({'path': str(path), 'sha256': digest,
            'provenance': _text(item['provenance'], 'attachment provenance', required=True)})
    return result


class OperatorTasks:
    @classmethod
    def read_only(cls, store):
        """Read an installed task schema without constructor migrations or writes."""
        reader = cls.__new__(cls)
        reader.db = store
        return reader

    def __init__(self, store):
        self.db = store
        with store.transaction():
            store.execute('''CREATE TABLE IF NOT EXISTS operator_drafts (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, status TEXT NOT NULL,
                revision INTEGER NOT NULL, intent TEXT NOT NULL, parent_task_id TEXT,
                base_version TEXT, base_context_revision INTEGER, created_at TEXT NOT NULL)''')
            store.execute('''CREATE TABLE IF NOT EXISTS operator_draft_revisions (
                draft_id TEXT NOT NULL REFERENCES operator_drafts(id), revision INTEGER NOT NULL,
                payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(draft_id,revision))''')
            store.execute('''CREATE TABLE IF NOT EXISTS operator_draft_requests (
                request_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
                draft_id TEXT UNIQUE NOT NULL REFERENCES operator_drafts(id))''')
            store.execute('''CREATE TABLE IF NOT EXISTS operator_tasks (
                id TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL REFERENCES operator_drafts(id),
                draft_revision INTEGER NOT NULL, label TEXT NOT NULL, reviewer TEXT NOT NULL,
                source_evidence TEXT NOT NULL, reviewed_at TEXT NOT NULL, binding_id TEXT NOT NULL,
                message_id TEXT NOT NULL, case_id TEXT NOT NULL, question_id TEXT NOT NULL,
                turn_id TEXT NOT NULL, question_version TEXT NOT NULL, context_revision INTEGER NOT NULL,
                input_fingerprint TEXT NOT NULL, run_id TEXT, manifest_path TEXT, manifest_sha256 TEXT,
                preparation_path TEXT, evidence_dir TEXT)''')

    def get_draft(self, draft_id):
        row = self.db.one('SELECT * FROM operator_drafts WHERE id=?', (draft_id,))
        if not row:
            raise ValueError('Unknown operator draft')
        result = dict(row)
        result['revisions'] = [{**dict(r), 'payload': json.loads(r['payload'])} for r in self.db.all(
            'SELECT * FROM operator_draft_revisions WHERE draft_id=? ORDER BY revision', (draft_id,))]
        result['payload'] = result['revisions'][-1]['payload']
        task = self.db.one('SELECT id,run_id FROM operator_tasks WHERE draft_id=?', (draft_id,))
        result['task_id'] = task['id'] if task else None
        result['run_id'] = task['run_id'] if task else None
        return result

    def get_task(self, task_id):
        row = self.db.one('SELECT * FROM operator_tasks WHERE id=?', (task_id,))
        if not row:
            raise ValueError('Unknown reviewed operator test task')
        result = dict(row)
        result['task_id'] = result['id']
        result['revision'] = result['draft_revision']
        result['status'] = 'FROZEN' if result['run_id'] else 'REVIEWED'
        run = self.db.one('SELECT state FROM runs WHERE id=?', (result['run_id'],)) if result['run_id'] else None
        result['run_state'] = run['state'] if run else None
        result['generation_submitted_by_intake'] = False
        return result

    def list_drafts(self):
        return [self.get_draft(r['id']) for r in self.db.all('SELECT id FROM operator_drafts ORDER BY rowid DESC')]

    def list_tasks(self):
        return [self.get_task(r['id']) for r in self.db.all('SELECT id FROM operator_tasks ORDER BY rowid DESC')]

    def _save_revision(self, draft_id, revision, payload):
        raw = encode(payload)
        self.db.execute('INSERT INTO operator_draft_revisions VALUES(?,?,?,?,?)',
                        (draft_id, revision, raw, sha256(raw.encode()).hexdigest(), now()))

    def create_draft(self, payload, *, parent_task_id=None, intent='NEW', request_id=None):
        request_hash = sha256(encode({'payload': payload, 'parent_task_id': parent_task_id,
                                     'intent': str(intent)}).encode()).hexdigest()
        if request_id is not None:
            _text(request_id, 'request_id', required=True)
            if len(request_id) > 128:
                raise ValueError('Invalid request_id')
        payload = _payload(payload)
        intent = Intent(intent)
        if intent not in (Intent.NEW, Intent.FOLLOWUP, Intent.CORRECTION):
            raise ValueError('Only NEW, FOLLOWUP and CORRECTION are supported')
        if (intent == Intent.NEW) != (parent_task_id is None):
            raise ValueError('Followup/correction requires an explicit reviewed task reference')
        with self.db.transaction():
            if request_id is not None:
                previous = self.db.one('SELECT * FROM operator_draft_requests WHERE request_id=?', (request_id,))
                if previous:
                    if previous['request_sha256'] != request_hash:
                        raise ValueError('request_id content conflict')
                    return self.get_draft(previous['draft_id'])
            parent = self.get_task(parent_task_id) if parent_task_id else None
            if parent:
                context = Helpdesk(self.db).context(parent['turn_id'])
            draft_id = new_id()
            self.db.execute('INSERT INTO operator_drafts VALUES(?,?,?,?,?,?,?,?,?)',
                (draft_id, 'OPERATOR_TEST', 'DRAFT', 1, intent, parent_task_id,
                 context['question_version'] if parent else None,
                 context['context_revision'] if parent else None, now()))
            self._save_revision(draft_id, 1, payload)
            if request_id is not None:
                self.db.execute('INSERT INTO operator_draft_requests VALUES(?,?,?)',
                                (request_id, request_hash, draft_id))
        return self.get_draft(draft_id)

    def revise(self, draft_id, payload, *, expected_revision):
        payload = _payload(payload)
        with self.db.transaction():
            draft = self.get_draft(draft_id)
            if draft['label'] != 'OPERATOR_TEST':
                raise ValueError('Source corrections use the existing question/version workflow')
            if draft['status'] != 'DRAFT' or draft['revision'] != expected_revision:
                raise ValueError('Draft is reviewed or revision is stale')
            revision = expected_revision + 1
            self._save_revision(draft_id, revision, payload)
            self.db.execute('UPDATE operator_drafts SET revision=? WHERE id=?', (revision, draft_id))
        return self.get_draft(draft_id)

    def review(self, draft_id, *, expected_revision, reviewer, source_evidence):
        reviewer = _text(reviewer, 'reviewer', required=True)
        source_evidence = _text(source_evidence, 'source evidence', required=True)
        with self.db.transaction():
            draft = self.get_draft(draft_id)
            if draft['label'] != 'OPERATOR_TEST':
                raise ValueError('Source question reviews must preserve the original receipt')
            if draft['revision'] != expected_revision:
                raise ValueError('Draft revision is stale')
            existing = self.db.one('SELECT * FROM operator_tasks WHERE draft_id=?', (draft_id,))
            if existing:
                if (existing['reviewer'], existing['source_evidence']) != (reviewer, source_evidence):
                    raise ValueError('Review already recorded with different evidence')
                return self.get_task(existing['id'])
            payload = _payload(draft['payload'], complete=True)
            app = Helpdesk(_NestedStore(self.db))
            parent = self.get_task(draft['parent_task_id']) if draft['parent_task_id'] else None
            if parent:
                context = app.context(parent['turn_id'])
                if (context['question_version'], context['context_revision']) != (
                        draft['base_version'], draft['base_context_revision']):
                    raise ValueError('Referenced task input is stale; create a new draft')
                if draft['intent'] == Intent.FOLLOWUP and not payload['request_text'].strip():
                    raise ValueError('Followup requires request_text')
                if draft['intent'] == Intent.FOLLOWUP:
                    old = self.get_draft(parent['draft_id'])['payload']
                    if any(payload[k] != old[k] for k in ('passage', 'stem', 'number', 'options', 'question_type', 'attachments')):
                        raise ValueError('Followup cannot change reviewed question; use CORRECTION')
                binding = parent['binding_id']
            else:
                binding = app.bind('local-operator-test:' + draft_id, 'local-fixture:' + draft_id,
                                   'OPERATOR_TEST local fixture', verified=True)
            provenance = 'OPERATOR_TEST reviewed transcription: ' + source_evidence
            question = Question(payload['number'], payload['stem'], payload['stem'],
                tuple(Option.confirmed(label, payload['options'][label], index, provenance)
                      for index, label in enumerate('ABCD')), provenance)
            message_text = payload['request_text'].strip() or f"请讲解第{payload['number']}题。"
            outcome = app.ingest(Incoming(binding, message_text,
                intent=Intent(draft['intent']), source='OPERATOR_TEST', observation_id=draft_id,
                attachments=tuple(payload['attachments']), question_id=parent['question_id'] if parent else None,
                case_id=parent['case_id'] if parent else None, verified_question=question,
                raw_material=payload['passage'], verified_material=payload['passage']))
            if outcome.status != 'LINKED' or not outcome.turn_id:
                raise ValueError('Reviewed task could not be linked')
            if parent and draft['intent'] == Intent.CORRECTION:
                material_id = self.db.one('SELECT material_id FROM questions WHERE id=?', (outcome.question_id,))[0]
                old = app.context(outcome.turn_id)['student_material']
                if payload['passage'] != old:
                    app.correct_material(material_id, outcome.message_id, payload['passage'], payload['passage'])
                    current = self.db.one('SELECT * FROM questions WHERE id=?', (outcome.question_id,))
                    self.db.execute('UPDATE turns SET question_version=?,context_revision=? WHERE id=?',
                        (current['current_version'], current['context_revision'], outcome.turn_id))
            context = app.context(outcome.turn_id)
            task_id = new_id()
            self.db.execute('''INSERT INTO operator_tasks(id,draft_id,draft_revision,label,reviewer,
                source_evidence,reviewed_at,binding_id,message_id,case_id,question_id,turn_id,
                question_version,context_revision,input_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (task_id, draft_id, expected_revision, 'OPERATOR_TEST', reviewer, source_evidence, now(),
                 binding, outcome.message_id, outcome.case_id, outcome.question_id, outcome.turn_id,
                 context['question_version'], context['context_revision'], input_fingerprint(context)))
            self.db.execute("UPDATE operator_drafts SET status='REVIEWED' WHERE id=?", (draft_id,))
            app._audit('OPERATOR_TEST_INPUT_REVIEWED', case=outcome.case_id,
                question=outcome.question_id, turn=outcome.turn_id, details={
                    'task_id': task_id, 'draft_id': draft_id, 'draft_revision': expected_revision,
                    'reviewer': reviewer, 'source_evidence': source_evidence, 'label': 'OPERATOR_TEST',
                    'identity_rationale': 'Isolated local fixture identity only; no platform identity claimed',
                    'original_sender_known': False, 'original_sent_at': None,
                    'formal_statistics_eligible': False})
        return self.get_task(task_id)

    def _freeze_marker(self, task, draft):
        if task['label'] != 'OPERATOR_TEST' or draft['label'] != 'OPERATOR_TEST':
            raise ValueError('Source tasks require their original source review')
        return 'operator_test', {'label': 'OPERATOR_TEST', 'task_id': task['id'],
            'draft_id': task['draft_id'], 'draft_revision': task['draft_revision'],
            'reviewer': task['reviewer'], 'source_evidence': task['source_evidence'],
            'formal_statistics_eligible': False, 'original_sender_known': False, 'original_sent_at': None}

    def freeze(self, task_id, *, teaching_manifest, preparation_path, evidence_dir):
        """Persist a prepared-adapter run only; no generator/transport is invoked."""
        manifest_path = Path(teaching_manifest).resolve(strict=True)
        preparation_path = str(Path(preparation_path).resolve())
        evidence_dir = str(Path(evidence_dir).resolve())
        manifest = verify_bundle(manifest_path)
        digest = sha256(manifest_path.read_bytes()).hexdigest()
        with self.db.transaction():
            task = self.get_task(task_id)
            draft = self.get_draft(task['draft_id'])
            marker_key, marker = self._freeze_marker(task, draft)
            payload = _payload(draft['payload'], complete=True)
            if manifest['question_type'] != payload['question_type']:
                raise ValueError('Course manifest question type differs from reviewed input')
            app = Helpdesk(self.db)
            context = app.context(task['turn_id'])
            if input_fingerprint(context) != task['input_fingerprint']:
                raise ValueError('Reviewed task input is stale')
            if task['run_id']:
                if (task['manifest_path'], task['manifest_sha256'], task['preparation_path'], task['evidence_dir']) != (
                        str(manifest_path), digest, preparation_path, evidence_dir):
                    raise ValueError('Frozen run configuration changed')
                return task
            generator = PreparedDeepSeekGenerator(None, preparation_path, evidence_dir)
            workflow = Workflow(_NestedStore(self.db), desktop=_NoDesktop(),
                                generation_adapter=generator, teaching_manifest=manifest_path)
            run_id = workflow.start(task['turn_id'])
            run = self.db.one('SELECT input_json FROM runs WHERE id=?', (run_id,))
            snapshot = json.loads(run[0])
            snapshot[marker_key] = marker
            self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(snapshot), run_id))
            self.db.execute('''UPDATE operator_tasks SET run_id=?,manifest_path=?,manifest_sha256=?,
                preparation_path=?,evidence_dir=? WHERE id=?''',
                (run_id, str(manifest_path), digest, preparation_path, evidence_dir, task_id))
        return self.get_task(task_id)
