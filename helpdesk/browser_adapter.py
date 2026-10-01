"""Configurable Playwright page contract; tested against a local fixture, not live DeepSeek.

Page selectors must be obtained by an operator from the actual authorized session.
No production defaults, login bypass, arbitrary URLs or repeated submissions.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit


class BrowserPaused(Exception):
    pass


@dataclass(frozen=True)
class PageContract:
    origin: str
    session: str
    prompt: str
    submit: str
    file_input: str
    uploaded: str
    answer: str
    completion: str
    login: str
    denied: str
    timeout_ms: int = 5000


class PlaywrightDeepSeek:
    """Own a page in one worker; callers serialize by page/session, never desktop lock."""
    def __init__(self, page, contract: PageContract, allowed_files=()):
        self.page = page
        self.contract = contract
        self.allowed_files = frozenset(Path(p).resolve() for p in allowed_files)
        self.submitted_runs = set()

    def _preflight(self, session_id):
        c = self.contract
        parsed = urlsplit(self.page.url)
        if f"{parsed.scheme}://{parsed.netloc}" != c.origin:
            raise BrowserPaused("ORIGIN_MISMATCH")
        if self.page.locator(c.login).is_visible():
            raise BrowserPaused("LOGIN_EXPIRED")
        if self.page.locator(c.denied).is_visible():
            raise BrowserPaused("ACCESS_DENIED")
        if self.page.locator(c.session).inner_text(timeout=c.timeout_ms).strip() != session_id:
            raise BrowserPaused("SESSION_MISMATCH")

    def submit(self, run_id, session_id, prompt, attachments=()):
        """Run ID must already be durably RUNNING in business DB before calling.

        After any error the caller must pause/reconcile, not retry submit. The in-memory
        guard adds protection within a worker; persisted run state is restart authority.
        """
        from playwright.sync_api import expect, TimeoutError as PlaywrightTimeout
        if run_id in self.submitted_runs:
            raise BrowserPaused("SUBMISSION_ALREADY_ATTEMPTED")
        self._preflight(session_id)
        c = self.contract
        files = tuple(Path(p).resolve() for p in attachments)
        if len({p.name for p in files}) != len(files):
            raise BrowserPaused("ATTACHMENT_NAME_COLLISION")
        manifest = []
        for p in files:
            if p not in self.allowed_files or not p.is_file() or p.stat().st_size > 20_000_000:
                raise BrowserPaused("ATTACHMENT_NOT_ALLOWED")
            manifest.append({"name": p.name, "sha256": sha256(p.read_bytes()).hexdigest(), "session_id": session_id})
        try:
            if files:
                self.page.locator(c.file_input).set_input_files([str(p) for p in files], timeout=c.timeout_ms)
                for item in manifest:
                    expect(self.page.locator(c.uploaded).filter(has_text=item["name"])).to_have_count(1, timeout=c.timeout_ms)
                self._preflight(session_id)
            old_count = self.page.locator(c.answer).count()
            self.page.locator(c.prompt).fill(prompt, timeout=c.timeout_ms)
            self.submitted_runs.add(run_id) # Immediately before the non-idempotent click.
            self.page.locator(c.submit).click(timeout=c.timeout_ms)
            expect(self.page.locator(c.answer)).to_have_count(old_count + 1, timeout=c.timeout_ms)
            response = self.page.locator(c.answer).last
            expect(response.locator(c.completion)).to_be_visible(timeout=c.timeout_ms)
            self._preflight(session_id)
            answer = response.inner_text(timeout=c.timeout_ms).strip()
            if not answer:
                raise BrowserPaused("EMPTY_OUTPUT")
            return {"text": answer, "complete": True, "uploads_confirmed": True,
                    "session_id": session_id, "upload_manifest": manifest, "run_id": run_id,
                    "adapter": "PLAYWRIGHT_PAGE_CONTRACT", "live_deepseek_verified": False}
        except (PlaywrightTimeout, AssertionError) as exc:
            raise BrowserPaused("PAGE_STATE_UNCONFIRMED") from exc
