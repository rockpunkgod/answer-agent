"""Persistent scheduling after one committed operator source review.

Runs, owned chats, preparation evidence and outbox remain authoritative. This
queue stores only configuration and executor progress, never another question
source, answer or delivery record. Desktop actions require injected LUNA work.
"""
from hashlib import sha256
import json
from pathlib import Path

from .automatic_preparation import _source_review, complete_automatic_preparation
from . import automatic_preparation
from .domain import new_id
from .locking import resource_lock
from .mcp_preparation import question_text_fields
from .operator_tasks import OperatorTasks, _NoDesktop
from .session_isolation import claim_deepseek_chat
from .service import Helpdesk
from .storage import encode, now
from .test_answer_queue import validate_source_answer
from .workflow import Workflow


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _schema(store):
    with store.transaction():
        store.execute('''CREATE TABLE IF NOT EXISTS reviewed_question_queue (
            task_id TEXT PRIMARY KEY REFERENCES operator_tasks(id),
            run_id TEXT NOT NULL REFERENCES runs(id), candidate_path TEXT NOT NULL,
            phase TEXT NOT NULL, session_url TEXT, last_error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        store.execute('''CREATE TABLE IF NOT EXISTS reviewed_question_attempts (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES reviewed_question_queue(task_id),
            stage TEXT NOT NULL, executor TEXT NOT NULL, state TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT, reason TEXT,
            UNIQUE(task_id,stage))''')
        store.execute('''CREATE TABLE IF NOT EXISTS reviewed_question_admissions (
            task_id TEXT PRIMARY KEY REFERENCES operator_tasks(id),
            manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
            preparation_path TEXT NOT NULL, evidence_dir TEXT NOT NULL, candidate_path TEXT NOT NULL,
            phase TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def get(store, task_id):
    _require(store.one("SELECT name FROM sqlite_master WHERE name='reviewed_question_queue'") is not None,
             'QUEUED_REVIEWED_QUESTION_REQUIRED')
    row = store.one('SELECT * FROM reviewed_question_queue WHERE task_id=?', (task_id,))
    _require(row is not None, 'QUEUED_REVIEWED_QUESTION_REQUIRED')
    value = dict(row)
    value['attempts'] = [dict(a) for a in store.all(
        'SELECT * FROM reviewed_question_attempts WHERE task_id=? ORDER BY rowid', (task_id,))]
    value['formal_statistics_eligible'] = False
    value['desktop_executor'] = 'LUNA'
    value['source_review_required_again'] = False
    value['student_delivered'] = False  # This queue never records delivery.
    value['stored_phase'] = value['phase']
    run = store.one('SELECT state FROM runs WHERE id=?', (value['run_id'],))
    value['authoritative_run_state'] = run[0] if run else None
    if run is None or run[0] not in ('RUNNING', 'GENERATED'):
        value['phase'] = 'NEEDS_ATTENTION'
        value['last_error'] = 'AUTHORITATIVE_RUN_' + (run[0] if run else 'MISSING')
    elif run[0] == 'GENERATED':
        try:
            answer = _generated_outbox(store, value['run_id'])
        except Exception:
            value['phase'], value['last_error'] = 'NEEDS_ATTENTION', 'GENERATED_OUTBOX_INVALID'
        else:
            value['phase'], value['last_error'] = 'GENERATED', None
            value['outbox_id'], value['outbox_state'] = answer['id'], answer['state']
    elif value['phase'] == 'GENERATED':
        value['phase'], value['last_error'] = 'NEEDS_ATTENTION', 'QUEUE_PHASE_WITHOUT_GENERATED_RUN'
    return value


def list_queue(store):
    if not store.one("SELECT name FROM sqlite_master WHERE name='reviewed_question_queue'"):
        return []
    return [get(store, r[0]) for r in store.all('SELECT task_id FROM reviewed_question_queue ORDER BY rowid')]


def _admission_audit(store, task_id, event, details):
    task = store.one('SELECT case_id,question_id,turn_id FROM operator_tasks WHERE id=?', (task_id,))
    store.execute('''INSERT INTO audit(case_id,question_id,turn_id,event,details,created_at)
        VALUES(?,?,?,?,?,?)''', (*tuple(task), event, encode({'task_id': task_id, **details}), now()))


def _set_admission(store, task_id, phase, reason=None):
    old = store.one('SELECT phase,last_error FROM reviewed_question_admissions WHERE task_id=?', (task_id,))
    if old and (old[0], old[1]) == (phase, reason):
        return
    with store.transaction():
        store.execute('UPDATE reviewed_question_admissions SET phase=?,last_error=?,updated_at=? WHERE task_id=?',
                      (phase, reason, now(), task_id))
        _admission_audit(store, task_id, 'REVIEWED_QUESTION_ADMISSION_CHANGED', {'phase': phase, 'reason': reason})


def list_admissions(store):
    """Read scheduling intent and authoritative queue status; never retry work."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='reviewed_question_admissions'"):
        return []
    result = []
    for row in store.all('SELECT * FROM reviewed_question_admissions ORDER BY rowid'):
        value = dict(row)
        value.update(admission_phase=value['phase'], enqueued=False, run_id=None,
                     source_review_required_again=False, formal_statistics_eligible=False,
                     desktop_executor='LUNA', student_delivered=False)
        queued = store.one('SELECT task_id FROM reviewed_question_queue WHERE task_id=?', (row['task_id'],))
        if queued:
            value.update(get(store, row['task_id']), enqueued=True)
        else:
            task = store.one('SELECT run_id FROM operator_tasks WHERE id=?', (row['task_id'],))
            value['run_id'] = task[0] if task else None
        result.append(value)
    return result


def _validate_admission_materials(store, row):
    _, task, draft = _reviewed_task(store, row['task_id'])
    manifest = Path(row['manifest_path'])
    _require(sha256(manifest.read_bytes()).hexdigest() == row['manifest_sha256'], 'ADMISSION_MANIFEST_CHANGED')
    bundle = automatic_preparation.verify_bundle(manifest)
    _require(bundle['question_type'] == draft['payload']['question_type']
             and bundle['answer_generation_allowed_by_course'] is True, 'ADMISSION_COURSE_POLICY_CHANGED')
    for item in bundle['files']:
        _require(sha256(Path(item['snapshot_path']).read_bytes()).hexdigest() == item['snapshot_sha256'],
                 'ADMISSION_COURSE_FILES_CHANGED')
    if task.get('run_id'):
        _require(tuple(task.get(k) for k in ('manifest_path', 'manifest_sha256', 'preparation_path', 'evidence_dir')) ==
                 tuple(row[k] for k in ('manifest_path', 'manifest_sha256', 'preparation_path', 'evidence_dir')),
                 'ADMISSION_FROZEN_CONFIGURATION_CHANGED')
    return task


def _resume_admission(store, row):
    if row['phase'] in ('NEEDS_ATTENTION', 'ENQUEUED'):
        return
    try:
        task = _validate_admission_materials(store, row)
        existing_queue = store.one('SELECT task_id FROM reviewed_question_queue WHERE task_id=?', (row['task_id'],))
        if existing_queue:
            _set_admission(store, row['task_id'], 'ENQUEUED')
            return
        policy = Workflow(store, desktop=_NoDesktop())
        if policy._stopped():
            _set_admission(store, row['task_id'], 'STOPPED', 'STOPPED')
            return
        try:
            policy._require_confirmed_ack(task['turn_id'], require_real=True)
        except ValueError as exc:
            if str(exc) != 'ACK_REQUIRED':
                raise
            _set_admission(store, row['task_id'], 'WAITING_ACK', 'ACK_REQUIRED')
            return
        enqueue(store, row['task_id'], row['manifest_path'], preparation_path=row['preparation_path'],
                evidence_dir=row['evidence_dir'], candidate_path=row['candidate_path'])
        _set_admission(store, row['task_id'], 'ENQUEUED')
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc) in ('ACK_REQUIRED', 'STOPPED'):
            _set_admission(store, row['task_id'], 'WAITING_ACK' if str(exc) == 'ACK_REQUIRED' else 'STOPPED', str(exc))
        else:
            _set_admission(store, row['task_id'], 'NEEDS_ATTENTION', 'ADMISSION_VALIDATION_FAILED:' + type(exc).__name__)


def request_enqueue(store, task_id, manifest, *, preparation_path=None, evidence_dir=None, candidate_path=None):
    """Persist trusted scheduling intent, then admit if actual ACK/stop allow it.

    A WAITING_ACK task stays reviewed but unfrozen. Later resume_pending reuses
    that exact source review; it never generates ACK evidence or a new approval.
    Configuration must come from trusted backend state, never browser payload.
    """
    _require(not store.connection.in_transaction, 'COMMITTED_REVIEW_TRANSACTION_REQUIRED')
    with resource_lock(str(Path(store.path).resolve()) + '.reviewed-question-admissions.lock'):
        _, task, _ = _reviewed_task(store, task_id)
        prep, evidence, candidate = _paths(store, task, preparation_path, evidence_dir, candidate_path)
        manifest = Path(manifest).resolve(strict=True)
        digest = sha256(manifest.read_bytes()).hexdigest()
        _schema(store)
        config = (str(manifest), digest, str(prep), str(evidence), str(candidate))
        row = store.one('SELECT * FROM reviewed_question_admissions WHERE task_id=?', (task_id,))
        if row:
            _require(tuple(row[k] for k in ('manifest_path', 'manifest_sha256', 'preparation_path', 'evidence_dir', 'candidate_path')) == config,
                     'ADMISSION_CONFIGURATION_CHANGED')
        else:
            timestamp = now()
            with store.transaction():
                store.execute('''INSERT INTO reviewed_question_admissions
                    (task_id,manifest_path,manifest_sha256,preparation_path,evidence_dir,candidate_path,
                     phase,created_at,updated_at) VALUES(?,?,?,?,?,?,'READY_FOR_ENQUEUE',?,?)''',
                    (task_id, *config, timestamp, timestamp))
                _admission_audit(store, task_id, 'REVIEWED_QUESTION_ADMISSION_REQUESTED', {'phase': 'READY_FOR_ENQUEUE'})
            row = store.one('SELECT * FROM reviewed_question_admissions WHERE task_id=?', (task_id,))
        _resume_admission(store, row)
        return next(a for a in list_admissions(store) if a['task_id'] == task_id)


def resume_pending(store):
    """Trusted periodic, desktop-free admission retry; unchanged waits are quiet."""
    _require(not store.connection.in_transaction, 'COMMITTED_QUEUE_TRANSACTION_REQUIRED')
    if not store.one("SELECT name FROM sqlite_master WHERE name='reviewed_question_admissions'"):
        return []
    with resource_lock(str(Path(store.path).resolve()) + '.reviewed-question-admissions.lock'):
        for row in store.all("SELECT * FROM reviewed_question_admissions WHERE phase IN ('READY_FOR_ENQUEUE','WAITING_ACK','STOPPED') ORDER BY rowid"):
            _resume_admission(store, row)
        return list_admissions(store)


def _phase(store, task_id, phase, *, reason=None, session_url=None):
    with store.transaction():
        store.execute('''UPDATE reviewed_question_queue SET phase=?,last_error=?,updated_at=?,
            session_url=COALESCE(?,session_url) WHERE task_id=?''',
            (phase, reason, now(), session_url, task_id))


def _reviewed_task(store, task_id):
    row = store.one('SELECT label FROM operator_tasks WHERE id=?', (task_id,))
    if row and row[0] == 'SOURCE_MESSAGE':
        from .source_question_tasks import SourceQuestionTasks, validate_reviewed_source_task
        validate_reviewed_source_task(store, task_id)
        tasks = SourceQuestionTasks(store)
        task = tasks.get_task(task_id)
        return tasks, task, tasks.get_draft(task['draft_id'])
    tasks = OperatorTasks(store)
    task = tasks.get_task(task_id)
    draft = tasks.get_draft(task['draft_id'])
    _require(task['label'] == 'OPERATOR_TEST' and draft['label'] == 'OPERATOR_TEST'
             and draft['status'] == 'REVIEWED' and draft['revision'] == task['draft_revision'],
             'COMMITTED_INITIAL_REVIEW_REQUIRED')
    revision = draft['revisions'][-1]
    _require(revision['revision'] == task['draft_revision'] and sha256(store.one(
        'SELECT payload FROM operator_draft_revisions WHERE draft_id=? AND revision=?',
        (task['draft_id'], task['draft_revision']))[0].encode('utf-8')).hexdigest() == revision['payload_sha256'],
        'INITIAL_REVIEW_REVISION_CHANGED')
    audits = [json.loads(a[0]) for a in store.all(
        "SELECT details FROM audit WHERE turn_id=? AND event='OPERATOR_TEST_INPUT_REVIEWED'", (task['turn_id'],))]
    matching = [a for a in audits if a.get('task_id') == task_id]
    _require(len(matching) == 1 and all(matching[0].get(k) == task[k]
        for k in ('draft_id', 'draft_revision', 'reviewer', 'source_evidence')),
        'INITIAL_REVIEW_SOURCE_CHANGED')
    context = Helpdesk(store).context(task['turn_id'])
    _require(question_text_fields(context) == {k: draft['payload'][k] for k in ('passage', 'stem', 'number', 'options')}
             and context['attachments'] == draft['payload']['attachments'], 'INITIAL_REVIEW_FIELDS_CHANGED')
    for image in draft['payload']['attachments']:
        _require(sha256(Path(image['path']).read_bytes()).hexdigest() == image['sha256'], 'INITIAL_SOURCE_IMAGE_CHANGED')
    return tasks, task, draft


def _paths(store, task, preparation_path=None, evidence_dir=None, candidate_path=None):
    base = Path(store.path).resolve().parent / 'private' / 'reviewed-question-queue' / task['id']
    prep = Path(preparation_path or task.get('preparation_path') or base / 'prepared-session.json').resolve()
    evidence = Path(evidence_dir or task.get('evidence_dir') or base / 'generation').resolve()
    candidate = Path(candidate_path or prep.with_name('preparation-candidate.json')).resolve()
    _require(candidate != prep, 'CANDIDATE_AND_PREPARATION_MUST_DIFFER')
    return prep, evidence, candidate


def _generated_outbox(store, run_id):
    answer = store.one("SELECT * FROM outbox WHERE run_id=? AND purpose IN ('ANSWER','CORRECTION') ORDER BY rowid DESC LIMIT 1",
                       (run_id,))
    _require(answer is not None and answer['simulated'] == 0
             and answer['state'] not in ('STALE', 'CANCELLED', 'FAILED'), 'GENERATED_OUTBOX_REQUIRED')
    run = store.one('SELECT * FROM runs WHERE id=?', (run_id,))
    evidence = store.one('SELECT * FROM answer_evidence WHERE answer_id=?', (answer['answer_id'],))
    _require(run is not None and run['state'] == 'GENERATED' and evidence is not None
             and evidence['run_id'] == run_id and all(answer[k] == run[k]
                 for k in ('turn_id', 'question_version', 'context_revision')), 'GENERATED_OUTBOX_RUN_MISMATCH')
    validate_source_answer(store, answer, approval=False)
    return answer


def enqueue(store, task_id, manifest, *, preparation_path=None, evidence_dir=None, candidate_path=None):
    """Freeze an already reviewed task and queue it without desktop execution.

    Call after the initial review transaction commits. Repeated identical calls
    preserve the run and queue row. Existing frozen task paths are reused.
    """
    _require(not store.connection.in_transaction, 'COMMITTED_REVIEW_TRANSACTION_REQUIRED')
    with resource_lock(str(Path(store.path).resolve()) + '.reviewed-question-enqueue.lock'):
        tasks, task, _ = _reviewed_task(store, task_id)
        prep, evidence, candidate = _paths(store, task, preparation_path, evidence_dir, candidate_path)
        task = tasks.freeze(task_id, teaching_manifest=manifest, preparation_path=prep, evidence_dir=evidence)
        _schema(store)
        existing = store.one('SELECT * FROM reviewed_question_queue WHERE task_id=?', (task_id,))
        if existing:
            _require(existing['run_id'] == task['run_id'] and existing['candidate_path'] == str(candidate),
                     'QUEUED_QUESTION_CONFIGURATION_CHANGED')
            # Terminal runs are authoritative and are never frozen/generated again.
            run = store.one('SELECT state FROM runs WHERE id=?', (task['run_id'],))
            if run[0] == 'RUNNING':
                _source_review(store, task_id)
            return get(store, task_id)
        _source_review(store, task_id)
        timestamp = now()
        with store.transaction():
            store.execute('''INSERT INTO reviewed_question_queue
                (task_id,run_id,candidate_path,phase,created_at,updated_at) VALUES(?,?,?,?,?,?)''',
                (task_id, task['run_id'], str(candidate), 'WAITING_DESKTOP_EXECUTOR', timestamp, timestamp))
        return get(store, task_id)


def _attempt_finish(store, attempt_id, state, reason=None):
    with store.transaction():
        store.execute('''UPDATE reviewed_question_attempts SET state=?,completed_at=?,reason=?
            WHERE id=? AND state='STARTED' ''', (state, now(), reason, attempt_id))


def _authoritative_result(store, task_id, *, recover_attempts=True):
    queue = get(store, task_id)
    task = dict(store.one('SELECT * FROM operator_tasks WHERE id=?', (task_id,)))
    _require(task['run_id'] == queue['run_id'], 'QUEUE_RUN_CHANGED')
    run = store.one('SELECT * FROM runs WHERE id=?', (queue['run_id'],))
    _require(run is not None, 'QUEUE_RUN_MISSING')
    if run['state'] == 'GENERATED':
        _generated_outbox(store, run['id'])
        _phase(store, task_id, 'GENERATED')
        for attempt in queue['attempts']:
            if recover_attempts and attempt['stage'] == 'GENERATION' and attempt['state'] == 'STARTED':
                _attempt_finish(store, attempt['id'], 'RECOVERED_FROM_RUN')
        return None, None, 'GENERATED'
    if run['state'] != 'RUNNING':
        _phase(store, task_id, 'NEEDS_ATTENTION', reason='AUTHORITATIVE_RUN_' + run['state'])
        return None, None, 'NEEDS_ATTENTION'
    checked_task, snapshot, _ = _source_review(store, task_id)
    owned = store.one('SELECT session_url FROM deepseek_chats WHERE session_id=?', (run['session_id'],))
    if owned:
        _require(not queue['session_url'] or queue['session_url'] == owned[0], 'QUEUE_CHAT_CHANGED')
        claim_deepseek_chat(snapshot, owned[0], reserve=False)
        starts = store.all("SELECT details FROM audit WHERE run_id=? AND event='QUESTION_MATCH_ATTEMPT_STARTED'",
                           (snapshot['run_id'],))
        if starts and json.loads(starts[0][0]).get('session_url') is None:
            from .question_matching import _attempt
            _attempt(store, snapshot, json.loads(starts[0][0])['proof_path'], owned[0])
        _phase(store, task_id, 'READY_FOR_PREPARATION', session_url=owned[0])
    elif queue['session_url']:
        raise ValueError('QUEUE_CHAT_OWNERSHIP_MISSING')
    candidate_path = Path(queue['candidate_path'])
    preparation_path = Path(task['preparation_path'])
    if candidate_path.exists() or preparation_path.exists():
        # Pure source/evidence validation is safe to resume without any desktop
        # actor. It never reruns the uploader or submits another model request.
        preparation = complete_automatic_preparation(store, task_id, candidate_path)
        _require(preparation['status'] == 'ATTACHMENTS_READY_SOURCE_REVIEWED', 'FAST_PREPARATION_REQUIRED')
        _phase(store, task_id, 'ATTACHMENTS_READY', session_url=preparation['session_url'])
        for attempt in queue['attempts']:
            if attempt['stage'] == 'PREPARATION' and attempt['state'] == 'STARTED':
                _attempt_finish(store, attempt['id'], 'RECOVERED_FROM_PREPARATION')
        return checked_task, snapshot, 'GENERATION'
    if owned:
        for attempt in queue['attempts']:
            if attempt['stage'] == 'SESSION_CREATION' and attempt['state'] == 'STARTED':
                _attempt_finish(store, attempt['id'], 'RECOVERED_FROM_CHAT')
        return checked_task, snapshot, 'PREPARATION'
    return checked_task, snapshot, 'SESSION_CREATION'


def advance(store, task_id, *, executor=None, session_creator=None, preparer=None, generator=None):
    """Advance at most one desktop stage through an explicitly injected LUNA actor.

    All callbacks receive keyword arguments store, task, snapshot, attempt_id.
    session_creator returns an exact independently created DeepSeek chat URL.
    preparer also receives session_url and candidate_path, and must save FAST
    candidate evidence at that path. generator must persist the real result via
    the existing Workflow/run_existing machinery; its return value is not proof.

    STARTED is committed before a callback. Uncertain stages never replay. Only
    independently persisted chat/preparation/run evidence can recover progress.
    No callback is supplied or executed implicitly by this library.
    """
    _require(not store.connection.in_transaction, 'COMMITTED_QUEUE_TRANSACTION_REQUIRED')
    _require(executor in (None, 'LUNA'), 'ONLY_EXPLICIT_LUNA_EXECUTOR_ALLOWED')
    with resource_lock(str(Path(store.path).resolve()) + '.reviewed-question-executor.lock'):
        try:
            task, snapshot, stage = _authoritative_result(store, task_id)
        except Exception as exc:
            _phase(store, task_id, 'NEEDS_ATTENTION', reason='QUEUE_EVIDENCE_INVALID:' + type(exc).__name__)
            return get(store, task_id)
        if stage in ('GENERATED', 'NEEDS_ATTENTION'):
            return get(store, task_id)
        queue = get(store, task_id)
        prior = next((a for a in queue['attempts'] if a['stage'] == stage), None)
        if prior is not None:
            if prior['state'] == 'STARTED':
                _attempt_finish(store, prior['id'], 'UNCERTAIN', 'EXECUTOR_INTERRUPTED')
            _phase(store, task_id, 'EXECUTION_UNCERTAIN', reason=stage + '_NOT_REPLAYED')
            return get(store, task_id)
        callback = {'SESSION_CREATION': session_creator, 'PREPARATION': preparer, 'GENERATION': generator}[stage]
        stop = store.one("SELECT value FROM settings WHERE key='stop_requested'")
        if stop and stop[0] == 'true':
            _phase(store, task_id, queue['phase'], reason='STOPPED')
            return get(store, task_id)
        if executor != 'LUNA' or callback is None:
            phase = {'SESSION_CREATION': 'WAITING_DESKTOP_EXECUTOR', 'PREPARATION': 'READY_FOR_PREPARATION',
                     'GENERATION': 'ATTACHMENTS_READY'}[stage]
            _phase(store, task_id, phase, reason='LUNA_EXECUTOR_REQUIRED:' + stage)
            return get(store, task_id)
        _require(callable(callback), 'EXECUTOR_CALLBACK_MUST_BE_CALLABLE')
        attempt_id = new_id()
        with store.transaction():
            store.execute('''INSERT INTO reviewed_question_attempts
                (id,task_id,stage,executor,state,started_at) VALUES(?,?,?,?,?,?)''',
                (attempt_id, task_id, stage, executor, 'STARTED', now()))
            store.execute('''UPDATE reviewed_question_queue SET phase=?,last_error=NULL,updated_at=? WHERE task_id=?''',
                          (stage + '_STARTED', now(), task_id))
        arguments = dict(store=store, task=task, snapshot=snapshot, attempt_id=attempt_id)
        if stage == 'PREPARATION':
            arguments.update(session_url=queue['session_url'], candidate_path=Path(queue['candidate_path']))
        try:
            result = callback(**arguments)
            if stage == 'SESSION_CREATION':
                _require(isinstance(result, str), 'EXACT_CREATED_CHAT_URL_REQUIRED')
                # A blank webpage may acquire its real URL during the first
                # matching request. Recheck the now-persisted frozen context.
                _, current_snapshot, _ = _source_review(store, task_id)
                claim_deepseek_chat(current_snapshot, result)
                _phase(store, task_id, 'READY_FOR_PREPARATION', session_url=result)
            elif stage == 'PREPARATION':
                preparation = complete_automatic_preparation(store, task_id, queue['candidate_path'])
                _phase(store, task_id, 'ATTACHMENTS_READY', session_url=preparation['session_url'])
            else:
                _, _, authoritative_stage = _authoritative_result(store, task_id, recover_attempts=False)
                _require(authoritative_stage == 'GENERATED', 'GENERATOR_DID_NOT_PERSIST_VALID_ANSWER')
            _attempt_finish(store, attempt_id, 'SUCCEEDED')
        except Exception as exc:
            reason = stage + '_UNCERTAIN:' + type(exc).__name__
            _attempt_finish(store, attempt_id, 'UNCERTAIN', reason)
            _phase(store, task_id, 'EXECUTION_UNCERTAIN', reason=reason)
        return get(store, task_id)
