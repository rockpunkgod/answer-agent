"""Crash-resumable archive media tasks; metadata already exists before network IO."""
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import time

from .collector_storage import CollectorStore
from .locking import resource_lock
from .message_sources import utc_now
from .wecom_archive import ArchiveAuthorization, ArchiveProtocolError, ArchiveTransport, MediaChunk


class ArchiveMediaWorker:
    def __init__(self, store: CollectorStore, transport: ArchiveTransport,
                 authorization: ArchiveAuthorization, *, root: str | Path = "data/media",
                 source_name: str = "wecom_archive", max_bytes: int = 100 * 1024 * 1024,
                 max_chunks: int = 10000, min_call_interval: float = 0.01):
        authorization.require()
        if max_bytes <= 0 or max_chunks <= 0 or not math.isfinite(min_call_interval) or min_call_interval < 0.0024:
            raise ValueError("positive media limits and interval respecting 25000 calls/minute required")
        self.store, self.transport, self.authorization = store, transport, authorization
        self.root, self.source_name = Path(root).resolve(), source_name
        self.max_bytes, self.max_chunks = max_bytes, max_chunks
        self.min_call_interval, self._last_call = min_call_interval, 0.0
        self.root.mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS archive_media_progress (
                media_task_id INTEGER PRIMARY KEY REFERENCES message_media(id),
                indexbuf TEXT NOT NULL DEFAULT '', confirmed_bytes INTEGER NOT NULL DEFAULT 0,
                chunks INTEGER NOT NULL DEFAULT 0)''')

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("media path escapes configured root")
        return resolved

    def run_pending(self, limit: int = 20) -> dict:
        self.authorization.require()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("worker limit must be 1..1000")
        results = {"downloaded": 0, "failed": 0}
        # OS releases this lock on process death; partial-file checkpoints survive.
        with resource_lock(self.root / ".archive-media.lock", timeout=1):
            with self.store.connect() as db:
                ids = [r[0] for r in db.execute('''SELECT mm.id FROM message_media mm
                    JOIN messages m ON m.message_id=mm.message_id
                    WHERE m.source_type='wecom_archive' AND m.source_name=?
                    AND mm.status IN ('PENDING','FAILED','DOWNLOADING') ORDER BY mm.id LIMIT ?''',
                    (self.source_name, limit))]
            for task_id in ids:
                try:
                    self._download(task_id)
                    results["downloaded"] += 1
                except Exception as exc:
                    # Avoid payloads, tokens, SDK ids or key text in diagnostics.
                    with self.store.connect() as db:
                        db.execute("UPDATE message_media SET status='FAILED',last_error=? WHERE id=?",
                                   ("media download failed: " + type(exc).__name__, task_id))
                    results["failed"] += 1
        return results

    def _download(self, task_id: int):
        with self.store.connect() as db:
            row = db.execute('''SELECT mm.*,m.raw_payload,m.sent_at_local,m.ingested_at
                FROM message_media mm JOIN messages m ON m.message_id=mm.message_id
                WHERE mm.id=? AND m.source_name=? AND m.source_type='wecom_archive' ''',
                (task_id, self.source_name)).fetchone()
            if row is None:
                raise ValueError("media task outside source scope")
            row = dict(row)
            db.execute("UPDATE message_media SET status='DOWNLOADING',attempts=attempts+1,last_error=NULL WHERE id=?", (task_id,))
            db.execute("INSERT OR IGNORE INTO archive_media_progress(media_task_id) VALUES(?)", (task_id,))
            progress = dict(db.execute("SELECT * FROM archive_media_progress WHERE media_task_id=?", (task_id,)).fetchone())
        payload = json.loads(row["raw_payload"])
        metadata = payload.get(payload.get("msgtype"), {})
        media_id = row["media_id"]
        if not isinstance(media_id, str) or not media_id or not isinstance(metadata, dict):
            raise ValueError("missing official sdkfileid")
        expected_size = metadata.get("filesize", metadata.get("voice_size"))
        expected_md5 = metadata.get("md5sum")
        filename = metadata.get("filename", "")
        if expected_size is not None and (type(expected_size) is not int or not 0 <= expected_size <= self.max_bytes):
            raise ValueError("declared media size exceeds configured limit")
        if expected_md5 is not None and (not isinstance(expected_md5, str) or len(expected_md5) != 32 or any(c not in "0123456789abcdefABCDEF" for c in expected_md5)):
            raise ValueError("invalid official media checksum")
        if not isinstance(filename, str):
            raise ValueError("invalid original filename")
        with self.store.connect() as db:
            db.execute("UPDATE message_media SET original_filename=?,size=? WHERE id=?", (filename, expected_size, task_id))
            cached = db.execute('''SELECT mm.local_path,mm.hash,mm.size FROM message_media mm
                JOIN messages m ON m.message_id=mm.message_id WHERE m.source_name=?
                AND m.source_type='wecom_archive' AND mm.media_id=? AND mm.status='DOWNLOADED'
                AND mm.id<>? ORDER BY mm.id LIMIT 1''', (self.source_name, media_id, task_id)).fetchone()
        # sdkfileid + source scope identify a reusable resource. Hash alone never
        # discards a student's independent message or authorizes cross-source reuse.
        if cached:
            try:
                path = self._safe_path(Path(cached["local_path"]))
                if path.is_file() and path.stat().st_size <= self.max_bytes:
                    sha, md5, size = self._digests(path)
                    if sha == cached["hash"] and size == cached["size"] and (expected_size is None or size == expected_size) and (expected_md5 is None or md5 == expected_md5.lower()):
                        self._complete(task_id, row["message_id"], path, sha, size)
                        return
            except (OSError, ValueError, TypeError):
                pass
        date = datetime.fromisoformat(row["sent_at_local"] or row["ingested_at"])
        directory = self._safe_path(self.root / f"{date.year:04d}" / f"{date.month:02d}" / f"{date.day:02d}")
        directory.mkdir(parents=True, exist_ok=True)
        part = self._safe_path(directory / f"task-{task_id}.part")
        confirmed = progress["confirmed_bytes"]
        if not part.exists() or part.stat().st_size < confirmed:
            confirmed, progress = 0, {"indexbuf": "", "confirmed_bytes": 0, "chunks": 0}
            with self.store.connect() as db:
                db.execute("UPDATE archive_media_progress SET indexbuf='',confirmed_bytes=0,chunks=0 WHERE media_task_id=?", (task_id,))
        index, chunks = progress["indexbuf"], progress["chunks"]
        with part.open("r+b" if part.exists() else "w+b") as stream:
            stream.truncate(confirmed)  # discard bytes written before an uncommitted checkpoint
            stream.seek(confirmed)
            while True:
                if chunks >= self.max_chunks:
                    raise ValueError("media exceeded chunk limit")
                delay = self.min_call_interval - (time.monotonic() - self._last_call)
                if delay > 0:
                    time.sleep(delay)
                self._last_call = time.monotonic()
                chunk = self.transport.get_media_data(media_id, index)
                if not isinstance(chunk, MediaChunk) or not isinstance(chunk.data, bytes) or type(chunk.finished) is not bool or not isinstance(chunk.next_index, str):
                    raise ArchiveProtocolError("invalid media transport chunk")
                if len(chunk.data) > 512 * 1024 or confirmed + len(chunk.data) > self.max_bytes:
                    raise ValueError("media exceeds size limit")
                if not chunk.finished and (not chunk.data or not chunk.next_index or chunk.next_index == index):
                    raise ArchiveProtocolError("media cursor did not advance")
                stream.write(chunk.data)
                stream.flush()
                os.fsync(stream.fileno())
                confirmed += len(chunk.data)
                chunks += 1
                if chunk.finished:
                    break
                with self.store.connect() as db:
                    db.execute("UPDATE archive_media_progress SET indexbuf=?,confirmed_bytes=?,chunks=? WHERE media_task_id=?", (chunk.next_index, confirmed, chunks, task_id))
                index = chunk.next_index
        sha, md5, size = self._digests(part)
        if (expected_size is not None and size != expected_size) or (expected_md5 is not None and md5 != expected_md5.lower()):
            # Corrupt bytes must not become a resumable prefix on the next attempt.
            with self.store.connect() as db:
                db.execute("UPDATE archive_media_progress SET indexbuf='',confirmed_bytes=0,chunks=0 WHERE media_task_id=?", (task_id,))
            raise ValueError("official media checksum or size mismatch")
        target = self._safe_path(directory / (sha + ".bin"))
        if target.exists() and self._digests(target)[0] != sha:
            raise ValueError("content-addressed destination is corrupt")
        os.replace(part, target)
        self._complete(task_id, row["message_id"], target, sha, size)

    @staticmethod
    def _digests(path: Path):
        sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk); md5.update(chunk); size += len(chunk)
        return sha.hexdigest(), md5.hexdigest(), size

    def _complete(self, task_id, message_id, path, sha, size):
        with self.store.connect() as db:
            db.execute("UPDATE message_media SET status='DOWNLOADED',local_path=?,hash=?,size=?,downloaded_at=?,last_error=NULL WHERE id=?", (str(path), sha, size, utc_now(), task_id))
            db.execute("UPDATE messages SET local_media_path=?,media_hash=?,updated_at=? WHERE message_id=?", (str(path), sha, utc_now(), message_id))
            db.execute("DELETE FROM archive_media_progress WHERE media_task_id=?", (task_id,))
