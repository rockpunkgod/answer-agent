"""Import verified native Clipboard archives, not a live desktop listener.

One row is an acquisition fragment, possibly containing multiple platform
messages. Missing identity/time are never inferred from its text or read time.
Each page verifies all configured artifacts: O(N) local file work, bounded at
10,000 acquisition IDs. The cursor records IDs plus evidence hashes rather than
random directory order, so newly added lower-sorting directories are found.
"""
from hashlib import sha256
import json
from pathlib import Path
from .message_sources import CursorExpired, MessageBatch, NormalizedMessage, SyncMode
from .native_intake import MISSING_METADATA, verify_native_record


class NativeMessageSource:
    source_name = "windows_native_staging"

    def __init__(self, archive_root, *, business_timezone="Asia/Shanghai", max_acquisitions=10000):
        self.root = Path(archive_root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Configured native archive root must be a directory")
        if type(max_acquisitions) is not int or not 1 <= max_acquisitions <= 10000:
            raise ValueError("max_acquisitions must be 1..10000; cursor is never silently truncated")
        self.max_acquisitions = max_acquisitions
        self.business_timezone = business_timezone
        self.root_key = sha256(str(self.root).encode("utf-8")).hexdigest()

    def _read_cursor(self, cursor, mode):
        if cursor is None:
            return {}
        if not isinstance(cursor, str) or len(cursor.encode("utf-8")) > 1500000:
            raise CursorExpired("Native import cursor requires review")
        try:
            value = json.loads(cursor)
            if (not isinstance(value, dict) or set(value) != {"version", "root", "mode", "seen"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["root"] != self.root_key or value["mode"] != str(mode)
                    or not isinstance(value["seen"], dict) or len(value["seen"]) > self.max_acquisitions):
                raise ValueError()
            for identity, digest in value["seen"].items():
                if (not isinstance(identity, str) or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity)
                    or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                    raise ValueError()
            return value["seen"]
        except (ValueError, TypeError, KeyError):
            raise CursorExpired("Native import cursor format/root/mode requires review") from None

    @staticmethod
    def _evidence_hash(record):
        return sha256(json.dumps([record[k] for k in
            ("raw_text_sha256", "result_sha256", "attempt_sha256", "manifest_sha256")], separators=(",", ":")).encode()).hexdigest()

    def _message(self, record):
        paths = json.loads(record["evidence_json"])
        manifest_bytes = Path(paths["manifest_path"]).read_bytes()
        if sha256(manifest_bytes).hexdigest() != record["manifest_sha256"]:
            raise ValueError("Native evidence changed during verification")
        identity = record["acquisition_id"]
        group = record["observed_group_label"]
        provenance = {k: v for k, v in record.items() if k not in {"original_text", "evidence_json"}}
        return NormalizedMessage(source_type="windows_gui", message_id="native-acquisition:" + identity,
            source_message_id=None, source_seq=None,
            room_id="unverified-room:" + sha256(group.encode("utf-8")).hexdigest(),
            room_name=group, sender_id="unverified-sender:" + identity,
            raw_content=record["original_text"], normalized_text=record["original_text"],
            sent_at_raw=None, sent_at_utc=None, sent_at_local=None,
            source_confidence="low", time_confidence="low", parse_status="pending_time_verification",
            fingerprint=sha256((self.source_name + ":" + identity).encode()).hexdigest(),
            business_timezone=self.business_timezone,
            raw_payload={"record_kind": "native_acquisition_fragment_not_platform_message",
                "source_kind": "WINDOWS_MCP_NATIVE_CLIPBOARD", "manifest": json.loads(manifest_bytes),
                "provenance": provenance, "evidence_paths": paths, "identity_verified": False,
                "platform_message_id": None, "platform_room_id": None, "platform_sender_id": None,
                "original_message_time": None, "formal_statistics_eligible": False,
                "desktop_listener_verified": False, "review_required": list(MISSING_METADATA)})

    def fetch_page(self, cursor, mode, page_size):
        mode = SyncMode(mode)
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("Native import page_size must be 1..1000")
        seen = self._read_cursor(cursor, mode)
        records, hashes = {}, {}
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not (folder / "manifest.json").is_file():
                continue
            if folder.resolve().parent != self.root:
                raise ValueError("Native archive evidence escapes configured root")
            record = verify_native_record(folder)
            identity, digest = record["acquisition_id"], self._evidence_hash(record)
            if (identity in hashes and hashes[identity] != digest) or (identity in seen and seen[identity] != digest):
                raise ValueError("Existing acquisition evidence changed; manual review required")
            # Scope is only an observed label filter, never verified platform identity.
            if "english" not in record["observed_group_label"].casefold():
                continue
            records.setdefault(identity, record)
            hashes[identity] = digest
            if len(records) > self.max_acquisitions:
                raise CursorExpired("Native archive limit reached; explicit partition/review required")
        unread = sorted(identity for identity in records if identity not in seen)
        selected = unread[:page_size]
        if len(seen) + len(selected) > self.max_acquisitions:
            raise CursorExpired("Native cursor capacity reached; no IDs were discarded")
        messages = tuple(self._message(records[identity]) for identity in selected)
        next_seen = dict(seen)
        next_seen.update({identity: hashes[identity] for identity in selected})
        next_cursor = json.dumps({"version": 1, "root": self.root_key, "mode": str(mode), "seen": next_seen}, sort_keys=True, separators=(",", ":"))
        return MessageBatch(messages, next_cursor, len(unread) > len(selected))


def create_source(config):
    source = config.get("source", {})
    if source.get("kind") != "native_clipboard_archive" or not source.get("native_root"):
        raise ValueError("Configure explicit native_clipboard_archive kind and native_root")
    if source.get("name", NativeMessageSource.source_name) != NativeMessageSource.source_name:
        raise ValueError("Native archive source name must be windows_native_staging")
    return NativeMessageSource(source["native_root"],
        business_timezone=config.get("collector", {}).get("business_timezone", "Asia/Shanghai"),
        max_acquisitions=source.get("max_acquisitions", 10000))
