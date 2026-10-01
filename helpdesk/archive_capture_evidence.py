"""Independent local SDK receipts; official plaintext is never decorated.

HMAC attests the explicitly trusted local native runtime, not a Tencent signature.
Host operators with access to the private evidence key are inside this trust boundary.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import uuid4

from .message_sources import utc_now
from .locking import resource_lock


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def evidence_key(environment=None):
    env = os.environ if environment is None else environment
    try:
        key = base64.b64decode(env.get("WECOM_ARCHIVE_EVIDENCE_KEY", ""), validate=True)
    except (ValueError, TypeError):
        raise ValueError("ACTUAL_EVIDENCE_KEY_REQUIRED") from None
    if len(key) < 32:
        raise ValueError("ACTUAL_EVIDENCE_KEY_REQUIRED")
    return key


def current_deployment(environment=None):
    env = os.environ if environment is None else environment
    from . import wecom_sdk_bridge
    result = {"deployment_id": env.get("WECOM_ARCHIVE_DEPLOYMENT_ID", ""),
              "sdk_sha256": env.get("WECOM_ARCHIVE_SDK_SHA256", ""),
              "bridge_sha256": hashlib.sha256(Path(wecom_sdk_bridge.__file__).read_bytes()).hexdigest()}
    if env.get("WECOM_ARCHIVE_RUNTIME_MODE") != "ACTUAL" or not result["deployment_id"] or len(result["sdk_sha256"]) != 64:
        raise ValueError("ACTUAL_NATIVE_DEPLOYMENT_REQUIRED")
    return result


def sign_native_receipt(response, environment=None):
    return hmac.new(evidence_key(environment), canonical(response), hashlib.sha256).hexdigest()


def verify_native_receipt(receipt, operation, deployment, environment=None):
    if not isinstance(receipt, dict):
        raise ValueError("SIGNED_NATIVE_RECEIPT_REQUIRED")
    body = {key: value for key, value in receipt.items() if key != "receipt_hmac_sha256"}
    runtime = body.get("native_runtime")
    if (body.get("protocol") != "wecom-archive-sdk-v1" or body.get("operation") != operation
            or type(body.get("sdk_code")) is not int or body["sdk_code"] != 0
            or type(body.get("errcode")) is not int or body["errcode"] != 0
            or not isinstance(runtime, dict) or runtime.get("execution_kind") != "NATIVE_SDK"
            or runtime.get("observation_mode") != "ACTUAL"
            or any(runtime.get(k) != value for k, value in deployment.items())):
        raise ValueError("ACTUAL_NATIVE_RUNTIME_MISMATCH")
    supplied = receipt.get("receipt_hmac_sha256")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, sign_native_receipt(body, environment)):
        raise ValueError("NATIVE_RECEIPT_SIGNATURE_INVALID")
    return body["result"]


class ArchiveCaptureEvidence:
    def __init__(self, root, deployment):
        self.root, self.deployment = Path(root).resolve(), dict(deployment)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_batch(self, source_name, mode, page_receipt, entries):
        verify_native_receipt(page_receipt, "get_chat_data", self.deployment)
        if not entries:
            return None
        record = {"format": "wecom-native-capture-v1", "source_type": "wecom_archive",
            "source_name": source_name, "mode": str(mode), "observation_mode": "ACTUAL",
            "captured_at": utc_now(), "deployment": self.deployment,
            "page_receipt": page_receipt, "messages": entries}
        for entry in entries:
            plaintext = verify_native_receipt(entry["decrypt_receipt"], "decrypt_message", self.deployment)
            if plaintext != entry["raw_payload"]:
                raise ValueError("DECRYPTED_RECEIPT_PAYLOAD_MISMATCH")
        data = canonical(record)
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / (digest + ".json")
        with resource_lock(self.root / ".capture.lock", timeout=5):
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError("ARCHIVE_CAPTURE_FILE_CORRUPT")
            else:
                self._atomic_write(target, data)
            for entry in entries:
                key = hashlib.sha256(canonical([source_name, entry["source_message_id"]])).hexdigest()
                index = self.root / (key + ".index.json")
                # Preserve a valid first capture. A crash before atomic replacement
                # leaves only a temporary file, which never becomes trusted evidence.
                if index.exists():
                    self._validate_first_index(index, source_name, entry)
                else:
                    self._atomic_write(index, canonical({"evidence_filename": target.name, "sha256": digest}))
        return target

    def _validate_first_index(self, index, source_name, entry):
        try:
            target = _read_index(index, self.root)
            record = json.loads(target.read_bytes())
            if (record.get("format") != "wecom-native-capture-v1" or record.get("source_name") != source_name
                    or record.get("source_type") != "wecom_archive" or record.get("observation_mode") != "ACTUAL"
                    or record.get("mode") not in {"LIVE", "BACKFILL"} or record.get("deployment") != self.deployment):
                raise ValueError()
            page = verify_native_receipt(record.get("page_receipt"), "get_chat_data", self.deployment)
            matching = [row for row in record.get("messages", []) if row.get("source_message_id") == entry["source_message_id"]]
            if len(matching) != 1:
                raise ValueError()
            first = matching[0]
            plain = verify_native_receipt(first.get("decrypt_receipt"), "decrypt_message", self.deployment)
            if (plain != first.get("raw_payload") or plain != entry["raw_payload"]
                    or plain.get("msgid") != entry["source_message_id"]
                    or first.get("payload_sha256") != hashlib.sha256(canonical(plain)).hexdigest()
                    or not any(row.get("msgid") == first["source_message_id"] and str(row.get("seq")) == str(first.get("source_seq"))
                               for row in page.get("chatdata", []))):
                raise ValueError()
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError("ARCHIVE_CAPTURE_INDEX_HOLD_REQUIRED") from None

    @staticmethod
    def _atomic_write(target, data):
        temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            # A hard-link publication is atomic and never overwrites an existing
            # first capture. Same-directory temp and target share a filesystem.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise ValueError("ARCHIVE_CAPTURE_PUBLICATION_HOLD_REQUIRED") from None
        finally:
            if temporary.exists():
                temporary.unlink()


def lookup_archive_capture(raw_message, root="private/archive-capture"):
    raw = dict(raw_message)
    root = Path(root).resolve()
    key = hashlib.sha256(canonical([raw["source_name"], raw["source_message_id"]])).hexdigest()
    return _read_index(root / (key + ".index.json"), root)


def _read_index(index_path, root):
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        digest = index["sha256"]
        filename = index["evidence_filename"]
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or filename != digest + ".json"):
            raise ValueError()
        target = (root / filename).resolve()
        if not target.is_relative_to(root) or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError()
        return target
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError("ARCHIVE_CAPTURE_INDEX_HOLD_REQUIRED") from None


def verify_archive_capture(raw_message, evidence_path, expected_deployment=None):
    raw = dict(raw_message)
    if raw.get("source_type") != "wecom_archive":
        raise ValueError("OFFICIAL_ARCHIVE_SOURCE_REQUIRED")
    deployment = current_deployment() if expected_deployment is None else dict(expected_deployment)
    # Explicit expected deployment never bypasses the private runtime signing key.
    path = Path(evidence_path).resolve()
    data = path.read_bytes()
    record = json.loads(data)
    if (record.get("format") != "wecom-native-capture-v1" or record.get("source_name") != raw.get("source_name")
            or record.get("source_type") != "wecom_archive" or record.get("mode") != "LIVE"
            or record.get("observation_mode") != "ACTUAL" or record.get("deployment") != deployment):
        raise ValueError("ACTUAL_ARCHIVE_CAPTURE_MISMATCH")
    page = verify_native_receipt(record.get("page_receipt"), "get_chat_data", deployment)
    original = json.loads(raw["raw_payload"]) if isinstance(raw["raw_payload"], str) else raw["raw_payload"]
    if isinstance(original, dict) and (original.get("fixture") is True or original.get("simulated") is True):
        raise ValueError("FIXTURE_CANNOT_BECOME_ACTUAL_SOURCE")
    digest = hashlib.sha256(canonical(original)).hexdigest()
    matches = [entry for entry in record.get("messages", []) if entry.get("source_message_id") == raw.get("source_message_id")
               and str(entry.get("source_seq")) == str(raw.get("source_seq"))]
    if len(matches) != 1:
        raise ValueError("ARCHIVE_CAPTURE_MESSAGE_NOT_UNIQUE")
    entry = matches[0]
    plain = verify_native_receipt(entry.get("decrypt_receipt"), "decrypt_message", deployment)
    if (entry.get("payload_sha256") != digest or plain != original or entry.get("raw_payload") != original
            or plain.get("msgid") != raw.get("source_message_id")):
        raise ValueError("ARCHIVE_CAPTURE_PAYLOAD_CHANGED")
    if not any(envelope.get("msgid") == raw["source_message_id"] and str(envelope.get("seq")) == str(raw["source_seq"])
               for envelope in page.get("chatdata", [])):
        raise ValueError("ARCHIVE_PAGE_MESSAGE_MISSING")
    return {"verified_actual": True, "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
        **deployment, "collector_message_id": raw["message_id"], "source_message_id": raw["source_message_id"],
        "source_seq": raw["source_seq"], "payload_sha256": digest}
