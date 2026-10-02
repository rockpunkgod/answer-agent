"""Offline, explicit review of a DeepSeek upload/readback candidate.

This module never inspects image pixels or calls the desktop. A named operator
must independently review frozen images or the original text question source.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

from .mcp_generation import input_fingerprint, requires_question_text
from .mcp_page_contract import DeepSeekPage
from .mcp_preparation import _files, prepare_question_text, question_text_fields

TEXT_REVIEW_STATEMENT = ('I independently reviewed the original question source and verified '
                         'the frozen passage, stem, number and A-D options.')


class PreparationReviewError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise PreparationReviewError(message)


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _normalize(value):
    return re.sub(r'\s+', '', value).strip('"“”')


def _frozen_files(snapshot, evidence_directory=None):
    try:
        text = prepare_question_text(snapshot, evidence_directory, create=False) if requires_question_text(snapshot) else None
        return _files(snapshot, question_text_path=text['path'] if text else None)
    except (ValueError, OSError) as exc:
        raise PreparationReviewError(str(exc)) from exc


def review_preparation(snapshot: dict, candidate_path, review_path, output_path,
                       readback_evidence_path, *, store_path=None):
    """Write new immutable review artifacts; refuse every incomplete evidence set.

    review JSON schema: reviewer (nonempty), source_verified_excerpts (absolute
    course path -> exact excerpt), verified_question_stem (text read from image),
    reviewed_image_hashes (absolute image path -> SHA-256), and
    image_review_statement (the exact acknowledgement below). For zero images,
    use reviewed_question_text, reviewed_input_fingerprint, source_review_evidence,
    and question_text_review_statement=TEXT_REVIEW_STATEMENT instead. The named
    reviewer must inspect the original source independently of model readback.
    """
    candidate_path = Path(candidate_path).resolve(strict=True)
    review_path = Path(review_path).resolve(strict=True)
    output_path = Path(output_path).resolve()
    readback_evidence_path = Path(readback_evidence_path).resolve()
    _require(len({candidate_path, review_path, output_path, readback_evidence_path}) == 4,
             'Candidate, review, preparation, and readback must be distinct files')
    _require(not output_path.exists() and not readback_evidence_path.exists(),
             'Output and readback evidence paths must be new')
    candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
    review = json.loads(review_path.read_text(encoding='utf-8'))
    files = _frozen_files(snapshot, candidate_path.parent)
    courses = [x for x in files if x['kind'] == 'course']
    images = [x for x in files if x['kind'] == 'question_image']
    fast = candidate.get('preparation_mode') in ('FAST_UPLOAD_THEN_GENERATE', 'VERIFY_THEN_TEACH')
    if candidate.get('preparation_mode') == 'VERIFY_THEN_TEACH':
        from .question_matching import frozen_receipt
        _require(candidate.get('question_match_result') == frozen_receipt(snapshot, session_url=candidate['session_url']),
                 'First question verification changed')
    _require(candidate.get('status') == ('ATTACHMENTS_READY_REQUIRES_SOURCE_REVIEW' if fast else 'READBACK_CANDIDATE_REQUIRES_OPERATOR_REVIEW')
             and candidate.get('operator_verified') is False,
             'Candidate is not an unverified completed readback')
    _require(candidate.get('run_id') == snapshot['run_id'] and
             candidate.get('input_fingerprint') == input_fingerprint(snapshot),
             'Candidate does not match frozen run and question context')
    _require(candidate.get('files') == files and
             candidate.get('uploaded_teaching_hashes') ==
             {x['path']: x['sha256'] for x in courses},
             'Candidate upload manifest differs from frozen files')
    _require(candidate.get('visible_attachment_names') == [x['name'] for x in files],
             'All frozen attachment names were not observed after upload')
    session_url = candidate['session_url']
    controls = candidate.get('controls', {})
    _require(isinstance(controls, dict) and candidate.get('display_index') == controls.get('display_index'),
             'Candidate display scope differs from reviewed controls')
    page = DeepSeekPage(session_url, display_index=candidate.get('display_index'))
    from .session_isolation import claim_deepseek_chat
    claim_deepseek_chat(snapshot, page.url, store_path=store_path)
    if fast:
        _require(candidate.get('model_readback_performed') is False,
                 'Fast candidate must explicitly declare no model readback')
        observed = candidate['readiness_snapshot']
        tree = page.inspect(observed)
        page.stage_action(observed, 'probe')
        _require(all(x['name'] in tree for x in files), 'Ready attachment names missing')
        upload_events = [e for e in candidate.get('events', [])
                         if e.get('intent') in ('submit file picker once', 'submit reviewed visual picker once')]
        _require(len(upload_events) == len(files) and
                 all(e.get('status') == 'TOOL_RETURNED' for e in upload_events),
                 'Ready attachments lack successful upload journal')
        answer = ''
    else:
        _require(candidate.get('all_course_excerpts_match') is True and
                 candidate.get('question_stem_matches') is True,
                 'Candidate content checks did not pass')
        token = 'run_' + snapshot['run_id']
        observed = candidate['readback_snapshot']
        answer = page.completed_text(observed, token)
        _require(answer == candidate.get('readback_text'),
                 'Readback text does not match captured snapshot')
        submit_events = [e for e in candidate.get('events', [])
                         if e.get('intent') == 'submit readback request once']
        expected_submissions = 2 if candidate.get('material_order') == 'COURSE_THEN_QUESTION' else 1
        _require(len(submit_events) == expected_submissions and
                 all(e.get('status') == 'TOOL_RETURNED' for e in submit_events),
                 'Readback submission lacks a completed journal event')

    reviewer = review.get('reviewer')
    _require(isinstance(reviewer, str) and reviewer.strip(), 'Named reviewer is required')
    image_hashes = {x['path']: x['sha256'] for x in images}
    if images:
        statement = ('I inspected both frozen question images and verified the question stem.' if len(images) == 2 else
                     'I inspected all frozen question images and verified the question stem.')
        _require(review.get('image_review_statement') == statement,
                 'Explicit image inspection acknowledgement is required')
        _require(review.get('reviewed_image_hashes') == image_hashes,
                 'Both frozen image hashes must be explicitly reviewed' if len(images) == 2 else
                 'All frozen image hashes must be explicitly reviewed')
    else:
        fields = question_text_fields(snapshot)
        text_file = next(x for x in files if x['kind'] == 'question_text')
        _require(not review.get('image_review_statement') and not review.get('reviewed_image_hashes'),
                 'Text-only review cannot attest to nonexistent question images')
        _require(review.get('reviewed_question_text') == fields and
                 review.get('reviewed_input_fingerprint') == input_fingerprint(snapshot),
                 'Independent reviewed text fields or fingerprint differ from frozen input')
        _require(review.get('question_text_review_statement') == TEXT_REVIEW_STATEMENT and
                 isinstance(review.get('source_review_evidence'), str) and review['source_review_evidence'].strip(),
                 'Independent original-source text review acknowledgement and evidence are required')
        _require(candidate.get('question_text_fields') == fields and
                 candidate.get('question_text_sha256') == text_file['sha256'],
                 'Candidate question text differs from derived frozen attachment')
    stem = review.get('verified_question_stem')
    expected_stem = snapshot['student_question'].get('verified_stem')
    _require(isinstance(stem, str) and isinstance(expected_stem, str) and
             len(_normalize(stem)) >= 10 and _normalize(stem) == _normalize(expected_stem),
             'Reviewed image stem differs from frozen question stem')
    stem_rows = [line.removeprefix('题干：').strip() for line in answer.splitlines()
                 if line.startswith('题干：')]
    _require(fast or (len(stem_rows) == 1 and _normalize(stem) in _normalize(stem_rows[0])),
             'Reviewed image stem is absent from readback')

    excerpts = review.get('source_verified_excerpts')
    _require(isinstance(excerpts, dict) and set(excerpts) == {x['path'] for x in courses},
             'Every course needs an explicit source-verified excerpt')
    _require(fast or candidate.get('course_readback_excerpts') == excerpts,
             'Reviewed excerpts differ from candidate source checks')
    for item in courses:
        excerpt = excerpts[item['path']]
        rows = [line.removeprefix(item['name'] + '：').strip() for line in answer.splitlines()
                if line.startswith(item['name'] + '：')]
        _require(isinstance(excerpt, str) and len(excerpt) >= 10 and
                 excerpt in Path(item['path']).read_text(encoding='utf-8') and
                 (fast or rows == [excerpt]), f'Course excerpt is not source and readback verified: {item["name"]}')

    preparation = {
        'status': 'ATTACHMENTS_READY_SOURCE_REVIEWED' if fast else 'OPERATOR_VERIFIED_UPLOAD_AND_INPUT',
        'preparation_mode': candidate['preparation_mode'] if fast else 'STRICT_READBACK',
        'model_readback_performed': not fast,
        'operator_verified': True,
        'reviewer': reviewer.strip(),
        'run_id': snapshot['run_id'],
        'session_id': snapshot.get('session_id'),
        'question_id': snapshot['question_id'],
        'binding_id': snapshot.get('binding_id'),
        'session_url': session_url,
        'display_index': page.display_index,
        'input_fingerprint': input_fingerprint(snapshot),
        'uploaded_teaching_hashes': {x['path']: x['sha256'] for x in courses},
        'reviewed_image_hashes': image_hashes,
        'verified_question_stem': stem,
        'course_readback_excerpts': excerpts,
        'candidate_evidence': str(candidate_path),
        'candidate_sha256': _digest(candidate_path),
        'operator_review_evidence': str(review_path),
        'operator_review_sha256': _digest(review_path),
        'readback_evidence': str(readback_evidence_path),
    }
    active_tree = page.inspect(observed)
    active_window = re.search(r'window "([^\n]+)"', active_tree)
    _require(active_window is not None, 'Reviewed Edge window title is missing')
    preparation['window_name'] = active_window[1]
    text_file = next((x for x in files if x['kind'] == 'question_text'), None)
    if text_file:
        preparation['question_text_file'] = text_file
    if not images:
        preparation.update(reviewed_question_text=review['reviewed_question_text'],
            reviewed_input_fingerprint=review['reviewed_input_fingerprint'],
            question_text_review_statement=review['question_text_review_statement'],
            source_review_evidence=review['source_review_evidence'],
            question_text_file=text_file)
    readback_evidence_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation makes an accidental second approval fail closed.
    with readback_evidence_path.open('x', encoding='utf-8') as stream:
        json.dump(observed, stream, ensure_ascii=False, indent=2)
    preparation['readback_sha256'] = _digest(readback_evidence_path)
    if fast:
        preparation['readiness_evidence'] = preparation.pop('readback_evidence')
        preparation['readiness_sha256'] = preparation.pop('readback_sha256')
        preparation['source_verified_excerpts'] = preparation.pop('course_readback_excerpts')
    with output_path.open('x', encoding='utf-8') as stream:
        json.dump(preparation, stream, ensure_ascii=False, indent=2)
    return preparation
