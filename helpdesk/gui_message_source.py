"""Incremental observation adapter; its provider must perform real MCP reads.

No desktop control, scrolling, OCR, clock-based sent-time guesses or sending.
This module alone does not constitute a running desktop listener.

Provider positions are durable logical message locators within a room, not
screen coordinates or indexes in the current viewport. A provider unable to
prove continuity must stop instead of recycling a position for another message.
"""
from hashlib import sha256
import json
from pathlib import Path
from .message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time


def _message_id(room, position):
    return "gui-position-v1:" + sha256(json.dumps([room, position], ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def same_message_payload(first, second, *, message_id, room_id):
    """Only capture evidence may differ for this adapter's logical identity.

    The first observation remains authoritative in storage. Message content,
    identity, time, attachments and context must still agree exactly; neither a
    text fingerprint nor an arbitrary caller-supplied ID enables this exception.
    """
    keys = {"observation", "item", "sender_role", "is_self", "identity_verified"}
    try:
        before, after = json.loads(first), json.loads(second)
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or set(before) != keys or set(after) != keys
                or not isinstance(before["item"], dict) or not isinstance(after["item"], dict)):
            return False
        position = before["item"].get("position")
        if (not isinstance(position, str) or not position.strip()
                or message_id != _message_id(room_id, position)):
            return False
        before.pop("observation")
        after.pop("observation")
        return json.dumps(before, sort_keys=True, allow_nan=False) == json.dumps(after, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        return False


class GUIMessageSource:
    source_name = "windows_gui"

    def __init__(self, observation_provider, *, expected_room_id, business_timezone="Asia/Shanghai", attachment_root=None):
        self.provider = observation_provider
        self.room = expected_room_id
        self.timezone = business_timezone
        self.attachment_root = Path(attachment_root).resolve(strict=True) if attachment_root is not None else None
        if self.attachment_root is not None and not self.attachment_root.is_dir():
            raise ValueError("Approved attachment root must be a directory")

    def fetch_page(self, cursor, mode, page_size):
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        if mode != SyncMode.LIVE:
            raise ValueError("GUI historical scrolling is not supported")
        observation = self.provider(cursor=cursor, page_size=page_size)
        if observation.get("provenance") != "official_windows_mcp":
            raise ValueError("Real official Windows-MCP observation provenance required")
        if observation.get("room_id") != self.room:
            raise ValueError("Observed group differs from configured group")
        if observation.get("foreground") is not True or observation.get("desktop_unlocked") is not True:
            raise RuntimeError("Desktop is not foreground/unlocked; synchronization suspended")
        position = observation.get("last_position")
        if observation.get("position_continuity") is not True or not isinstance(position, str) or not position.strip():
            raise RuntimeError("Position continuity unavailable; operator review required")
        entries = observation.get("messages", ())
        if not isinstance(entries, (list, tuple)) or len(entries) > page_size or not all(isinstance(item, dict) for item in entries):
            raise ValueError("Invalid or oversized incremental observation batch")
        if entries and position == cursor:
            raise RuntimeError("Messages supplied without incremental position advancement")
        records, positions = [], set()
        for item in entries:
            item_position = item.get("position")
            has_position = isinstance(item_position, str) and bool(item_position.strip())
            if has_position:
                if item_position in positions:
                    raise ValueError("Repeated GUI message position requires review")
                positions.add(item_position)
            media_path = item.get("local_media_path")
            if media_path is not None:
                if not isinstance(media_path, str) or not media_path.strip() or self.attachment_root is None:
                    raise ValueError("Original media requires an approved attachment directory")
                if not Path(media_path).resolve().is_relative_to(self.attachment_root):
                    raise ValueError("Original media escapes the approved attachment directory")
            utc = local = None
            raw_time = item.get("sent_at") or item.get("display_time")
            if item.get("full_time_verified") is True:
                try:
                    utc, local = normalize_sent_time(raw_time, business_timezone=self.timezone)
                except (TypeError, ValueError):
                    pass
            sender_id = item.get("sender_id")
            sender_present = isinstance(sender_id, str) and bool(sender_id.strip())
            trusted = (sender_present and item.get("sender_identity_verified") is True
                       and item.get("consecutive_header_verified") is True
                       and has_position)
            # An evidence placeholder is explicitly unverified, never a student identity.
            sender = sender_id if sender_present else "unverified:" + sha256(str(item_position or "unknown").encode()).hexdigest()
            fingerprint = sha256(json.dumps([self.room, sender, raw_time, item.get("text"),
                item.get("media_hash"), item.get("position"), item.get("context")], ensure_ascii=False,
                sort_keys=True).encode()).hexdigest()
            identity = {"message_id": _message_id(self.room, item_position)} if has_position else {}
            records.append(NormalizedMessage(**identity, source_type="windows_gui", room_id=self.room,
                sender_id=sender, room_name=observation.get("room_name", ""),
                sender_display_name=item.get("sender_display_name", ""),
                raw_content=item.get("text", ""), normalized_text=item.get("text", ""),
                message_type=item.get("message_type", "text"), sent_at_raw=raw_time,
                media_id=item.get("media_id"), local_media_path=media_path,
                media_hash=item.get("media_hash"),
                sent_at_utc=utc, sent_at_local=local, fingerprint=fingerprint,
                source_confidence="high" if trusted else "low",
                time_confidence="high" if utc else "low", business_timezone=self.timezone,
                raw_payload={"observation": observation.get("evidence"), "item": item,
                             "sender_role": item.get("sender_role") or item.get("role", ""),
                             "is_self": item.get("is_self") is True,
                             "identity_verified": trusted}, parse_status="parsed" if trusted and utc else "pending_review"))
        return MessageBatch(tuple(records), str(observation["last_position"]), False)
