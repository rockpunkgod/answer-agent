"""Authoritative raw source DB, separate from Helpdesk's business projection.

Business messages must reference these message_id values. WAL allows a dispatcher
and media worker to read while the single short collector transaction commits.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Callable
from uuid import uuid4

from .message_sources import MessageBatch, NormalizedMessage, SyncMode, business_zone, utc_now, normalize_sent_time


class CursorConflict(RuntimeError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class PersistResult:
    inserted_ids: tuple[str, ...]
    duplicate_count: int
    conflict_count: int = 0


class CollectorStore:
    def __init__(self, path: str | Path = "data/messages.db"):
        from .storage import ensure_local_database
        ensure_local_database(path)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
CREATE TABLE IF NOT EXISTS messages (
 message_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_message_id TEXT,
 source_seq TEXT, room_id TEXT NOT NULL, room_name TEXT, sender_id TEXT NOT NULL,
 sender_display_name TEXT, message_type TEXT NOT NULL, raw_content TEXT,
 normalized_text TEXT, media_id TEXT, local_media_path TEXT, media_hash TEXT,
 reply_to_message_id TEXT, quoted_message_id TEXT, sent_at_raw TEXT,
 sent_at_utc TEXT, sent_at_local TEXT, ingested_at TEXT NOT NULL, updated_at TEXT,
 processed_at TEXT, answered_at TEXT, raw_payload TEXT NOT NULL,
 source_confidence TEXT, time_confidence TEXT, parse_status TEXT, fingerprint TEXT,
 business_timezone TEXT, source_name TEXT NOT NULL,
 UNIQUE(source_type, source_message_id));
CREATE INDEX IF NOT EXISTS messages_seq ON messages(source_seq);
CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id);
CREATE INDEX IF NOT EXISTS messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS messages_sent ON messages(sent_at_local);
CREATE INDEX IF NOT EXISTS messages_type ON messages(message_type);
CREATE INDEX IF NOT EXISTS messages_fingerprint ON messages(fingerprint);
CREATE TABLE IF NOT EXISTS rooms (source_type TEXT, room_id TEXT, room_name TEXT,
 PRIMARY KEY(source_type,room_id));
CREATE TABLE IF NOT EXISTS students (source_type TEXT, sender_id TEXT, display_name TEXT,
 PRIMARY KEY(source_type,sender_id));
CREATE TABLE IF NOT EXISTS sync_state (
 source_name TEXT, mode TEXT CHECK(mode IN ('LIVE','BACKFILL')), cursor TEXT,
 last_seen_message_id TEXT, last_success_at TEXT, last_attempt_at TEXT,
 last_error TEXT, status TEXT NOT NULL DEFAULT 'READY',
 received_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source_name,mode));
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, message_id TEXT NOT NULL
 REFERENCES messages(message_id), source_name TEXT, mode TEXT NOT NULL,
 auto_reply_allowed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 processed_at TEXT, last_error TEXT, UNIQUE(event_type,message_id));
CREATE TABLE IF NOT EXISTS message_conflicts (
 conflict_id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(message_id),
 kind TEXT NOT NULL, observed_payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message_media (
 id INTEGER PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(message_id),
 media_id TEXT, original_filename TEXT, media_type TEXT, size INTEGER,
 hash TEXT, local_path TEXT, downloaded_at TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
 last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(message_id,media_id));
CREATE TABLE IF NOT EXISTS sync_batches (
 id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, mode TEXT NOT NULL,
 committed_at TEXT NOT NULL, received_count INTEGER NOT NULL,
 duplicate_count INTEGER NOT NULL, conflict_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS live_activation (
 source_name TEXT PRIMARY KEY, activated_at_utc TEXT NOT NULL);
''')

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def get_message(self, message_id: str):
        with self.connect() as db:
            return db.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()

    def get_sync_state(self, source_name: str, mode: SyncMode = SyncMode.LIVE):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sync_state WHERE source_name=? AND mode=?", (source_name, mode)).fetchone()
            return dict(row) if row else {"source_name": source_name, "mode": str(mode), "cursor": None, "status": "READY"}

    def get_live_activation(self, source_name: str):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='live_activation'").fetchone():
                return None
            row = db.execute("SELECT activated_at_utc FROM live_activation WHERE source_name=?", (source_name,)).fetchone()
            return row[0] if row else None

    def activate_live(self, source_name: str, *, activated_at: str | None = None):
        """Persist the first listening boundary; restarting must not reset it.

        This is a receive policy, never a replacement for a student's timestamp.
        Initial seq=0 history remains raw evidence but cannot trigger an ACK.
        """
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError("source name required")
        cutoff, _ = normalize_sent_time(activated_at if activated_at is not None else utc_now())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO live_activation VALUES(?,?)", (source_name, cutoff))
            cutoff = db.execute("SELECT activated_at_utc FROM live_activation WHERE source_name=?", (source_name,)).fetchone()[0]
            db.execute("""UPDATE events SET auto_reply_allowed=0,
                last_error='PRE_ACTIVATION_HISTORY_ACK_FORBIDDEN'
                WHERE source_name=? AND mode='LIVE' AND message_id IN
                (SELECT message_id FROM messages WHERE source_name=? AND
                 (sent_at_utc IS NULL OR julianday(sent_at_utc)<julianday(?)))""", (source_name, source_name, cutoff))
        return cutoff

    def record_attempt(self, source_name: str, mode: SyncMode):
        with self.connect() as db:
            db.execute("INSERT INTO sync_state(source_name,mode,last_attempt_at) VALUES(?,?,?) ON CONFLICT(source_name,mode) DO UPDATE SET last_attempt_at=excluded.last_attempt_at", (source_name, mode, utc_now()))

    def record_error(self, source_name: str, mode: SyncMode, error: str, *, review: bool = False):
        with self.connect() as db:
            db.execute("INSERT INTO sync_state(source_name,mode,last_error,status) VALUES(?,?,?,?) ON CONFLICT(source_name,mode) DO UPDATE SET last_error=CASE WHEN sync_state.status='NEEDS_REVIEW' THEN sync_state.last_error ELSE excluded.last_error END,status=CASE WHEN sync_state.status='NEEDS_REVIEW' THEN sync_state.status ELSE excluded.status END", (source_name, mode, error, "NEEDS_REVIEW" if review else "ERROR"))

    def persist_batch(self, source_name: str, mode: SyncMode, batch: MessageBatch,
                      expected_cursor: str | None, *, before_cursor: Callable | None = None) -> PersistResult:
        mode = SyncMode(mode)
        inserted, duplicates, conflicts = [], 0, 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT cursor,status FROM sync_state WHERE source_name=? AND mode=?", (source_name, mode)).fetchone()
            if (current["cursor"] if current else None) != expected_cursor:
                raise CursorConflict("another sync advanced the cursor; refetch from committed position")
            if current and current["status"] == "NEEDS_REVIEW":
                raise CursorConflict("cursor requires explicit operator review")
            activation = db.execute("SELECT activated_at_utc FROM live_activation WHERE source_name=?", (source_name,)).fetchone()
            for message in batch.messages:
                fields = asdict(message)
                fields["raw_payload"] = json.dumps(message.raw_payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
                fields["sent_at_raw"] = json.dumps(message.sent_at_raw, ensure_ascii=False, allow_nan=False)
                fields["source_seq"] = str(message.source_seq) if message.source_seq is not None else None
                fields["source_name"] = source_name
                existing = db.execute("SELECT * FROM messages WHERE message_id=? OR (source_type=? AND source_message_id=?)", (message.message_id, message.source_type, message.source_message_id)).fetchone()
                if existing:
                    # Never overwrite the original if a stable ID reappears with changed content.
                    immutable = ("source_type", "source_message_id", "room_id", "sender_id", "message_type", "raw_content", "raw_payload", "sent_at_raw", "media_id", "reply_to_message_id", "quoted_message_id")
                    if any(existing[k] != fields[k] for k in immutable):
                        conflicts += 1
                        db.execute("INSERT INTO message_conflicts VALUES(?,?,?,?,?)", (str(uuid4()), existing["message_id"], "stable_id_payload_conflict", json.dumps(fields, ensure_ascii=False), utc_now()))
                        db.execute("UPDATE events SET auto_reply_allowed=0,last_error='stable_id_payload_conflict: review required' WHERE message_id=? AND processed_at IS NULL", (existing["message_id"],))
                    duplicates += 1
                    continue
                collision = None
                if message.fingerprint:
                    collision = db.execute("SELECT message_id FROM messages WHERE source_type=? AND fingerprint=? LIMIT 1", (message.source_type, message.fingerprint)).fetchone()
                db.execute(f"INSERT INTO messages ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", tuple(fields.values()))
                inserted.append(message.message_id)
                if collision:
                    conflicts += 1
                    db.execute("INSERT INTO message_conflicts VALUES(?,?,?,?,?)", (str(uuid4()), message.message_id, "fingerprint_collision", json.dumps({"other_message_id": collision[0], "fingerprint": message.fingerprint}), utc_now()))
                    db.execute("UPDATE events SET auto_reply_allowed=0,last_error='fingerprint_collision: review required' WHERE message_id=? AND processed_at IS NULL", (collision[0],))
                db.execute("INSERT INTO rooms VALUES(?,?,?) ON CONFLICT(source_type,room_id) DO UPDATE SET room_name=excluded.room_name", (message.source_type, message.room_id, message.room_name))
                db.execute("INSERT INTO students VALUES(?,?,?) ON CONFLICT(source_type,sender_id) DO UPDATE SET display_name=excluded.display_name", (message.source_type, message.sender_id, message.sender_display_name))
                before_activation = bool(activation and (not message.sent_at_utc or
                    datetime.fromisoformat(message.sent_at_utc) < datetime.fromisoformat(activation[0])))
                allowed = mode == SyncMode.LIVE and not collision and not before_activation and message.time_confidence != 'low' and message.source_confidence != 'low'
                db.execute("INSERT INTO events(event_id,event_type,message_id,source_name,mode,auto_reply_allowed,created_at,last_error) VALUES(?,?,?,?,?,?,?,?)", (str(uuid4()), "message_received", message.message_id, source_name, mode, int(allowed), utc_now(), "PRE_ACTIVATION_HISTORY_ACK_FORBIDDEN" if before_activation and mode == SyncMode.LIVE else None))
                if message.media_id or message.message_type in {"image", "file", "voice", "video"}:
                    db.execute("INSERT INTO message_media(message_id,media_id,media_type) VALUES(?,?,?)", (message.message_id, message.media_id, message.message_type))
            if before_cursor:
                before_cursor(db)
            db.execute("INSERT INTO sync_batches(source_name,mode,committed_at,received_count,duplicate_count,conflict_count) VALUES(?,?,?,?,?,?)", (source_name, mode, utc_now(), len(inserted), duplicates, conflicts))
            db.execute('''INSERT INTO sync_state(source_name,mode,cursor,last_seen_message_id,last_success_at,last_error,status,received_count,duplicate_count)
VALUES(?,?,?,?,?,NULL,'READY',?,?) ON CONFLICT(source_name,mode) DO UPDATE SET
cursor=excluded.cursor,last_seen_message_id=COALESCE(excluded.last_seen_message_id,sync_state.last_seen_message_id),
last_success_at=excluded.last_success_at,last_error=NULL,status='READY',
received_count=sync_state.received_count+excluded.received_count,duplicate_count=sync_state.duplicate_count+excluded.duplicate_count''', (source_name, mode, batch.next_cursor, (batch.messages[-1].source_message_id or batch.messages[-1].message_id) if batch.messages else None, utc_now(), len(inserted), duplicates))
        return PersistResult(tuple(inserted), duplicates, conflicts)

    def acknowledge_cursor_review(self, source_name: str, mode: SyncMode, cursor: str | None):
        """Explicit operator recovery, never automatically called by sync engine."""
        with self.connect() as db:
            db.execute("UPDATE sync_state SET cursor=?,status='READY',last_error=NULL WHERE source_name=? AND mode=? AND status='NEEDS_REVIEW'", (cursor, source_name, mode))

    def monitoring(self, source_name: str, mode: SyncMode = SyncMode.LIVE, *,
                   business_timezone: str = "Asia/Shanghai", stale_after_seconds: int = 300,
                   now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("monitoring now requires timezone")
        zone = business_zone(business_timezone)
        day = now.astimezone(zone).date()
        state = self.get_sync_state(source_name, mode)
        with self.connect() as db:
            stats = db.execute("SELECT (SELECT ingested_at FROM messages WHERE source_name=? ORDER BY julianday(ingested_at) DESC LIMIT 1) last_message_received_at, COALESCE(SUM(parse_status!='parsed'),0) messages_pending_parse, COALESCE(SUM(time_confidence!='high'),0) messages_pending_time_verification FROM messages WHERE source_name=?", (source_name, source_name)).fetchone()
            media_failures = db.execute("SELECT COUNT(*) FROM message_media mm JOIN messages m USING(message_id) WHERE m.source_name=? AND mm.status='FAILED'", (source_name,)).fetchone()[0]
            batches = db.execute("SELECT committed_at,received_count,duplicate_count FROM sync_batches WHERE source_name=? AND mode=?", (source_name, mode)).fetchall()
            latest = db.execute("SELECT ingested_at,sent_at_utc FROM messages WHERE source_name=? AND sent_at_utc IS NOT NULL ORDER BY julianday(sent_at_utc) DESC LIMIT 1", (source_name,)).fetchone()
        today = [row for row in batches if datetime.fromisoformat(row[0]).astimezone(zone).date() == day]
        last_success = state.get("last_success_at")
        age = max(0, (now - datetime.fromisoformat(last_success)).total_seconds()) if last_success else None
        lag = max(0, (datetime.fromisoformat(latest["ingested_at"]) - datetime.fromisoformat(latest["sent_at_utc"])).total_seconds()) if latest else None
        return state | dict(stats) | {
            "current_cursor": state["cursor"], "last_successful_sync_at": last_success,
            "live_activation_at": self.get_live_activation(source_name),
            "sync_lag_seconds": lag, "sync_lag_basis": "latest_known_message_ingest_minus_sent",
            "seconds_since_successful_sync": age,
            "messages_received_today": sum(row[1] for row in today),
            "duplicate_messages_today": sum(row[2] for row in today),
            "media_download_failures": media_failures,
            "sync_health": "NEEDS_REVIEW" if state.get("status") == "NEEDS_REVIEW" else "FAILED" if state.get("last_error") else "NEVER_SYNCED" if age is None else ("STALE" if age > stale_after_seconds else "HEALTHY"),
        }
