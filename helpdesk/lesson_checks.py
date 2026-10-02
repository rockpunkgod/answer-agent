"""Run the pinned ANSWER checker, without inventing teaching rules or a solver.

Only the declared, byte-verified script runs with a bounded interpreter command.
Inputs come from the frozen student version; outputs are stored on the same run.
Neither a model's approval nor an exit code alone proves teaching correctness.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .answer_teaching import _file, _reject_redirects
from .domain import Question
from .teaching_bundle import verify_frozen_teaching


MAX_BYTES = 1_000_000
KINDS = {'阅读理解': 'reading', '完形填空': 'cloze'}


class LessonCheckError(ValueError):
    pass


def _source(snapshot):
    question = Question.from_dict(snapshot['student_question'])
    material = snapshot.get('student_material')
    if (not question.complete or not isinstance(material, str) or not material.strip()
            or not question.number.isdigit() or not 1 <= int(question.number) <= 999):
        raise LessonCheckError('ANSWER_CHECK_REQUIRES_VERIFIED_STUDENT_SOURCE')
    # Preserve the student's text and labels; only introduce ordinary separators.
    text = material + '\n\n' + question.number + '. ' + question.verified_stem + '\n'
    text += '\n'.join(o.label + '. ' + o.verified_text for o in sorted(question.options, key=lambda o: o.order))
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise LessonCheckError('ANSWER_CHECK_INPUT_TOO_LARGE')
    return text


def _contract(snapshot, manifest):
    if manifest.get('format_version') != 3 or manifest.get('required_task_checks') != ['SOURCE', 'DRAFT']:
        raise LessonCheckError('ANSWER_TASK_CHECK_CONTRACT_REQUIRED')
    kind = KINDS.get(manifest['question_type'])
    if not kind or len(manifest['required_dependencies']) != 1:
        raise LessonCheckError('ANSWER_CHECKER_ROUTE_NOT_SUPPORTED')
    source = _source(snapshot)
    if any(not isinstance(snapshot.get(k), str) or not snapshot[k] for k in
           ('case_id', 'question_id', 'question_version', 'run_id', 'session_id', 'binding_id')):
        raise LessonCheckError('ANSWER_CHECK_RUN_BINDING_REQUIRED')
    relative = manifest['required_dependencies'][0]
    root = Path(snapshot['teaching_skills'][0]['manifest_path']).absolute().parent
    script = _file(root.resolve(strict=True), 'sources/' + relative)
    digest = sha256(script.read_bytes()).hexdigest()
    if digest != manifest['policy_source_sha256'][relative]:
        raise LessonCheckError('ANSWER_CHECKER_CONTENT_CHANGED')
    binding = {k: snapshot[k] for k in ('case_id', 'question_id', 'question_version', 'context_revision',
                                       'run_id', 'session_id', 'binding_id')}
    binding.update(answer_commit=manifest['source_repository']['commit'],
                   source_sha256=sha256(source.encode('utf-8')).hexdigest(),
                   checker_path=relative, checker_sha256=digest)
    return source, script, kind, binding


def validate_source_check(snapshot, *, manifest=None):
    manifest = manifest or verify_frozen_teaching(snapshot)
    _, _, _, binding = _contract(snapshot, manifest)
    evidence = snapshot.get('teaching_source_check')
    if (not isinstance(evidence, dict) or evidence.get('binding') != binding
            or evidence.get('stage') != 'SOURCE' or evidence.get('status') != 'SOURCE_READY'
            or evidence.get('exit_code') != 0 or not isinstance(evidence.get('output'), dict)
            or not evidence['output'].get('source_paragraphs')):
        raise LessonCheckError('ANSWER_SOURCE_CHECK_MISSING_OR_STALE')
    return evidence


def validate_task_checks(store, snapshot, body):
    """Recheck the original source and persisted checker proof before delivery.

    The model result cannot supply these records. They are written by Workflow
    after invoking the pinned script on the frozen source and exact answer.
    """
    source = validate_source_check(snapshot)
    rows = store.all("SELECT event,details FROM audit WHERE run_id=? AND event IN "
                     "('TEACHING_SOURCE_CHECKED','TEACHING_DRAFT_CHECKED','TEACHING_DRAFT_CHECK_FAILED')",
                     (snapshot['run_id'],))
    sources = [json.loads(r['details']) for r in rows if r['event'] == 'TEACHING_SOURCE_CHECKED']
    drafts = [json.loads(r['details']) for r in rows if r['event'] == 'TEACHING_DRAFT_CHECKED']
    if (sources != [source] or len(drafts) != 1
            or any(r['event'] == 'TEACHING_DRAFT_CHECK_FAILED' for r in rows)):
        raise LessonCheckError('ANSWER_TASK_CHECK_EVIDENCE_MISSING_OR_CHANGED')
    draft = drafts[0]
    if (draft.get('stage') != 'DRAFT' or draft.get('binding') != source['binding']
            or draft.get('status') != 'NO_AUTOMATIC_FLAGS' or draft.get('exit_code') != 0
            or draft.get('draft_sha256') != sha256(body.encode('utf-8')).hexdigest()
            or not isinstance(draft.get('output'), dict)
            or draft['output'].get('automatic_flags') != []):
        raise LessonCheckError('ANSWER_DRAFT_CHECK_MISSING_OR_STALE')
    return draft


def check_lesson(snapshot, *, draft=None):
    """SOURCE runs before generation; DRAFT runs on the exact returned answer."""
    manifest = verify_frozen_teaching(snapshot)
    source, script, kind, binding = _contract(snapshot, manifest)
    stage = 'SOURCE' if draft is None else 'DRAFT'
    if stage == 'DRAFT':
        validate_source_check(snapshot, manifest=manifest)
        if not isinstance(draft, str) or not draft.strip() or len(draft.encode('utf-8')) > MAX_BYTES:
            raise LessonCheckError('ANSWER_CHECK_DRAFT_INVALID')
    database = snapshot.get('session_store_path')
    if not isinstance(database, str) or not Path(database).is_file():
        raise LessonCheckError('ANSWER_CHECK_BUSINESS_DATABASE_REQUIRED')
    # The trusted workflow binds the local database path. No model-supplied
    # script, executable, flags, input paths or output paths are accepted.
    root = Path(database).absolute().parent / 'lesson-check-inputs'
    _reject_redirects(root)
    root.mkdir(exist_ok=True)
    _reject_redirects(root)
    with tempfile.TemporaryDirectory(prefix='answer-', dir=root) as directory:
        work = Path(directory).resolve(strict=True)
        if not work.is_relative_to(root.resolve(strict=True)):
            raise LessonCheckError('ANSWER_CHECK_PATH_OUTSIDE_SCOPE')
        source_file = work / 'student-source.txt'
        source_file.write_text(source, encoding='utf-8')
        command = [sys.executable, '-I', '-B', '-X', 'utf8', str(script),
                   '--source', str(source_file), '--kind', kind,
                   '--only', snapshot['student_question']['number']]
        if draft is not None:
            draft_file = work / 'answer-draft.txt'
            draft_file.write_text(draft, encoding='utf-8')
            command += ['--draft', str(draft_file)]
        try:
            result = subprocess.run(command, cwd=str(work), capture_output=True,
                timeout=10, shell=False,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        except subprocess.TimeoutExpired:
            raise LessonCheckError('ANSWER_CHECKER_TIMEOUT') from None
        except OSError:
            raise LessonCheckError('ANSWER_CHECKER_UNAVAILABLE') from None
    if (result.returncode not in (0, 1) or len(result.stdout) > MAX_BYTES
            or (stage == 'SOURCE' and result.returncode != 0)):
        raise LessonCheckError('ANSWER_CHECKER_FAILED')
    try:
        output = json.loads(result.stdout)
        if not isinstance(output, dict) or not isinstance(output.get('next_step'), str):
            raise ValueError()
        if stage == 'SOURCE':
            if result.returncode != 0 or not isinstance(output.get('source_paragraphs'), list) or not output['source_paragraphs']:
                raise ValueError()
            status = 'SOURCE_READY'
        else:
            flags = output.get('automatic_flags')
            if not isinstance(flags, list) or any(not isinstance(flag, dict) for flag in flags):
                raise ValueError()
            if result.returncode != int(bool(flags)):
                raise ValueError()
            status = 'REVIEW_REQUIRED' if flags else 'NO_AUTOMATIC_FLAGS'
    except (ValueError, TypeError, UnicodeError):
        raise LessonCheckError('ANSWER_CHECKER_RESULT_INVALID') from None
    return {'stage': stage, 'binding': binding, 'status': status, 'exit_code': result.returncode,
            'draft_sha256': sha256(draft.encode('utf-8')).hexdigest() if draft is not None else None,
            'output': output, 'teaching_correctness_proven': False}
