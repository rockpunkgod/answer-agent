"""Pinned ANSWER input using existing routes and teaching manifests.

Reads Git objects and approved local files only. No fetch, checkout, teaching
script execution, model summary, desktop operation or external delivery.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from .teaching_routes import resolve_route, OBJECTIVE, GRAMMAR, WRITING

ROOT = Path(__file__).resolve().parents[1]
PIN_CONFIG = ROOT / 'config/teaching-source.toml'
REPOSITORY_URL = 'https://github.com/rockpunkgod/ANSWER.git'
MAX_BYTES = 1_000_000


class TeachingSourceError(ValueError):
    pass


def source_pin():
    try:
        value = tomllib.loads(PIN_CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise TeachingSourceError('ANSWER_SOURCE_PIN_UNAVAILABLE') from None
    if (set(value) != {'repository_url', 'commit', 'repository'}
            or value['repository_url'] != REPOSITORY_URL
            or not isinstance(value['commit'], str) or not re.fullmatch('[0-9a-f]{40}', value['commit'])
            or not isinstance(value['repository'], str) or not value['repository'].strip()):
        raise TeachingSourceError('ANSWER_SOURCE_PIN_INVALID')
    return value


def _git(root, *arguments):
    try:
        result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True,
            timeout=15, shell=False,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    except (OSError, subprocess.TimeoutExpired):
        raise TeachingSourceError('ANSWER_GIT_READ_UNAVAILABLE') from None
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise TeachingSourceError('ANSWER_GIT_READ_UNCONFIRMED')
    return result.stdout


def _repository(repository, pin):
    try:
        raw = Path(repository) if repository is not None else ROOT / pin['repository']
        _reject_redirects(raw.absolute())
        root = raw.resolve(strict=True)
    except (OSError, RuntimeError):
        raise TeachingSourceError('ANSWER_REPOSITORY_UNAVAILABLE') from None
    top = Path(_git(root, 'rev-parse', '--show-toplevel').decode('utf-8').strip()).resolve(strict=True)
    if top != root or _git(root, 'remote', 'get-url', 'origin').decode('utf-8').strip() != pin['repository_url']:
        raise TeachingSourceError('ANSWER_REPOSITORY_SOURCE_MISMATCH')
    if _git(root, 'rev-parse', 'HEAD').decode('ascii').strip() != pin['commit']:
        raise TeachingSourceError('ANSWER_COMMIT_CHANGED_REVIEW_REQUIRED')
    # Inspect actual index/worktree differences. On Windows, a changed stat
    # cache alone can produce a porcelain "M" for identical normalized text.
    if (_git(root, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', 'HEAD', '--').strip()
            or _git(root, 'ls-files', '--others', '--exclude-standard').strip()):
        raise TeachingSourceError('ANSWER_WORKING_TREE_CHANGED_REVIEW_REQUIRED')
    return root


def _reject_redirects(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise TeachingSourceError('ANSWER_PATH_REDIRECTED')


def _file(root, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or '..' in parts or ':' in relative or '\\' in relative:
        raise TeachingSourceError('ANSWER_PATH_OUTSIDE_SCOPE')
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise TeachingSourceError('ANSWER_PATH_REDIRECTED')
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise TeachingSourceError('ANSWER_DEPENDENCY_MISSING:' + relative) from None
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.stat().st_size > MAX_BYTES:
        raise TeachingSourceError('ANSWER_PATH_OUTSIDE_SCOPE')
    return resolved


def _content(root, commit, relative):
    path = _file(root, relative)
    data = _git(root, 'show', commit + ':' + relative)
    # Windows Git commonly checks LF text out as CRLF. Upload Git's exact
    # committed bytes; reject all working-copy changes except this EOL form.
    if path.read_bytes().replace(b'\r\n', b'\n') != data.replace(b'\r\n', b'\n'):
        raise TeachingSourceError('ANSWER_WORKING_FILE_CHANGED:' + relative)
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        raise TeachingSourceError('ANSWER_DEPENDENCY_NOT_UTF8') from None
    return data


def _selection(question_type, request_kind):
    if type(question_type) is not str or type(request_kind) is not str:
        raise TeachingSourceError('ANSWER_ROUTE_REQUIRES_REVIEW')
    if request_kind == 'course_basis':
        raise TeachingSourceError('ANSWER_COURSE_EVIDENCE_LOOKUP_REQUIRED')
    try:
        route = resolve_route(question_type, 'answer' if request_kind == 'coverage_notice' else request_kind)
    except ValueError:
        raise TeachingSourceError('ANSWER_ROUTE_REQUIRES_REVIEW') from None
    documents = ['README.md', route.skill + '/SKILL.md', f'agents/{route.agent}.md',
                 route.skill + '/' + route.module]
    if route.skill == OBJECTIVE:
        documents += [route.skill + '/references/' + name + '.md'
                      for name in ('delivery-contract', 'router', 'evidence-gaps')]
    elif route.skill == GRAMMAR:
        documents += [route.skill + '/references/' + name + '.md'
                      for name in ('objective-revision', 'method-application-example', 'course-evidence')]
    requirements = []
    if route.checker:
        requirements.append(route.checker)
    return route, tuple(documents), tuple(requirements)


def _input(pin, question_type, documents):
    # This header identifies sources and scope; every teaching file below is
    # included verbatim. It does not reinterpret, combine or summarize methods.
    head = ('ANSWER teaching input\nRepository: ' + pin['repository_url'] + '\nCommit: ' + pin['commit'] +
            '\nSelected question type: ' + question_type + '\n'
            'Scope follows this commit\'s README routing. Agent definitions below are source documents, '
            'not additional running agents.\n'
            'The following sections contain complete committed source files. '
            'Relative paths identify those sections, not access to local files. '
            'Other type modules and optional course lookup materials are not supplied.\n')
    return head.encode('utf-8') + b''.join(
        ('\n\n--- BEGIN SOURCE: ' + name + ' ---\n').encode('utf-8') + data +
        ('\n--- END SOURCE: ' + name + ' ---\n').encode('utf-8') for name, data in documents.items())


def _bundle_key(pin, question_type, request_kind, hashes, *, for_generation=False):
    inputs = [pin['commit'], question_type, request_kind, list(hashes.items())]
    if for_generation:
        inputs.append('TASK_CHECKED_SOURCE_V1')
    return sha256(json.dumps(inputs, ensure_ascii=False).encode('utf-8')).hexdigest()


def build_answer_bundle(*, question_type, output_root, request_kind='answer', repository=None, for_generation=False):
    if type(for_generation) is not bool:
        raise TeachingSourceError('ANSWER_BUILD_MODE_INVALID')
    # The current Question/generation contract supports four-option objective
    # tasks. Preserve the other original source previews and manual workflows.
    if for_generation and (question_type not in ('阅读理解', '完形填空') or request_kind != 'answer'):
        raise TeachingSourceError('ANSWER_AUTOMATIC_TASK_SHAPE_NOT_SUPPORTED')
    pin = source_pin()
    root = _repository(repository, pin)
    route, selected, required = _selection(question_type, request_kind)
    documents = {name: _content(root, pin['commit'], name) for name in selected}
    tracked = set(_git(root, 'ls-tree', '-r', '--name-only', pin['commit']).decode('utf-8').splitlines())
    missing = [name for name in required if name not in tracked]
    if for_generation and missing:
        raise TeachingSourceError('ANSWER_REQUIRED_DEPENDENCIES_MISSING: ' + ', '.join(missing))
    checks = {name: _content(root, pin['commit'], name) for name in required if name not in missing}
    content = _input(pin, question_type, documents)
    if len(content) > MAX_BYTES:
        raise TeachingSourceError('ANSWER_INPUT_EXCEEDS_WORKFLOW_LIMIT')
    inputs = {**documents, **checks}
    hashes = {name: sha256(data).hexdigest() for name, data in inputs.items()}
    key = _bundle_key(pin, question_type, request_kind, hashes, for_generation=for_generation)
    output = Path(output_root).absolute()
    _reject_redirects(output)
    bundle = output.resolve() / ('answer-' + key[:24])
    _reject_redirects(bundle)
    manifest_path = bundle / 'manifest.json'
    if bundle.exists():
        current = verify_answer_bundle(manifest_path)
        if current['source_repository']['root'] != str(root):
            raise TeachingSourceError('ANSWER_CACHE_SOURCE_CHANGED')
        return manifest_path
    bundle.mkdir(parents=True)
    sources = []
    for name, data in inputs.items():
        snapshot = bundle / 'sources' / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
        sources.append({'relative_path': name, 'sha256': sha256(data).hexdigest(), 'bytes': len(data)})
    teaching_input = bundle / 'teaching-input.md'
    teaching_input.write_bytes(content)
    digest = sha256(content).hexdigest()
    manifest = dict(format_version=2, created_at=datetime.now(timezone.utc).isoformat(),
        skill=route.skill, skill_root=str(root / route.skill), question_type=question_type, request_kind=request_kind,
        source_repository={'url': pin['repository_url'], 'commit': pin['commit'], 'root': str(root)},
        source_files=sources, required_dependencies=list(required), missing_dependencies=missing,
        policy_review_status='PINNED_ANSWER_SOURCE', reviewed_policy_id='ANSWER@' + pin['commit'],
        policy_source_sha256={name: sha256(data).hexdigest() for name, data in inputs.items()},
        course_coverage='DEPENDENCY_INCOMPLETE' if missing else 'SOURCE_VERIFIED_NOT_ACTIVATED',
        course_coverage_note='仅核验来源并生成原文预览；未运行教学检查脚本或验证实际教学输出，未启用自动生成。',
        answer_generation_allowed_by_course=False,
        real_deepseek_uploaded=False, real_delivery_verified=False,
        files=[{'relative_path': 'teaching-input.md', 'role': 'committed_teaching_input',
            'source_path': str(teaching_input), 'source_sha256': digest, 'snapshot_path': str(teaching_input),
            'snapshot_sha256': digest, 'bytes': len(content)}], workflow_teaching_paths=[str(teaching_input)],
        deferred_references=[{'relative_path': 'other question types / conditional course lookup',
            'reason': '未提供，不能声称已读取；课程依据问题需另行检索并带原文证据。'}])
    if for_generation:
        manifest.update(format_version=3, course_coverage='TASK_CHECKS_REQUIRED',
            course_coverage_note='固定原文及依赖已核验；每题必须执行原脚本定位及成稿检查，异常转人工。不是实际网页或交付验收。',
            answer_generation_allowed_by_course=True,
            required_task_checks=['SOURCE', 'DRAFT'])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    verify_answer_bundle(manifest_path)
    return manifest_path


def verify_answer_bundle(manifest_path, *, check_sources=True):
    try:
        return _verify_answer_bundle(manifest_path, check_sources=check_sources)
    except TeachingSourceError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        raise TeachingSourceError('ANSWER_MANIFEST_INVALID') from None


def _verify_answer_bundle(manifest_path, *, check_sources):
    raw_path = Path(manifest_path).absolute()
    _reject_redirects(raw_path)
    path = _file(raw_path.parent.resolve(strict=True), raw_path.name)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    pin = source_pin()
    provenance = manifest['source_repository']
    if (manifest.get('format_version') not in (2, 3) or provenance['url'] != pin['repository_url']
            or provenance['commit'] != pin['commit'] or manifest['reviewed_policy_id'] != 'ANSWER@' + pin['commit']
            or manifest['policy_review_status'] != 'PINNED_ANSWER_SOURCE'):
        raise TeachingSourceError('ANSWER_SOURCE_PIN_MISMATCH')
    route, selected, required = _selection(manifest['question_type'], manifest['request_kind'])
    active = manifest['format_version'] == 3
    if active and (manifest['question_type'] not in ('阅读理解', '完形填空')
                   or manifest['request_kind'] != 'answer'
                   or manifest.get('required_task_checks') != ['SOURCE', 'DRAFT']):
        raise TeachingSourceError('ANSWER_TASK_CHECK_CONTRACT_CHANGED')
    root = _repository(provenance['root'], pin) if check_sources else None
    missing = manifest['missing_dependencies']
    if not isinstance(missing, list) or len(missing) != len(set(missing)) or any(n not in required for n in missing):
        raise TeachingSourceError('ANSWER_DEPENDENCY_STATUS_INVALID')
    if active and missing:
        raise TeachingSourceError('ANSWER_REQUIRED_DEPENDENCIES_MISSING: ' + ', '.join(missing))
    names = (*selected, *(name for name in required if name not in missing))
    if ([item['relative_path'] for item in manifest['source_files']] != list(names)
            or manifest['required_dependencies'] != list(required) or manifest['skill'] != route.skill):
        raise TeachingSourceError('ANSWER_SOURCE_ALLOWLIST_CHANGED')
    if root is not None:
        tracked = set(_git(root, 'ls-tree', '-r', '--name-only', pin['commit']).decode('utf-8').splitlines())
        if missing != [name for name in required if name not in tracked]:
            raise TeachingSourceError('ANSWER_DEPENDENCY_STATUS_CHANGED')
        if Path(manifest['skill_root']).resolve(strict=True) != root / route.skill:
            raise TeachingSourceError('ANSWER_SKILL_ROOT_CHANGED')
    documents = {}
    hashes = {}
    for item in manifest['source_files']:
        name = item['relative_path']
        data = _file(path.parent, 'sources/' + name).read_bytes()
        if sha256(data).hexdigest() != item['sha256'] or len(data) != item['bytes']:
            raise TeachingSourceError('ANSWER_SOURCE_SNAPSHOT_CHANGED')
        if root is not None and data != _content(root, pin['commit'], name):
            raise TeachingSourceError('ANSWER_SOURCE_COMMIT_CONTENT_MISMATCH')
        hashes[name] = item['sha256']
        if name in selected:
            documents[name] = data
    expected = _input(pin, manifest['question_type'], documents)
    teaching_input = _file(path.parent, 'teaching-input.md')
    if teaching_input.read_bytes() != expected or len(expected) > MAX_BYTES:
        raise TeachingSourceError('ANSWER_TEACHING_INPUT_CHANGED')
    digest = sha256(expected).hexdigest()
    expected_file = {'relative_path': 'teaching-input.md', 'role': 'committed_teaching_input',
        'source_path': str(teaching_input), 'source_sha256': digest, 'snapshot_path': str(teaching_input),
        'snapshot_sha256': digest, 'bytes': len(expected)}
    if (manifest['files'] != [expected_file] or manifest['workflow_teaching_paths'] != [str(teaching_input)]
            or manifest['policy_source_sha256'] != hashes
            or manifest['answer_generation_allowed_by_course'] is not active
            or manifest['real_deepseek_uploaded'] is not False or manifest['real_delivery_verified'] is not False
            or manifest['course_coverage'] != ('TASK_CHECKS_REQUIRED' if active else
                                             'DEPENDENCY_INCOMPLETE' if missing else 'SOURCE_VERIFIED_NOT_ACTIVATED')):
        raise TeachingSourceError('ANSWER_MANIFEST_PERMISSION_OR_INPUT_CHANGED')
    if active and path.parent.name != 'answer-' + _bundle_key(pin, manifest['question_type'],
            manifest['request_kind'], hashes, for_generation=True)[:24]:
        raise TeachingSourceError('ANSWER_BUILD_MODE_CACHE_MISMATCH')
    return manifest
