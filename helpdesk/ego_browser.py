"""Local ego-lite bridge. Own receipts, never fabricated Windows-MCP evidence."""
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from .browser_adapter import BrowserPaused
from .locking import resource_lock


def ego_executable():
    found = shutil.which('ego-browser')
    candidate = Path(found) if found else Path.home() / '.local/share/ego/active_version_dir/ego-browser.exe'
    if not candidate.is_file():
        raise BrowserPaused('EGO_NOT_INSTALLED')
    return candidate.resolve()


def page_control_directory():
    # Shared by different request/evidence directories in this local installation.
    return Path.home() / '.local/share/answer-agent/ego-controls'


class EgoDeepSeek:
    """Use one existing managed space/page; no account, profile or WeCom access.

    This is a browser primitive, like PlaywrightDeepSeek. Its captured reply is
    still subject to business version, ANSWER and delivery checks by the caller.
    """
    def __init__(self, space_id, evidence_dir, *, page='p1', allowed_files=(), executable=None):
        if type(space_id) is not int or space_id < 1 or not re.fullmatch(r'p[1-9][0-9]*', page):
            raise ValueError('An existing ego space and page are required')
        self.space_id, self.page = space_id, page
        self.directory = Path(evidence_dir).resolve()
        self.allowed_files = frozenset(Path(p).resolve(strict=True) for p in allowed_files)
        self.executable = Path(executable).resolve() if executable else ego_executable()

    def submit(self, request_id, session_url, prompt, *, attachments=(), response_token=None,
               capture_only=False, timeout_ms=180_000, new_session=False):
        if (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id)
                or not isinstance(session_url, str)
                or type(new_session) is not bool
                or not (session_url == 'https://chat.deepseek.com/' if new_session else
                        re.fullmatch(r'https://chat\.deepseek\.com/a/chat/s/[A-Za-z0-9-]+', session_url))
                or response_token is not None and (not isinstance(response_token, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', response_token))
                or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000
                or type(timeout_ms) is not int or not 100 <= timeout_ms <= 300_000):
            raise ValueError('Invalid bounded DeepSeek request')
        files = []
        for value in attachments:
            p = Path(value).resolve(strict=True)
            if p not in self.allowed_files or not p.is_file() or p.stat().st_size > 20_000_000:
                raise BrowserPaused('ATTACHMENT_NOT_ALLOWED')
            files.append({'path': str(p), 'name': p.name, 'sha256': sha256(p.read_bytes()).hexdigest()})
        if len({f['name'] for f in files}) != len(files):
            raise BrowserPaused('ATTACHMENT_NAME_COLLISION')
        request = {'space_id': self.space_id, 'page': self.page, 'session_url': session_url,
                   'prompt': prompt, 'files': files, 'response_token': response_token}
        if new_session:
            request['new_session'] = True
        fingerprint = sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        state_path = self.directory / (request_id + '.state.json')
        output_path = self.directory / (request_id + '.answer.json')
        control = page_control_directory() / f'space-{self.space_id}-{self.page}'
        owner_path = control.with_suffix('.owner')
        with resource_lock(control.with_suffix('.lock'), timeout=5), \
                resource_lock(self.directory / (request_id + '.lock'), timeout=5):
            # Old marker protocol is recovery-only; never create a new marked request.
            if response_token is not None and not state_path.exists():
                raise BrowserPaused('LEGACY_PROTOCOL_RECOVERY_ONLY')
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if state.get('fingerprint') != fingerprint:
                    raise BrowserPaused('REQUEST_CHANGED')
                if state.get('phase') == 'CAPTURED':
                    raw = output_path.read_bytes()
                    if sha256(raw).hexdigest() != state.get('output_sha256'):
                        raise BrowserPaused('CAPTURE_CHANGED')
                    if owner_path.exists() and owner_path.read_text(encoding='utf-8') == str(state_path):
                        owner_path.unlink()
                    return json.loads(raw)
                if not capture_only:
                    raise BrowserPaused('PRIOR_ATTEMPT_REQUIRES_READONLY_CAPTURE')
                if state.get('phase') not in ('SUBMISSION_INTENT', 'SUBMITTED'):
                    raise BrowserPaused('PREPARATION_INCOMPLETE_MANUAL_CHECK')
            elif capture_only:
                raise BrowserPaused('NO_PRIOR_ATTEMPT')
            else:
                if owner_path.exists() and owner_path.read_text(encoding='utf-8') != str(state_path):
                    raise BrowserPaused('EGO_PAGE_HAS_UNRESOLVED_REQUEST')
                state_path.write_text(json.dumps({'fingerprint': fingerprint, 'phase': 'PREPARING'}), encoding='utf-8')
            if owner_path.exists() and owner_path.read_text(encoding='utf-8') != str(state_path):
                raise BrowserPaused('EGO_PAGE_HAS_UNRESOLVED_REQUEST')
            owner_path.write_text(str(state_path), encoding='utf-8')
            request.update(fingerprint=fingerprint, state_path=str(state_path), output_path=str(output_path),
                           capture_only=capture_only, timeout_ms=timeout_ms)
            worker = Path(__file__).with_name('ego_deepseek.mjs').resolve()
            script = self.directory / ('round-' + uuid4().hex + '.mjs')
            script.write_text('const { run } = await import(' + json.dumps(worker.as_uri()) + ');\n'
                              + 'await run(' + json.dumps(request, ensure_ascii=False) + ');\n', encoding='utf-8')
            try:
                completed = subprocess.run([str(self.executable), 'nodejs', '-e',
                    "await import('" + script.as_uri() + "')"], capture_output=True,
                    timeout=timeout_ms / 1000 + 120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.TimeoutExpired):
                raise BrowserPaused('EGO_OUTCOME_UNKNOWN_USE_READONLY_CAPTURE') from None
            # CLI output may include private URLs or page data: private files only.
            script.with_suffix('.stdout').write_bytes(completed.stdout)
            script.with_suffix('.stderr').write_bytes(completed.stderr)
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if completed.returncode or state.get('phase') != 'CAPTURED':
                raise BrowserPaused('EGO_NOT_CAPTURED_USE_READONLY_CAPTURE')
            raw = output_path.read_bytes()
            if sha256(raw).hexdigest() != state.get('output_sha256'):
                raise BrowserPaused('CAPTURE_CHANGED')
            owner_path.unlink(missing_ok=True)
            return json.loads(raw)
