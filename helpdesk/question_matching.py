"""First DeepSeek stage: frozen Top2 evidence and program-checked relations.

Uses the existing run and audit records. Candidates remain references; this
module never changes student versions, confirms delivery or awards performance.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from .domain import Question, compare
from .mcp_generation import input_fingerprint
from .reference_resolution import candidate_view, content_fingerprint
from .service import Helpdesk
from .storage import encode, now


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _binding(snapshot):
    return {**{k: snapshot[k] for k in ('run_id', 'case_id', 'question_id', 'question_version',
                                      'context_revision', 'session_id', 'binding_id')},
            'input_fingerprint': input_fingerprint(snapshot)}


def _current(store, snapshot):
    row = store.one('SELECT * FROM runs WHERE id=?', (snapshot['run_id'],))
    _require(row is not None and row['state'] == 'RUNNING', 'MATCH_RUN_NOT_ACTIVE')
    _require(input_fingerprint(Helpdesk(store).context(row['turn_id'])) == input_fingerprint(snapshot),
             'MATCH_STUDENT_VERSION_CHANGED')
    _require(_binding(json.loads(row['input_json'])) == _binding(snapshot), 'MATCH_RUN_BINDING_CHANGED')
    stop = store.one("SELECT value FROM settings WHERE key='stop_requested'")
    _require(not stop or stop[0] != 'true', 'STOPPED')
    return row


def _candidate(store, snapshot, candidate_id):
    row = store.one('SELECT * FROM reference_candidates WHERE id=?', (candidate_id,))
    _require(row is not None and row['student_version'] == snapshot['question_version'], 'MATCH_CANDIDATE_NOT_FOUND')
    view = candidate_view(row)
    _require(view['question_id'] == snapshot['question_id']
             and view['source_policy'].get('business_record_storage_allowed') is True,
             'MATCH_CANDIDATE_NOT_AVAILABLE')
    # Explicit allowlist excludes third-party answers, explanations and scores.
    # A rejected candidate is comparison evidence only. Keeping its state lets
    # DeepSeek see the actual conflicting version without allowing its reuse.
    return {k: view[k] for k in ('candidate_id', 'source', 'source_url', 'retrieved_at', 'content_hash',
                                'reference_question', 'reference_material', 'state')}


def validate_input(store, snapshot):
    item = snapshot.get('question_match_input')
    _require(isinstance(item, dict) and item.get('binding') == _binding(snapshot), 'MATCH_INPUT_MISSING_OR_STALE')
    candidates = item.get('candidates')
    _require(isinstance(candidates, list) and len(candidates) <= 2, 'MATCH_TOP2_REQUIRED')
    identities, contents = set(), set()
    for candidate in candidates:
        _require(isinstance(candidate, dict) and isinstance(candidate.get('candidate_id'), str), 'MATCH_REAL_CANDIDATE_ID_REQUIRED')
        actual = _candidate(store, snapshot, candidate['candidate_id'])
        _require(encode(actual) == encode(candidate), 'MATCH_CANDIDATE_CONTENT_CHANGED')
        fingerprint = content_fingerprint(Question.from_dict(actual['reference_question']), actual['reference_material'])
        _require(actual['candidate_id'] not in identities and fingerprint not in contents, 'MATCH_DUPLICATE_CANDIDATE')
        identities.add(actual['candidate_id'])
        contents.add(fingerprint)
    return item


def prepare_input(store, run_id, lookup, **lookup_args):
    """Run one existing bounded search and pin real Top2 IDs on the frozen run.

    An interrupted/disabled/unavailable search is exposed, never called no hit.
    Repeated calls reuse the same frozen input and do not search again.
    """
    _require(not store.connection.in_transaction, 'MATCH_REQUIRES_COMMITTED_RUN')
    row = store.one('SELECT input_json FROM runs WHERE id=?', (run_id,))
    _require(row is not None, 'MATCH_RUN_NOT_FOUND')
    snapshot = json.loads(row[0])
    _current(store, snapshot)
    if 'question_match_input' in snapshot:
        validate_input(store, snapshot)
        if snapshot.get('followup_reuse'):
            validate_receipt(store, snapshot)
        return snapshot
    if snapshot.get('intent') == 'FOLLOWUP':
        from .followup_reuse import prepare
        return prepare(store, snapshot)
    trigger = 'version_difference' if snapshot.get('intent') in ('CORRECTION', 'DISPUTE') else 'initial_question'
    report = lookup.run_for_question(store, snapshot['question_id'], snapshot['question_version'],
                                     snapshot['context_revision'], trigger=trigger, **lookup_args)
    _require(report.get('retrieval_status') in ('CANDIDATES_FOUND', 'NO_RESULTS', 'OFFLINE_FIXTURE')
             and not report.get('stale') and not report.get('evidence_expired_or_unavailable'),
             'MATCH_SEARCH_UNAVAILABLE:' + str(report.get('retrieval_status')))
    _require(snapshot.get('simulated') is True or report['retrieval_status'] != 'OFFLINE_FIXTURE',
             'MATCH_FIXTURE_CANNOT_BE_REAL_EVIDENCE')
    candidates = []
    for item in report.get('top_candidates', []):
        _require(item.get('candidate_id'), 'MATCH_REAL_CANDIDATE_ID_REQUIRED')
        candidates.append(_candidate(store, snapshot, item['candidate_id']))
    snapshot['question_match_input'] = {'binding': _binding(snapshot), 'candidates': candidates,
        'search_status': report['retrieval_status'], 'lookup_key': report.get('lookup_key')}
    with store.transaction():
        current = _current(store, snapshot)
        _require('question_match_input' not in json.loads(current['input_json']), 'MATCH_INPUT_ALREADY_FROZEN')
        validate_input(store, snapshot)
        store.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(snapshot), run_id))
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                      (run_id, 'QUESTION_MATCH_INPUT_FROZEN', encode(snapshot['question_match_input']), now()))
    return snapshot


def matching_payload(snapshot):
    """No teaching files, solution text or student identity in stage one."""
    item = snapshot['question_match_input']
    _require(item.get('binding') == _binding(snapshot), 'MATCH_INPUT_MISSING_OR_STALE')
    return {'binding': item['binding'], 'student_question': snapshot['student_question'],
            'student_material': snapshot['student_material'], 'student_words': snapshot['student_words'],
            'original_images': [{'name': Path(a['path']).name, 'sha256': a['sha256']} for a in snapshot['attachments']],
            'candidates': item['candidates']}


def prepare_text(snapshot, directory, *, create=True):
    content = json.dumps(matching_payload(snapshot), ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    digest = sha256(content).hexdigest()
    path = Path(directory).resolve() / 'question-text' / ('match-' + digest + '.txt')
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    _require(path.is_file() and path.read_bytes() == content, 'MATCH_INPUT_FILE_CHANGED')
    return {'kind': 'matching_text', 'path': str(path), 'name': path.name, 'sha256': digest}


def validate_result(snapshot, result):
    """Confidence is never accepted. Exact identity/mapping still uses compare."""
    keys = {'match_status', 'selected_candidate', 'relation', 'differences', 'option_mapping', 'unresolved_fields'}
    _require(isinstance(result, dict) and set(result) == keys, 'MATCH_RESULT_SCHEMA_INVALID')
    _require(result['match_status'] in ('MATCH', 'STUDENT_ONLY', 'UNRESOLVED')
             and all(isinstance(result[k], list) and all(isinstance(x, str) for x in result[k])
                     for k in ('relation', 'differences', 'unresolved_fields'))
             and isinstance(result['option_mapping'], dict), 'MATCH_RESULT_SCHEMA_INVALID')
    _require(result['match_status'] != 'UNRESOLVED' and not result['unresolved_fields'], 'MATCH_REQUIRES_REVIEW')
    student = Question.from_dict(snapshot['student_question'])
    _require(student.complete and isinstance(snapshot['student_material'], str)
             and bool(snapshot['student_material'].strip()), 'MATCH_STUDENT_SOURCE_INCOMPLETE')
    item = snapshot['question_match_input']
    if result['match_status'] == 'STUDENT_ONLY':
        _require(result['selected_candidate'] is None and result['option_mapping'] == {}
                 and result['relation'] == ['NO_SUITABLE_CANDIDATE'] and not result['differences'],
                 'MATCH_STUDENT_ONLY_CONTRACT_INVALID')
        return {'status': 'VERIFIED_STUDENT_ONLY', 'selected_candidate': None, 'comparison': None,
                'model_result': result}
    choices = [c for c in item['candidates'] if c['candidate_id'] == result['selected_candidate']]
    _require(len(choices) == 1, 'MATCH_SELECTED_CANDIDATE_NOT_FOUND')
    candidate = choices[0]
    _require(candidate.get('state') != 'REJECTED', 'MATCH_REJECTED_CANDIDATE')
    reference = Question.from_dict(candidate['reference_question'])
    comparison = compare(candidate['candidate_id'], reference, candidate['reference_material'],
                         snapshot['question_version'], student, snapshot['student_material'])
    _require(comparison.resolution_status == 'MATCH_CANDIDATE', 'MATCH_PROGRAM_COMPARISON_REJECTED')
    ref_options = {o.id: o.label for o in reference.options}
    stu_options = {o.id: o.label for o in student.options}
    expected = {ref_options[r]: stu_options[s] for r, s in comparison.option_mapping}
    _require(len(expected) == 4 and len(set(expected.values())) == 4 and result['option_mapping'] == expected,
             'MATCH_OPTION_BIJECTION_INVALID')
    _require(len(result['relation']) == len(set(result['relation']))
             and set(result['relation']) == set(comparison.relation)
             and len(result['differences']) == len(set(result['differences']))
             and set(result['differences']) == set(comparison.differences), 'MATCH_DIFFERENCES_CONFLICT')
    return {'status': 'VERIFIED_REFERENCE', 'selected_candidate': candidate['candidate_id'],
            'comparison': asdict(comparison), 'model_result': result}


def begin_attempt(store, snapshot, preparation_path, session_url):
    """Commit before the first upload. A new filename cannot bypass UNKNOWN."""
    preparation_path = Path(preparation_path).resolve()
    details = {'binding': _binding(snapshot), 'session_url': session_url,
               'preparation_path': str(preparation_path),
               'proof_path': str(preparation_path.with_name('matching-' + snapshot['run_id'] + '.json'))}
    with store.transaction():
        _current(store, snapshot)
        validate_input(store, snapshot)
        _require(not store.one("SELECT id FROM audit WHERE run_id=? AND event='QUESTION_MATCH_ATTEMPT_STARTED'",
                               (snapshot['run_id'],)), 'MATCH_ATTEMPT_REQUIRES_REVIEW')
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                      (snapshot['run_id'], 'QUESTION_MATCH_ATTEMPT_STARTED', encode(details), now()))


def _attempt(store, snapshot, proof_path, session_url):
    rows = store.all("SELECT details FROM audit WHERE run_id=? AND event='QUESTION_MATCH_ATTEMPT_STARTED'",
                     (snapshot['run_id'],))
    _require(len(rows) == 1, 'MATCH_ATTEMPT_EVIDENCE_MISSING')
    saved = json.loads(rows[0][0])
    _require(saved.get('binding') == _binding(snapshot) and saved.get('session_url') == session_url
             and saved.get('proof_path') == str(Path(proof_path).resolve()), 'MATCH_ATTEMPT_EVIDENCE_CHANGED')


def record_result(store, snapshot, proof_path):
    """Persist captured page evidence, not a caller's claim of model success."""
    from .mcp_page_contract import DeepSeekPage
    from .session_isolation import claim_deepseek_chat
    proof_path = Path(proof_path).resolve(strict=True)
    proof = json.loads(proof_path.read_text(encoding='utf-8'))
    _require(proof.get('binding') == _binding(snapshot) and proof.get('status') == 'MATCH_OUTPUT_CAPTURED',
             'MATCH_WEB_EVIDENCE_INVALID')
    page = DeepSeekPage(proof['session_url'], display_index=proof.get('display_index'))
    _attempt(store, snapshot, proof_path, page.url)
    claim_deepseek_chat(snapshot, page.url, store_path=store.path, reserve=False)
    result = json.loads(page.completed_text(proof['snapshot'], 'match_run_' + snapshot['run_id']))
    checked = validate_result(snapshot, result)
    receipt = {'binding': _binding(snapshot), 'checked': checked, 'session_url': page.url,
               'evidence_path': str(proof_path), 'evidence_sha256': sha256(proof_path.read_bytes()).hexdigest()}
    # Canonical JSON on first call matches SQLite replay exactly.
    receipt = json.loads(encode(receipt))
    with store.transaction():
        current = _current(store, snapshot)
        persisted = json.loads(current['input_json'])
        _require(persisted.get('question_match_input') == snapshot.get('question_match_input'), 'MATCH_INPUT_CHANGED')
        validate_input(store, snapshot)
        if 'question_match_result' in persisted:
            _require(persisted['question_match_result'] == receipt, 'MATCH_RESULT_ALREADY_FROZEN')
            return persisted
        persisted['question_match_result'] = receipt
        store.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(persisted), snapshot['run_id']))
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                      (snapshot['run_id'], 'QUESTION_MATCH_VERIFIED', encode(receipt), now()))
    return persisted


def validate_receipt(store, snapshot, *, session_url=None):
    """Used before teaching/generation/delivery, including after a restart."""
    validate_input(store, snapshot)
    receipt = snapshot.get('question_match_result')
    _require(isinstance(receipt, dict) and receipt.get('binding') == _binding(snapshot), 'QUESTION_MATCHING_REQUIRED')
    _require(session_url is None or receipt.get('session_url') == session_url, 'MATCH_WEB_SESSION_CHANGED')
    if snapshot.get('followup_reuse') or receipt.get('kind') == 'REUSED_MATCH':
        from .followup_reuse import validate_receipt as validate_reuse
        return validate_reuse(store, snapshot)
    proof_path = Path(receipt['evidence_path'])
    _attempt(store, snapshot, proof_path, receipt['session_url'])
    _require(sha256(proof_path.read_bytes()).hexdigest() == receipt['evidence_sha256'], 'MATCH_WEB_EVIDENCE_CHANGED')
    proof = json.loads(proof_path.read_text(encoding='utf-8'))
    from .mcp_page_contract import DeepSeekPage
    page = DeepSeekPage(receipt['session_url'], display_index=proof.get('display_index'))
    _require(proof.get('status') == 'MATCH_OUTPUT_CAPTURED' and proof.get('binding') == _binding(snapshot)
             and proof.get('session_url') == page.url, 'MATCH_WEB_EVIDENCE_CHANGED')
    result = json.loads(page.completed_text(proof['snapshot'], 'match_run_' + snapshot['run_id']))
    _require(json.loads(encode(validate_result(snapshot, result))) == receipt['checked'], 'MATCH_RESULT_CHANGED')
    audits = store.all("SELECT details FROM audit WHERE run_id=? AND event='QUESTION_MATCH_VERIFIED'", (snapshot['run_id'],))
    _require(len(audits) == 1 and json.loads(audits[0][0]) == receipt, 'MATCH_AUDIT_MISSING_OR_CHANGED')
    return receipt


def frozen_receipt(snapshot, *, session_url=None):
    from .storage import Store
    store = Store(Path(snapshot['session_store_path']).resolve(strict=True))
    try:
        return validate_receipt(store, snapshot, session_url=session_url)
    finally:
        store.close()
