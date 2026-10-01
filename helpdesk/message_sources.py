"""Transport-independent, immutable source records. No business decisions here."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import StrEnum
import json
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def business_zone(name: str = "Asia/Shanghai"):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        raise


def normalize_sent_time(raw: Any, *, unit: str = "iso", business_timezone: str = "Asia/Shanghai") -> tuple[str, str]:
    """Unit is explicit; callers must verify an official transport's time contract."""
    if unit in {"seconds", "milliseconds"}:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("epoch time requires a numeric value")
        dt = datetime.fromtimestamp(raw / (1000 if unit == "milliseconds" else 1), timezone.utc)
    elif unit == "iso":
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("sent time requires full date and explicit timezone")
    else:
        raise ValueError("unsupported time unit")
    return dt.astimezone(timezone.utc).isoformat(), dt.astimezone(business_zone(business_timezone)).isoformat()


class SyncMode(StrEnum):
    LIVE = "LIVE"
    BACKFILL = "BACKFILL"


class CursorExpired(RuntimeError):
    """Operator review required. Never silently restart an expired cursor."""


@dataclass(frozen=True)
class NormalizedMessage:
    source_type: str
    room_id: str
    sender_id: str
    message_id: str = field(default_factory=lambda: str(uuid4()))
    source_message_id: str | None = None
    source_seq: str | int | None = None
    room_name: str = ""
    sender_display_name: str = ""
    message_type: str = "text"
    raw_content: str = ""
    normalized_text: str = ""
    media_id: str | None = None
    local_media_path: str | None = None
    media_hash: str | None = None
    reply_to_message_id: str | None = None
    quoted_message_id: str | None = None
    sent_at_raw: Any = None
    sent_at_utc: str | None = None
    sent_at_local: str | None = None
    ingested_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    processed_at: str | None = None
    answered_at: str | None = None
    raw_payload: Any = field(default_factory=dict)
    source_confidence: str = "high"
    time_confidence: str = "high"
    parse_status: str = "parsed"
    fingerprint: str | None = None
    business_timezone: str = "Asia/Shanghai"

    def __post_init__(self):
        if self.source_type not in {"wecom_archive", "wecom_api", "windows_gui"}:
            raise ValueError("unknown source_type")
        if not all(isinstance(v, str) and v.strip() for v in (self.message_id, self.room_id, self.sender_id)):
            raise ValueError("message_id, room_id and sender_id are required")
        if self.source_type != "windows_gui" and not self.source_message_id:
            raise ValueError("official source requires verified stable source_message_id")
        if self.source_message_id is not None and (not isinstance(self.source_message_id, str) or not self.source_message_id.strip()):
            raise ValueError("source_message_id must be a nonempty string")
        if self.message_type not in {"text", "image", "file", "voice", "video", "link", "quote", "other"}:
            raise ValueError("unknown message_type")
        if self.source_seq is not None and (isinstance(self.source_seq, bool) or not isinstance(self.source_seq, (str, int))):
            raise ValueError("source_seq must be an integer or opaque string")
        for value in (self.room_name, self.sender_display_name, self.raw_content, self.normalized_text, self.parse_status):
            if not isinstance(value, str):
                raise ValueError("message text and parse status must be strings")
        if self.source_confidence not in {"high", "medium", "low"} or self.time_confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid confidence")
        json.dumps(self.raw_payload, ensure_ascii=False, allow_nan=False)
        json.dumps(self.sent_at_raw, ensure_ascii=False, allow_nan=False)
        for value in (self.ingested_at, self.updated_at, self.processed_at, self.answered_at):
            if value is not None:
                normalize_sent_time(value)
        if (self.sent_at_utc is None) != (self.sent_at_local is None):
            raise ValueError("both normalized sent timestamps must be supplied together")
        if self.sent_at_utc is None:
            object.__setattr__(self, "time_confidence", "low")
            object.__setattr__(self, "parse_status", "pending_time_verification")
        else:
            utc, local = normalize_sent_time(self.sent_at_utc, business_timezone=self.business_timezone)
            supplied_utc, supplied_local = normalize_sent_time(self.sent_at_local, business_timezone=self.business_timezone)
            if utc != supplied_utc:
                raise ValueError("sent timestamps describe different instants")
            object.__setattr__(self, "sent_at_utc", utc)
            object.__setattr__(self, "sent_at_local", supplied_local)


@dataclass(frozen=True)
class MessageBatch:
    messages: tuple[NormalizedMessage, ...]
    next_cursor: str | None
    has_more: bool = False

    def __post_init__(self):
        if not isinstance(self.has_more, bool) or (self.next_cursor is not None and not isinstance(self.next_cursor, str)):
            raise ValueError("invalid pagination metadata")
        if not all(isinstance(m, NormalizedMessage) for m in self.messages):
            raise ValueError("batch requires normalized messages")
        object.__setattr__(self, "messages", tuple(self.messages))


class MessageSource(Protocol):
    source_name: str

    def fetch_page(self, cursor: str | None, mode: SyncMode, page_size: int) -> MessageBatch: ...
