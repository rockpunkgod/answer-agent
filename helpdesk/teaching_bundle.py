"""Verify teaching snapshots and guard the sole source for real operations.

Legacy snapshots remain available for history and anonymous tests. New real
generation and uploads require a pinned ANSWER source and course authorization.
This module does not call DeepSeek, inspect the desktop, or send a message.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import mkdtemp


DEFAULT_SKILL_ROOT = Path('D:/Codex/skills/gaokao-english')
TYPE_MODULES = {
    '阅读理解': 'references/reading-comprehension.md',
    '七选五': 'references/seven-five.md',
    '完形填空': 'references/cloze.md',
    '语法填空': 'references/grammar-fill.md',
    '应用文': 'references/application-writing.md',
    '读后续写': 'references/continuation-writing.md',
    '听力': 'references/listening.md',
    '综合训练': 'references/integrated-practice.md',
}
BASE_FILES = ('SKILL.md', 'references/delivery-contract.md', 'references/router.md')
EVIDENCE_GAPS = 'references/evidence-gaps.md'
COURSE_EVIDENCE = 'references/course-evidence.md'
REFERENCE_PATTERN = re.compile(r'references/[a-z][a-z0-9-]*\.md')
RAW_REFERENCE_PATTERN = re.compile(r'''references/[^\s`\]\)"'，。、；;]+''')
MAX_WORKFLOW_FILE_BYTES = 1_000_000
REVIEWED_POLICY_ID = 'gaokao-english-reviewed-2026-09-30-v1'
REVIEWED_SOURCE_SHA256 = {
    'SKILL.md': '527acaf2042fb5fb9e744b8b32a7ea8909d96ee57244feebeaba64f27c0a3f9d',
    'references/application-writing.md': 'bc8798cde0a4d8ae5791aedfb4d1173f986697195af9609cacd927b48f9fdf22',
    'references/cloze.md': '84eeeac9d849aab6062f0a14c7a1346e6fa729c1a8c9af3823ed18b5dddf9754',
    'references/continuation-writing.md': 'f44003b1813b52a33431ceee470ae14a5549ef44a6552d779de08e8372bb635b',
    'references/course-evidence.md': 'd9b316dc07cce038752f67e7de8c604bdbcf4ab490eea2cbea3826272c3538f0',
    'references/delivery-contract.md': '02655ec072b7368e9676f4d19b245f91c32ff6128a90cc941656906e74f693dd',
    'references/evidence-gaps.md': '1c5904f8c32ba1e56cca2221648e0d8febbfb13e8d8159e88f15b2b0b4522405',
    'references/grammar-fill.md': 'a50d757c0b63b652394dbd6da80403f8ab21586945468e52a07cb1909beb3962',
    'references/integrated-practice.md': 'b86a5c8592817303d0bf7506d042fb4a3dd29d06ebe391255d8d9d7a301502d8',
    'references/listening.md': 'dfd7e2e200193114de0f9d9f6b2da35f0201ab1e84ba1407c0d6c475fb16fdbe',
    'references/reading-comprehension.md': '10f5ea4689e0238f1e95b836031324225d43bc807193f27fc3140ca86a84085c',
    'references/router.md': '15cb3ee0b48bc0db685304791a6d3a31da4389815972107c3a5c3840c2a53f50',
    'references/seven-five.md': 'e40c809bb5c2512676d6419e892d8401c93bc85eb3e55bbd6ac2f0e452b9da76',
}

# Preserve the skill's activation limits; a module file existing is not enough
# to make a full course method available.
COVERAGE = {
    '阅读理解': ('ACTIVATED_WITH_BOUNDARIES', True, '仅在题面实际触发且证据完整时使用已激活方法'),
    '七选五': ('ACTIVATED_WITH_BOUNDARIES', True, '需按主体、时态、结构和实际内容关系核对'),
    '完形填空': ('LOCAL_METHODS_ONLY', True, '仅已取证的语篇逻辑与瞻前顾后局部操作'),
    '语法填空': ('LOCAL_RULES_ONLY', True, '仅非谓语先后关系、主谓一致和介词语义分类局部规则'),
    '应用文': ('LOCAL_RULES_ONLY', True, '仅演讲和通知两种文体的局部内容检查'),
    '读后续写': ('CONFLICT_DISCLOSURE_ONLY', False, 'PTSD 槽位与十句结构版本冲突，不能据此生成作文'),
    '听力': ('NOT_ACTIVATED', False, '课程方法尚未形成可执行的核验证据闭环'),
    '综合训练': ('NOT_ACTIVATED', False, '课程方法尚未形成可执行的核验证据闭环'),
}


class TeachingBundleError(ValueError):
    pass


def _safe_file(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or '..' in Path(relative).parts or Path(relative).suffix != '.md':
        raise TeachingBundleError(f'Unsafe teaching file reference: {relative}')
    path = root / relative
    current = root
    if root.is_symlink():
        raise TeachingBundleError('Skill root cannot be a symbolic link')
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise TeachingBundleError(f'Symbolic link teaching file is forbidden: {relative}')
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TeachingBundleError(f'Missing teaching dependency: {relative}') from exc
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise TeachingBundleError(f'Teaching file escapes skill root: {relative}')
    if resolved.stat().st_size > MAX_WORKFLOW_FILE_BYTES:
        raise TeachingBundleError(f'Teaching file exceeds Workflow limit: {relative}')
    return resolved


def _read_source(root: Path, relative: str) -> bytes:
    path = _safe_file(root, relative)
    data = path.read_bytes()
    if len(data) > MAX_WORKFLOW_FILE_BYTES or path != _safe_file(root, relative):
        raise TeachingBundleError(f'Teaching source changed during read: {relative}')
    try:
        data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise TeachingBundleError(f'Teaching source is not UTF-8: {relative}') from exc
    return data


def _selected_files(question_type: str, request_kind: str) -> tuple[str, ...]:
    if question_type not in TYPE_MODULES:
        raise TeachingBundleError('Explicit supported question type required')
    if request_kind not in ('answer', 'course_basis', 'coverage_notice'):
        raise TeachingBundleError('Explicit request kind required')
    status, may_answer, _ = COVERAGE[question_type]
    if request_kind == 'answer' and not may_answer:
        raise TeachingBundleError(f'{question_type} is {status}; answer generation is not supported')
    paths = (*BASE_FILES, TYPE_MODULES[question_type], EVIDENCE_GAPS)
    if request_kind == 'course_basis':
        paths += (COURSE_EVIDENCE,)
    return paths


def build_bundle(*, question_type: str, output_root: str | Path,
                 request_kind: str = 'answer', skill_root: str | Path = DEFAULT_SKILL_ROOT) -> Path:
    """Return manifest path; its workflow_teaching_paths can feed Workflow directly."""
    source_root = Path(skill_root)
    selected = _selected_files(question_type, request_kind)
    sources = {relative: _read_source(source_root, relative) for relative in selected}
    reviewed_source = source_root.resolve(strict=True) == DEFAULT_SKILL_ROOT.resolve(strict=True)
    if reviewed_source:
        changed = [relative for relative, data in sources.items()
                   if sha256(data).hexdigest() != REVIEWED_SOURCE_SHA256.get(relative)]
        if changed:
            raise TeachingBundleError(f'Reviewed course policy changed; re-review required: {changed}')
    referenced = set()
    for relative, data in sources.items():
        text = data.decode('utf-8')
        raw_references = {candidate.rstrip('.:') for candidate in RAW_REFERENCE_PATTERN.findall(text)}
        unsafe = {candidate for candidate in raw_references if not REFERENCE_PATTERN.fullmatch(candidate)}
        if unsafe:
            raise TeachingBundleError(f'Unsafe or unlisted teaching reference in {relative}: {sorted(unsafe)}')
        referenced.update(raw_references)
    deferred = {COURSE_EVIDENCE} if request_kind != 'course_basis' else set()
    deferred.update(set(TYPE_MODULES.values()) - {TYPE_MODULES[question_type]})
    missing = referenced - set(selected) - deferred
    if missing:
        raise TeachingBundleError(f'Undeclared required teaching dependency: {sorted(missing)}')
    # A deferred route is intentional, never an accidental missing file.
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = Path(mkdtemp(prefix='teaching-bundle-', dir=output))
    files = []
    workflow_paths = []
    for relative in selected:
        source = _safe_file(source_root, relative)
        snapshot = bundle / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(sources[relative])
        digest = sha256(sources[relative]).hexdigest()
        files.append({'role': ('entry' if relative == 'SKILL.md' else
                               'selected_module' if relative == TYPE_MODULES[question_type] else
                               'conditional_course_evidence' if relative == COURSE_EVIDENCE else
                               'evidence_gap' if relative == EVIDENCE_GAPS else 'delivery_or_router'),
                      'relative_path': relative,
                      'source_path': str(source), 'source_sha256': digest,
                      'snapshot_path': str(snapshot.resolve()), 'snapshot_sha256': digest,
                      'bytes': len(sources[relative])})
        workflow_paths.append(str(snapshot.resolve()))
    coverage, may_answer, explanation = COVERAGE[question_type]
    manifest = {
        'format_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'skill': 'gaokao-english', 'skill_root': str(source_root.resolve(strict=True)),
        'reviewed_policy_id': REVIEWED_POLICY_ID if reviewed_source else None,
        'policy_review_status': 'PINNED_REVIEWED_SOURCE' if reviewed_source else 'UNREVIEWED_CUSTOM_SOURCE',
        'policy_source_sha256': {relative: sha256(sources[relative]).hexdigest() for relative in selected},
        'question_type': question_type, 'request_kind': request_kind,
        'course_coverage': coverage, 'course_coverage_note': explanation,
        'answer_generation_allowed_by_course': reviewed_source and may_answer and request_kind == 'answer',
        'real_deepseek_uploaded': False, 'real_delivery_verified': False,
        'files': files, 'workflow_teaching_paths': workflow_paths,
        'deferred_references': [
            {'relative_path': relative,
             'reason': ('课程依据或版本请求时另行打包' if relative == COURSE_EVIDENCE
                        else '由题面路由决定的其他题型模块，不在本次包内')}
            for relative in sorted(deferred)
        ],
    }
    manifest_path = bundle / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (bundle / 'teaching_paths.txt').write_text('\n'.join(workflow_paths) + '\n', encoding='utf-8')
    verify_bundle(manifest_path)
    return manifest_path


def verify_bundle(manifest_path: str | Path, *, check_sources: bool = True,
                  for_generation: bool = False) -> dict:
    """Reject altered snapshots, changed source files, or path redirection."""
    if for_generation and not check_sources:
        raise TeachingBundleError('ANSWER_SOURCE_CHECK_REQUIRED')
    path = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('format_version') == 2:
        from .answer_teaching import verify_answer_bundle
        manifest = verify_answer_bundle(manifest_path, check_sources=check_sources)
        if for_generation:
            missing = manifest.get('missing_dependencies', [])
            if missing:
                raise TeachingBundleError('ANSWER_REQUIRED_DEPENDENCIES_MISSING: ' + ', '.join(missing))
            if manifest.get('answer_generation_allowed_by_course') is not True:
                raise TeachingBundleError('ANSWER_TEACHING_NOT_ACTIVATED: source preview is not generation approval')
        return manifest
    if for_generation:
        raise TeachingBundleError('ANSWER_SOURCE_REQUIRED: legacy teaching bundles are read-only')
    if manifest.get('skill') != 'gaokao-english':
        raise TeachingBundleError('Manifest has unexpected skill')
    root = Path(manifest['skill_root'])
    selected = _selected_files(manifest['question_type'], manifest['request_kind'])
    files = manifest['files']
    if tuple(item['relative_path'] for item in files) != selected:
        raise TeachingBundleError('Manifest file allowlist differs from selected type')
    if len(files) != len({item['relative_path'] for item in files}):
        raise TeachingBundleError('Duplicate teaching file in manifest')
    if manifest['workflow_teaching_paths'] != [item['snapshot_path'] for item in files]:
        raise TeachingBundleError('Workflow allowlist differs from snapshot manifest')
    reviewed_source = root.resolve(strict=True) == DEFAULT_SKILL_ROOT.resolve(strict=True)
    coverage, may_answer, explanation = COVERAGE[manifest['question_type']]
    expected_permission = reviewed_source and may_answer and manifest['request_kind'] == 'answer'
    if manifest.get('answer_generation_allowed_by_course') is not expected_permission:
        raise TeachingBundleError('Course answer authorization differs from reviewed policy')
    if (manifest.get('course_coverage'), manifest.get('course_coverage_note')) != (coverage, explanation):
        raise TeachingBundleError('Course coverage differs from reviewed policy')
    if reviewed_source:
        if manifest.get('reviewed_policy_id') != REVIEWED_POLICY_ID or manifest.get('policy_review_status') != 'PINNED_REVIEWED_SOURCE':
            raise TeachingBundleError('Reviewed policy metadata mismatch')
    elif (manifest.get('reviewed_policy_id') is not None or
          manifest.get('policy_review_status') != 'UNREVIEWED_CUSTOM_SOURCE'):
        raise TeachingBundleError('Custom source cannot claim reviewed policy')
    if manifest.get('policy_source_sha256') != {item['relative_path']: item['source_sha256'] for item in files}:
        raise TeachingBundleError('Policy source hashes differ from file manifest')
    for item in files:
        relative = item['relative_path']
        snapshot = _safe_file(path.parent, relative)
        if str(snapshot) != item['snapshot_path']:
            raise TeachingBundleError(f'Snapshot path mismatch: {relative}')
        snapshot_bytes = snapshot.read_bytes()
        snapshot_hash = sha256(snapshot_bytes).hexdigest()
        if snapshot_hash != item['snapshot_sha256']:
            raise TeachingBundleError(f'Snapshot hash mismatch: {relative}')
        if len(snapshot_bytes) != item['bytes']:
            raise TeachingBundleError(f'Snapshot size mismatch: {relative}')
        if item['snapshot_sha256'] != item['source_sha256']:
            raise TeachingBundleError(f'Snapshot differs from source policy: {relative}')
        if reviewed_source and item['source_sha256'] != REVIEWED_SOURCE_SHA256.get(relative):
            raise TeachingBundleError(f'Reviewed course policy changed: {relative}')
        if check_sources:
            source = _safe_file(root, relative)
            if str(source) != item['source_path']:
                raise TeachingBundleError(f'Source path mismatch: {relative}')
            source_bytes = source.read_bytes()
            if len(source_bytes) != item['bytes'] or sha256(source_bytes).hexdigest() != item['source_sha256']:
                raise TeachingBundleError(f'Source hash mismatch: {relative}')
    return manifest


def verify_frozen_teaching(snapshot: dict) -> dict:
    """Recheck the sole source before an upload or resumed generation."""
    skills = snapshot.get('teaching_skills')
    if not isinstance(skills, list) or not skills or any(not isinstance(s, dict) for s in skills):
        raise TeachingBundleError('ANSWER_SOURCE_UNBOUND: re-freeze the reviewed question')
    paths = [s.get('manifest_path') for s in skills]
    if any(not isinstance(p, str) or not p.strip() for p in paths) or len(set(paths)) != 1:
        raise TeachingBundleError('ANSWER_SOURCE_UNBOUND: re-freeze the reviewed question')
    manifest = verify_bundle(paths[0], for_generation=True)
    if [s.get('path') for s in skills] != manifest['workflow_teaching_paths']:
        raise TeachingBundleError('ANSWER_FROZEN_FILES_CHANGED')
    hashes = {entry['snapshot_path']: entry['snapshot_sha256'] for entry in manifest['files']}
    for skill in skills:
        if (skill.get('source') != 'verified_teaching_manifest'
                or skill.get('reviewed_policy_id') != manifest['reviewed_policy_id']
                or skill.get('question_type') != manifest['question_type']
                or skill.get('sha256') != hashes.get(skill['path'])):
            raise TeachingBundleError('ANSWER_FROZEN_SOURCE_CHANGED')
        content = Path(skill['path']).read_bytes()
        if sha256(content).hexdigest() != skill['sha256'] or content.decode('utf-8') != skill.get('content'):
            raise TeachingBundleError('ANSWER_FROZEN_CONTENT_CHANGED')
    return manifest
