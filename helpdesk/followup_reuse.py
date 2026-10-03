"""Reuse verified material in one owned chat; preserve original proof bindings.

The existing run/audit records hold provenance. This module does not search,
upload, generate, send, rewrite a question, or create a completion ledger.
"""
from hashlib import sha256
import json
from pathlib import Path

from .storage import Store, encode, now


MATERIAL_EVENT = 'DEEPSEEK_SESSION_MATERIALS_VERIFIED'
REUSE_EVENT = 'QUESTION_MATCH_REUSED'
UPLOAD_EVENT = 'FOLLOWUP_PREPARATION_STARTED'


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _hash(value):
    return sha256(encode(value).encode()).hexdigest()


def remember_materials(snapshot, preparation_path, generation_path):
    """Called after preparation validation, before the second-stage submission."""
    if snapshot.get('followup_reuse'):
        return  # Every follow-up points directly to the original upload proof.
    from .question_matching import _binding, _current
    preparation_path = Path(preparation_path).resolve(strict=True)
    preparation = json.loads(preparation_path.read_text(encoding='utf-8'))
    details = {'binding': _binding(snapshot), 'session_url': preparation['session_url'],
               'preparation_path': str(preparation_path),
               'preparation_sha256': sha256(preparation_path.read_bytes()).hexdigest(),
               'generation_path': str(Path(generation_path).resolve())}
    store = Store(Path(snapshot['session_store_path']).resolve(strict=True))
    try:
        with store.transaction():
            _current(store, snapshot)
            prior = store.all('SELECT details FROM audit WHERE run_id=? AND event=?', (snapshot['run_id'], MATERIAL_EVENT))
            if prior:
                _require(len(prior) == 1 and json.loads(prior[0][0]) == details, 'SESSION_MATERIAL_RECORD_CHANGED')
            else:
                store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                              (snapshot['run_id'], MATERIAL_EVENT, encode(details), now()))
    finally:
        store.close()


def _origin(store, snapshot, link):
    from .mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
    from .question_matching import _binding, validate_receipt
    _require(snapshot.get('intent') == 'FOLLOWUP', 'REUSE_REQUIRES_ORDINARY_FOLLOWUP')
    row = store.one('SELECT * FROM runs WHERE id=?', (link.get('origin_run_id'),))
    _require(row is not None and row['state'] == 'GENERATED' and row['id'] != snapshot['run_id'],
             'REUSE_ORIGINAL_GENERATION_UNCONFIRMED')
    origin = json.loads(row['input_json'])
    _require(not origin.get('followup_reuse'), 'REUSE_MUST_REFERENCE_ORIGINAL_UPLOAD')
    keys = ('case_id', 'binding_id', 'question_id', 'question_version', 'session_id',
            'student_question', 'student_material', 'teaching_skills', 'generation_adapter', 'simulated')
    _require(all(snapshot.get(k) == origin.get(k) for k in keys)
             and snapshot.get('references', []) == origin.get('references', []), 'REUSE_QUESTION_SESSION_OR_COURSE_CHANGED')
    old_images = {a['sha256'] for a in origin['attachments']}
    _require(all(a['sha256'] in old_images for a in snapshot['attachments']), 'REUSE_NEW_IMAGE_REQUIRES_VERIFICATION')
    audits = store.all('SELECT details FROM audit WHERE run_id=? AND event=?', (row['id'], MATERIAL_EVENT))
    _require(len(audits) == 1, 'REUSE_UPLOAD_EVIDENCE_MISSING')
    materials = json.loads(audits[0][0])
    _require(_hash(materials) == link.get('materials_sha256') and materials.get('binding') == _binding(origin),
             'REUSE_UPLOAD_EVIDENCE_CHANGED')
    path = Path(materials['preparation_path'])
    _require(sha256(path.read_bytes()).hexdigest() == materials['preparation_sha256'], 'REUSE_PREPARATION_CHANGED')
    generator = PreparedDeepSeekGenerator(None, path, Path(materials['generation_path']).parent)
    preparation, page = generator._preparation(origin)
    _require(preparation.get('preparation_mode') == 'VERIFY_THEN_TEACH'
             and materials['session_url'] == page.url, 'REUSE_ORIGINAL_PREPARATION_REQUIRED')
    receipt = validate_receipt(store, origin, session_url=page.url)
    # A prepared but unsubmitted composer is not reusable session material.
    captured = json.loads(Path(materials['generation_path']).read_text(encoding='utf-8'))
    _require(captured.get('status') == 'FINAL_OUTPUT_CAPTURED' and captured.get('run_id') == row['id']
             and captured.get('session_url') == page.url
             and captured.get('input_fingerprint') == input_fingerprint(origin), 'REUSE_GENERATION_EVIDENCE_UNCONFIRMED')
    try:
        result = generator._capture_result(store, origin, preparation, page)
    except ValueError as exc:
        # Keep the existing business reason while retaining the detailed cause.
        raise ValueError('REUSE_GENERATED_ANSWER_EVIDENCE_CHANGED') from exc
    answer = store.one('''SELECT a.*,e.correct_option_id,e.complete,e.uploads_confirmed,e.simulated,e.session_id
        FROM answers a JOIN answer_evidence e ON e.answer_id=a.id WHERE e.run_id=?''', (row['id'],))
    option = next((o for o in origin['student_question']['options'] if o['id'] == result['correct_option_id']), None)
    _require(answer is not None and answer['state'] in ('GENERATED', 'STALE') and answer['simulated'] == 0
             and answer['complete'] == 1 and answer['uploads_confirmed'] == 1 and option is not None
             and answer['session_id'] == origin['session_id'] and answer['correct_option_id'] == option['id']
             and answer['text'] == result.get('text')
             and result.get('run_id') == row['id'] and result.get('session_id') == origin['session_id']
             and result.get('simulated') is False and result.get('complete') is True
             and result.get('uploads_confirmed') is True and result.get('adapter') == origin['generation_adapter']
             and result.get('correct_option_id') == answer['correct_option_id']
             and result.get('uploaded_teaching_hashes') == preparation['uploaded_teaching_hashes'],
             'REUSE_GENERATED_ANSWER_EVIDENCE_CHANGED')
    return origin, receipt


def _receipt(snapshot, origin, original_receipt):
    from .question_matching import _binding
    return {'binding': _binding(snapshot), 'kind': 'REUSED_MATCH', 'origin_run_id': origin['run_id'],
            'original_receipt_sha256': _hash(original_receipt), 'checked': original_receipt['checked'],
            'session_url': original_receipt['session_url']}


def prepare(store, snapshot):
    from .question_matching import _binding, _current, validate_input
    row = store.one('''SELECT a.run_id,a.details FROM audit a JOIN runs r ON r.id=a.run_id
        WHERE a.event=? AND r.question_id=? AND r.question_version=? AND r.session_id=? AND r.state='GENERATED'
        ORDER BY a.rowid DESC LIMIT 1''',
        (MATERIAL_EVENT, snapshot['question_id'], snapshot['question_version'], snapshot['session_id']))
    _require(row is not None, 'MATCH_FOLLOWUP_REUSE_REQUIRES_REVIEW')
    link = {'origin_run_id': row['run_id'], 'materials_sha256': _hash(json.loads(row['details']))}
    origin, original = _origin(store, snapshot, link)
    snapshot['followup_reuse'] = link
    snapshot['question_match_input'] = {**origin['question_match_input'], 'binding': _binding(snapshot),
                                        'search_status': 'REUSED_VERIFICATION'}
    snapshot['question_match_result'] = _receipt(snapshot, origin, original)
    with store.transaction():
        current = _current(store, snapshot)
        _require('question_match_input' not in json.loads(current['input_json']), 'MATCH_INPUT_ALREADY_FROZEN')
        validate_input(store, snapshot)
        store.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(snapshot), snapshot['run_id']))
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                      (snapshot['run_id'], REUSE_EVENT, encode({'link': link, 'receipt': snapshot['question_match_result']}), now()))
    record_reused_usage(store, snapshot)
    return snapshot


def record_reused_usage(store, snapshot):
    """Repair an interrupted accounting write after checking original proof."""
    from .call_costs import close_stage
    validate_receipt(store, snapshot)
    proof = 'verified-reuse:' + snapshot['followup_reuse']['materials_sha256']
    for kind in ('SEARCH', 'DEEPSEEK_MATCH'):
        close_stage(store, snapshot['run_id'], kind, proof)


def validate_receipt(store, snapshot):
    link = snapshot.get('followup_reuse')
    _require(isinstance(link, dict), 'REUSE_PROVENANCE_REQUIRED')
    origin, original = _origin(store, snapshot, link)
    expected = _receipt(snapshot, origin, original)
    _require(snapshot.get('question_match_result') == expected
             and snapshot['question_match_input']['candidates'] == origin['question_match_input']['candidates'],
             'REUSE_MATCH_RESULT_CHANGED')
    audits = store.all('SELECT details FROM audit WHERE run_id=? AND event=?', (snapshot['run_id'], REUSE_EVENT))
    _require(len(audits) == 1 and json.loads(audits[0][0]) == {'link': link, 'receipt': expected},
             'REUSE_AUDIT_MISSING_OR_CHANGED')
    return expected


def begin_upload(store, snapshot, evidence_path, session_url):
    """Commit before uploading the current context; unknown uploads never replay."""
    from .question_matching import _binding, _current
    with store.transaction():
        _current(store, snapshot)
        receipt = validate_receipt(store, snapshot)
        _require(receipt['session_url'] == session_url, 'REUSE_WEB_SESSION_CHANGED')
        _require(not store.one('SELECT 1 FROM audit WHERE run_id=? AND event=?', (snapshot['run_id'], UPLOAD_EVENT)),
                 'FOLLOWUP_UPLOAD_REQUIRES_REVIEW')
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
            (snapshot['run_id'], UPLOAD_EVENT, encode({'binding': _binding(snapshot), 'session_url': session_url,
                'preparation_path': str(Path(evidence_path).resolve())}), now()))


def validate_upload(snapshot, evidence_path, session_url):
    from .question_matching import _binding
    store = Store(Path(snapshot['session_store_path']).resolve(strict=True))
    try:
        records = store.all('SELECT details FROM audit WHERE run_id=? AND event=?', (snapshot['run_id'], UPLOAD_EVENT))
        expected = {'binding': _binding(snapshot), 'session_url': session_url,
                    'preparation_path': str(Path(evidence_path).resolve())}
        _require(len(records) == 1 and json.loads(records[0][0]) == expected, 'FOLLOWUP_UPLOAD_EVIDENCE_CHANGED')
    finally:
        store.close()
