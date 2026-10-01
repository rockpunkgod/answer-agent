"""Reuse the recorded input review for fast preparation, without a desktop.

This is a projection of an existing source clarity review, never a new
human approval. It neither generates an answer nor authorizes student delivery.
"""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from .locking import resource_lock
from .mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from .mcp_preparation import question_text_fields
from .mcp_preparation_review import TEXT_REVIEW_STATEMENT, review_preparation
from .service import Helpdesk
from .teaching_bundle import verify_bundle


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _source_review(store, task_id):
    """Read and cross-check the original task, immutable revision and frozen run."""
    task = store.one('SELECT * FROM operator_tasks WHERE id=?', (task_id,))
    _require(task is not None and task['label'] in ('OPERATOR_TEST', 'SOURCE_MESSAGE'), 'REVIEWED_OPERATOR_TASK_REQUIRED')
    task = dict(task)
    source = task['label'] == 'SOURCE_MESSAGE'
    if source:
        from .source_question_tasks import validate_reviewed_source_task, canonical_attachments
        _, _, _, _, _, origin, expected_marker = validate_reviewed_source_task(store, task_id)
    draft = store.one('SELECT * FROM operator_drafts WHERE id=?', (task['draft_id'],))
    revision = store.one('SELECT * FROM operator_draft_revisions WHERE draft_id=? AND revision=?',
                         (task['draft_id'], task['draft_revision']))
    _require(draft is not None and revision is not None and draft['label'] == task['label']
             and draft['status'] == 'REVIEWED' and draft['revision'] == task['draft_revision'],
             'INITIAL_INPUT_REVIEW_REQUIRED')
    _require(sha256(revision['payload'].encode('utf-8')).hexdigest() == revision['payload_sha256'],
             'REVIEWED_SOURCE_REVISION_CHANGED')
    payload = json.loads(revision['payload'])
    run = store.one('SELECT * FROM runs WHERE id=?', (task['run_id'],))
    _require(run is not None and run['state'] == 'RUNNING', 'RUNNING_FROZEN_RUN_REQUIRED')
    snapshot = json.loads(run['input_json'])
    marker = snapshot.get('source_clarity_review' if source else 'operator_test', {})
    _require(all(isinstance(task[k], str) and task[k].strip()
                 for k in ('reviewer', 'source_evidence', 'reviewed_at')), 'INITIAL_REVIEW_SOURCE_MISSING')
    reviewed_at = datetime.fromisoformat(task['reviewed_at'])
    _require(reviewed_at.tzinfo is not None, 'INITIAL_REVIEW_TIME_INVALID')
    if not source:
        expected_marker = {'label': 'OPERATOR_TEST', 'task_id': task['id'],
                        'draft_id': task['draft_id'], 'draft_revision': task['draft_revision'],
                        'reviewer': task['reviewer'], 'source_evidence': task['source_evidence'],
                        'formal_statistics_eligible': False, 'original_sender_known': False,
                        'original_sent_at': None}
    _require(marker == expected_marker, 'FROZEN_SOURCE_REVIEW_CHANGED')
    audits = store.all("SELECT * FROM audit WHERE event=? AND turn_id=?",
                       ('SOURCE_QUESTION_INPUT_REVIEWED' if source else 'OPERATOR_TEST_INPUT_REVIEWED', task['turn_id']))
    matching = [a for a in audits if json.loads(a['details']).get('task_id') == task_id]
    _require(len(matching) == 1, 'INITIAL_REVIEW_AUDIT_REQUIRED')
    audit = matching[0]
    details = json.loads(audit['details'])
    _require(all(details.get(k) == task[k] for k in
                 ('draft_id', 'draft_revision', 'reviewer', 'source_evidence'))
             and details.get('task_id') == task['id']
             and details.get('label') == task['label']
             and details.get('formal_statistics_eligible') is False
             and details.get('original_sender_known') is source
             and details.get('original_sent_at') == expected_marker['original_sent_at']
             and audit['question_id'] == task['question_id'] and audit['case_id'] == task['case_id'],
             'INITIAL_REVIEW_AUDIT_CHANGED')
    _require(datetime.fromisoformat(draft['created_at']) <= reviewed_at
             <= datetime.fromisoformat(audit['created_at']), 'INITIAL_REVIEW_TIME_CHANGED')
    for key in ('turn_id', 'question_id', 'question_version', 'context_revision'):
        _require(run[key] == task[key], 'FROZEN_RUN_TASK_MISMATCH')
    _require(snapshot.get('run_id') == run['id'] and snapshot.get('session_id') == run['session_id']
             and snapshot.get('binding_id') == task['binding_id']
             and snapshot.get('case_id') == task['case_id']
             and snapshot.get('question_id') == task['question_id']
             and snapshot.get('question_version') == task['question_version']
             and snapshot.get('context_revision') == task['context_revision']
             and snapshot.get('generation_adapter') == PreparedDeepSeekGenerator.identity
             and snapshot.get('simulated') is False
             and Path(snapshot.get('session_store_path', '')).resolve() == Path(store.path).resolve(),
             'FROZEN_RUN_TASK_MISMATCH')
    message = store.one('SELECT * FROM messages WHERE id=?', (task['message_id'],))
    binding = store.one('SELECT * FROM bindings WHERE id=?', (task['binding_id'],))
    if source:
        _require(message is not None and message['source'].startswith('collector:')
                 and message['binding_id'] == task['binding_id'] and binding is not None
                 and binding['verified'] == 1, 'ORIGINAL_SOURCE_BINDING_CHANGED')
    else:
        _require(message is not None and message['source'] == 'OPERATOR_TEST'
                 and message['binding_id'] == task['binding_id'] and binding is not None
                 and binding['verified'] == 1 and binding['group_key'].startswith('local-operator-test:')
                 and binding['student_key'].startswith('local-fixture:'), 'OPERATOR_SOURCE_BINDING_CHANGED')
    context = Helpdesk(store).context(task['turn_id'])
    _require(input_fingerprint(snapshot) == task['input_fingerprint'] == input_fingerprint(context),
             'REVIEWED_INPUT_CHANGED')
    expected = {key: payload[key] for key in ('passage', 'stem', 'number', 'options')}
    snapshot_images = canonical_attachments(snapshot['attachments']) if source else snapshot['attachments']
    _require(question_text_fields(snapshot) == expected
             and payload['attachments'] == snapshot_images,
             'REVIEWED_SOURCE_FIELDS_CHANGED')
    _require(draft['intent'] == snapshot['intent'], 'REVIEWED_SOURCE_INTENT_CHANGED')
    expected_words = payload['request_text'] if source else (payload['request_text'].strip() or f"请讲解第{payload['number']}题。")
    _require(snapshot['student_words'] == expected_words, 'REVIEWED_SOURCE_REQUEST_CHANGED')
    images = {}
    for image in payload['attachments']:
        _require(isinstance(image.get('provenance'), str) and image['provenance'].strip()
                 and _digest(image['path']) == image['sha256'], 'REVIEWED_SOURCE_IMAGE_CHANGED')
        images[image['path']] = image['sha256']
    manifest_path = Path(task['manifest_path']).resolve(strict=True)
    _require(_digest(manifest_path) == task['manifest_sha256'], 'FROZEN_MANIFEST_CHANGED')
    manifest = verify_bundle(manifest_path)
    _require(manifest['question_type'] == payload['question_type']
             and manifest['answer_generation_allowed_by_course'] is True, 'FROZEN_COURSE_POLICY_CHANGED')
    allowed = {entry['snapshot_path']: entry['snapshot_sha256'] for entry in manifest['files']}
    _require(set(manifest['workflow_teaching_paths']) == {s['path'] for s in snapshot['teaching_skills']},
             'FROZEN_TEACHING_FILES_CHANGED')
    excerpts = {}
    for skill in snapshot['teaching_skills']:
        _require(skill.get('source') == 'verified_teaching_manifest'
                 and allowed.get(skill['path']) == skill['sha256'] == _digest(skill['path'])
                 and skill.get('content') == Path(skill['path']).read_bytes().decode('utf-8'),
                 'FROZEN_TEACHING_SOURCE_CHANGED')
        content = Path(skill['path']).read_text(encoding='utf-8')
        excerpt = next((line.strip() for line in content.splitlines() if len(line.strip()) >= 10), None)
        _require(excerpt is not None, 'TEACHING_SOURCE_EXCERPT_MISSING')
        excerpts[skill['path']] = excerpt
    review = dict(review_origin='INITIAL_SOURCE_CLARITY_REVIEW' if source else 'INITIAL_OPERATOR_INPUT_REVIEW',
        review_record_kind='source_question_review', new_human_review=False,
        task_id=task['id'], draft_id=task['draft_id'], draft_revision=task['draft_revision'],
        draft_payload_sha256=revision['payload_sha256'], reviewer=task['reviewer'],
        reviewed_at=task['reviewed_at'], source_review_evidence=task['source_evidence'],
        source_evidence=task['source_evidence'], initial_review_audit_id=audit['id'],
        run_id=run['id'], session_id=run['session_id'], input_fingerprint=task['input_fingerprint'],
        source_verified_excerpts=excerpts, verified_question_stem=payload['stem'],
        formal_statistics_eligible=False, original_sender_known=source,
        original_sent_at=expected_marker['original_sent_at'],
        course_excerpt_origin='PROGRAM_VALIDATED_FROZEN_COURSE',
        reviewed_attachments=payload['attachments'])
    if images:
        review.update(reviewed_image_hashes=images,
            image_review_statement=('I inspected both frozen question images and verified the question stem.'
                if len(images) == 2 else 'I inspected all frozen question images and verified the question stem.'))
    else:
        review.update(reviewed_question_text=expected, reviewed_input_fingerprint=task['input_fingerprint'],
                      question_text_review_statement=TEXT_REVIEW_STATEMENT)
    return task, snapshot, review


def complete_automatic_preparation(store, task_id, candidate_path, *, source_review_path=None,
                                   readiness_path=None):
    """Validate a FAST candidate using the one recorded source review.

    Output is the task's frozen preparation_path. Optional evidence paths must
    be distinct, new files. An unchanged completed call returns the existing
    preparation without a second review. Partial/changed evidence fails closed.
    No desktop, generation, delivery, or new human-review record is involved.
    """
    task_row = store.one('SELECT preparation_path FROM operator_tasks WHERE id=?', (task_id,))
    _require(task_row is not None and task_row[0], 'FROZEN_PREPARATION_PATH_REQUIRED')
    output = Path(task_row[0]).resolve()
    source_path = Path(source_review_path).resolve() if source_review_path else output.with_name('source-question-review.json')
    ready_path = Path(readiness_path).resolve() if readiness_path else output.with_name('readiness.json')
    candidate_path = Path(candidate_path).resolve(strict=True)
    _require(len({output, source_path, ready_path, candidate_path}) == 4, 'PREPARATION_PATHS_MUST_BE_DISTINCT')
    with resource_lock(str(output) + '.automatic.lock'):
        task, snapshot, review = _source_review(store, task_id)
        _require(Path(task['preparation_path']).resolve() == output, 'FROZEN_PREPARATION_PATH_CHANGED')
        candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
        _require(candidate.get('preparation_mode') == 'FAST_UPLOAD_THEN_GENERATE'
                 and candidate.get('status') == 'ATTACHMENTS_READY_REQUIRES_SOURCE_REVIEW',
                 'FAST_UPLOAD_CANDIDATE_REQUIRED')
        staged_paths = [e.get('arguments', {}).get('text') for e in candidate.get('events', [])
                        if e.get('intent') in ('stage frozen file path', 'stage frozen path in reviewed picker')]
        files = candidate.get('files', [])
        _require(candidate.get('effective_material_order') == 'COURSE_THEN_QUESTION'
                 and staged_paths == [x['path'] for x in files if x['kind'] == 'course']
                                   + [x['path'] for x in files if x['kind'] != 'course']
                 and not any(e.get('intent') == 'submit readback request once'
                             for e in candidate.get('events', [])), 'FAST_COURSE_THEN_QUESTION_REQUIRED')
        generator = PreparedDeepSeekGenerator(None, output, task['evidence_dir'])
        if output.exists():
            _require(source_path.is_file() and ready_path.is_file()
                     and json.loads(source_path.read_text(encoding='utf-8')) == review,
                     'AUTOMATIC_SOURCE_REVIEW_CHANGED')
            preparation, _ = generator._preparation(snapshot)
            _require(preparation.get('candidate_evidence') == str(candidate_path)
                     and preparation.get('operator_review_evidence') == str(source_path)
                     and preparation.get('readiness_evidence') == str(ready_path),
                     'AUTOMATIC_PREPARATION_PATH_CHANGED')
            return preparation
        _require(not source_path.exists() and not ready_path.exists(), 'PARTIAL_AUTOMATIC_PREPARATION_EXISTS')
        created = []
        try:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            with source_path.open('x', encoding='utf-8') as stream:
                created.append(source_path)
                json.dump(review, stream, ensure_ascii=False, indent=2)
            # Existing review logic checks the complete upload journal, source
            # hashes, exact page URL and permanent student/question ownership.
            preparation = review_preparation(snapshot, candidate_path, source_path, output, ready_path)
            created.extend([ready_path, output])
            _require(_source_review(store, task_id) == (task, snapshot, review), 'SOURCE_CHANGED_DURING_PREPARATION')
            generator._preparation(snapshot)
            return preparation
        except Exception:
            # These paths were proven absent while owning the per-output lock.
            # A failed projection must not publish a usable authorization.
            for path in (output, ready_path, *created):
                if path.exists():
                    path.unlink()
            raise
