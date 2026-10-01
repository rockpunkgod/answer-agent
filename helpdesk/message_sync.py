"""Paged incremental collector. Fetching has no business or desktop side effects."""
from dataclasses import dataclass

from .collector_storage import CollectorStore
from .message_sources import CursorExpired, MessageSource, SyncMode


@dataclass(frozen=True)
class SyncResult:
    pages: int
    inserted_count: int
    duplicate_count: int
    conflict_count: int
    cursor: str | None
    has_more: bool


class SyncEngine:
    def __init__(self, store: CollectorStore, source: MessageSource, page_size: int = 1000):
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        self.store, self.source, self.page_size = store, source, page_size

    def sync(self, mode: SyncMode = SyncMode.LIVE, max_pages: int = 1000) -> SyncResult:
        mode = SyncMode(mode)
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        state = self.store.get_sync_state(self.source.source_name, mode)
        if state["status"] == "NEEDS_REVIEW":
            raise CursorExpired("source cursor requires explicit operator review")
        cursor, inserted, duplicates, conflicts = state["cursor"], 0, 0, 0
        for page in range(1, max_pages + 1):
            self.store.record_attempt(self.source.source_name, mode)
            try:
                batch = self.source.fetch_page(cursor, mode, self.page_size)
                if batch.has_more and (batch.next_cursor is None or batch.next_cursor == cursor):
                    raise ValueError("pagination did not advance; refuse incomplete infinite loop")
                result = self.store.persist_batch(self.source.source_name, mode, batch, cursor)
            except Exception as error:
                # Third-party exceptions can contain URLs, tokens or raw payloads.
                self.store.record_error(self.source.source_name, mode, type(error).__name__, review=isinstance(error, CursorExpired))
                raise
            cursor = batch.next_cursor
            inserted += len(result.inserted_ids)
            duplicates += result.duplicate_count
            conflicts += result.conflict_count
            if not batch.has_more:
                return SyncResult(page, inserted, duplicates, conflicts, cursor, False)
        return SyncResult(max_pages, inserted, duplicates, conflicts, cursor, True)
