"""Small candidate-content boundary; providers never compare or confirm a question."""
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from .reference_fetch import Budget, LookupFailure, MAX_BYTES
from .storage import now


class ReferenceProvider(Protocol):
    def capture(self, locator: str, budget: Budget) -> dict:
        """Return source, url, content, retrieved_time and hash, plus access evidence."""


class LocalProvider:
    """Only files the owner has placed in the explicitly approved local directory."""
    def __init__(self, root):
        self.root = Path(root)

    def read(self, locator):
        from .reference_lookup import approved_path
        path = approved_path(self.root, locator)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('Local candidate must be a bounded file in the approved directory')
        content = path.read_bytes()
        if len(content) > MAX_BYTES:
            raise ValueError('Local candidate grew beyond the read limit')
        return path, content

    def capture(self, locator, budget):
        budget.remaining()
        path, content = self.read(locator)
        try:
            text = content.decode('utf-8')
        except UnicodeError:
            raise ValueError('Local candidate must have confirmed UTF-8 encoding') from None
        budget.remaining()
        return {'source': 'APPROVED_LOCAL_FILE', 'url': 'https://fixtures.invalid/' + path.name,
            'content': text, 'retrieved_time': now(), 'hash': sha256(content).hexdigest()}


class HttpProvider:
    """Reuse the admitted, DNS-pinned and deadline-bounded HTTP implementation."""
    def __init__(self, config, fetcher):
        self.config, self.fetcher = config, fetcher

    def capture(self, locator, budget):
        if not self.config.enabled or not self.config.network_enabled:
            raise LookupFailure('ACCESS_RESTRICTED', 'NETWORK_DISABLED')
        source = self.config.source(locator)
        source.require(locator)
        if source.retention_seconds <= 0:
            raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_STORAGE_POLICY_REQUIRED')
        result, text = self.fetcher.page(locator, source, budget)
        budget.remaining()
        return {'source': source.domain, 'url': result.url, 'content': text,
            'retrieved_time': now(), 'hash': sha256(result.body).hexdigest(),
            'robots_sha256': result.headers.get('x-reference-robots-sha256'),
            'robots_checked_at': result.headers.get('x-reference-robots-checked-at'),
            'terms_review': {'url': source.terms_url, 'checked_at': source.checked_at}}
