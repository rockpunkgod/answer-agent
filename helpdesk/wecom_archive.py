"""Authorized archive SDK boundary; this module never pretends a mock is live.

Official contract verified 2026-09-30: developer.work.weixin.qq.com/document/path/91774.
The bridge must wrap Init/GetChatData/RSA private-key decrypt/DecryptData and
GetMediaData from the actual official SDK. Credentials stay in bridge environment.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Protocol

from .message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time


class ArchiveNotConfigured(RuntimeError):
    pass


class ArchiveProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveAuthorization:
    enabled: bool = False
    admin_authorized: bool = False
    member_scope_verified: bool = False
    consent_verified: bool = False
    evidence_reference: str = ""

    def __post_init__(self):
        if any(type(flag) is not bool for flag in (self.enabled, self.admin_authorized,
                self.member_scope_verified, self.consent_verified)) or not isinstance(self.evidence_reference, str):
            raise ValueError("authorization requires explicit boolean flags and evidence string")

    def require(self):
        if not all((self.enabled, self.admin_authorized, self.member_scope_verified,
                    self.consent_verified, self.evidence_reference.strip())):
            raise ArchiveNotConfigured("NEEDS_ADMIN_CONFIGURATION: archive authorization, scope, consent and evidence required")


@dataclass(frozen=True)
class MediaChunk:
    data: bytes
    next_index: str
    finished: bool


class ArchiveTransport(Protocol):
    def get_chat_data(self, seq: int, limit: int) -> dict: ...
    def decrypt_message(self, envelope: dict) -> dict: ...
    def get_media_data(self, sdkfileid: str, indexbuf: str) -> MediaChunk: ...


class SdkBridgeTransport:
    """Strict, local stdio JSON bridge, explicitly supplied by the administrator.

    No shell, URLs or downloaded executables are accepted. An installed SDK bridge
    is a separate deployment prerequisite. The native implementation is provided
    by wecom_sdk_bridge, but configuring it does not verify a real account.
    Errors deliberately omit stdout/stderr (may contain keys).
    """
    def __init__(self, command: tuple[str, ...], *, authorization: ArchiveAuthorization,
                 timeout_seconds: int = 30, runtime_mode: str = "FIXTURE",
                 capture_root: str | Path = "private/archive-capture",
                 deployment_id: str = "", sdk_sha256: str = ""):
        authorization.require()
        if not command or not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
            raise ArchiveNotConfigured("absolute existing SDK bridge executable required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("bridge timeout must be 1..120 seconds")
        self.command, self.timeout = command, timeout_seconds
        self.runtime_mode, self.last_response, self.capture_evidence = runtime_mode, None, None
        if runtime_mode not in {"FIXTURE", "ACTUAL"}:
            raise ValueError("explicit FIXTURE or ACTUAL runtime required")
        if runtime_mode == "ACTUAL":
            from .archive_capture_evidence import ArchiveCaptureEvidence, current_deployment, evidence_key
            if (len(command) != 3 or Path(command[0]).resolve() != Path(sys.executable).resolve()
                    or command[1:] != ("-m", "helpdesk.wecom_sdk_bridge")):
                raise ArchiveNotConfigured("ACTUAL capture requires the bundled native SDK bridge")
            deployment = current_deployment()
            evidence_key()
            if deployment_id != deployment["deployment_id"] or sdk_sha256 != deployment["sdk_sha256"]:
                raise ArchiveNotConfigured("ACTUAL capture deployment config/environment mismatch")
            self.capture_evidence = ArchiveCaptureEvidence(capture_root, deployment)

    def _call(self, operation: str, params: dict) -> dict:
        request = {"protocol": "wecom-archive-sdk-v1", "operation": operation, "params": params}
        try:
            result = subprocess.run(self.command, input=json.dumps(request), text=True,
                                    encoding="utf-8", capture_output=True, timeout=self.timeout,
                                    check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ArchiveProtocolError("SDK bridge unavailable or timed out") from None
        if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
            raise ArchiveProtocolError("SDK bridge failed or exceeded response limit")
        try:
            response = json.loads(result.stdout)
            if not isinstance(response, dict) or not {"protocol", "operation", "sdk_code", "errcode", "result"} <= response.keys():
                raise ValueError()
            if response["protocol"] != request["protocol"] or response["operation"] != operation:
                raise ValueError()
            if (type(response["sdk_code"]) is not int or type(response["errcode"]) is not int
                    or response["sdk_code"] != 0 or response["errcode"] != 0):
                raise ValueError()
            payload = response["result"]
            if not isinstance(payload, dict):
                raise ValueError()
            if self.runtime_mode == "ACTUAL":
                from .archive_capture_evidence import verify_native_receipt
                verify_native_receipt(response, operation, self.capture_evidence.deployment)
            self.last_response = response
            return payload
        except (ValueError, TypeError, KeyError):
            raise ArchiveProtocolError("SDK bridge returned an invalid or unsuccessful response") from None

    def get_chat_data(self, seq: int, limit: int) -> dict:
        return self._call("get_chat_data", {"seq": str(seq), "limit": limit})

    def decrypt_message(self, envelope: dict) -> dict:
        return self._call("decrypt_message", {"envelope": envelope})

    def get_media_data(self, sdkfileid: str, indexbuf: str) -> MediaChunk:
        import base64
        result = self._call("get_media_data", {"sdkfileid": sdkfileid, "indexbuf": indexbuf})
        try:
            chunk = base64.b64decode(result["data_base64"], validate=True)
            index, finished = result["outindexbuf"], result["is_finish"]
            if not isinstance(index, str) or type(finished) is not int or finished not in (0, 1) or len(chunk) > 512 * 1024:
                raise ValueError()
            return MediaChunk(chunk, index, bool(finished))
        except (ValueError, TypeError, KeyError):
            raise ArchiveProtocolError("invalid SDK media chunk") from None


class WecomArchiveSource:
    def __init__(self, transport: ArchiveTransport, authorization: ArchiveAuthorization,
                 *, source_name: str = "wecom_archive", business_timezone: str = "Asia/Shanghai",
                 min_call_interval: float = 0.1):
        authorization.require()
        if not source_name.strip() or not math.isfinite(min_call_interval) or min_call_interval < 0.015:
            raise ValueError("source name required; interval must respect 4000 calls/minute")
        self.transport, self.authorization = transport, authorization
        self.source_name, self.business_timezone = source_name, business_timezone
        self.min_call_interval, self._last_call = min_call_interval, 0.0

    def fetch_page(self, cursor: str | None, mode: SyncMode, page_size: int) -> MessageBatch:
        self.authorization.require()
        SyncMode(mode)
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("official archive page size must be 1..1000")
        if cursor is not None and (not cursor.isascii() or not cursor.isdecimal()):
            raise ValueError("archive cursor must be unsigned decimal seq")
        seq = int(cursor or 0)
        if not 0 <= seq < 2 ** 64:
            raise ValueError("seq outside uint64")
        delay = self.min_call_interval - (time.monotonic() - self._last_call)
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.monotonic()
        response = self.transport.get_chat_data(seq, page_size)
        capture = self.transport.capture_evidence if type(self.transport) is SdkBridgeTransport else None
        page_receipt = self.transport.last_response if capture is not None else None
        if (not isinstance(response, dict) or type(response.get("errcode")) is not int
                or response["errcode"] != 0 or not isinstance(response.get("chatdata"), list)):
            raise ArchiveProtocolError("archive SDK returned an unsuccessful or malformed page")
        envelopes = response["chatdata"]
        if len(envelopes) > page_size:
            raise ArchiveProtocolError("archive page exceeds requested limit")
        messages, entries, previous = [], [], seq
        for envelope in envelopes:
            if not isinstance(envelope, dict) or type(envelope.get("seq")) is not int or not previous < envelope["seq"] < 2 ** 64:
                raise ArchiveProtocolError("archive seq must be strictly increasing after cursor")
            payload = self.transport.decrypt_message(envelope)
            messages.append(self._normalize(envelope, payload))
            if capture is not None:
                from .archive_capture_evidence import canonical
                import hashlib
                entries.append({"source_message_id": payload["msgid"], "source_seq": envelope["seq"],
                    "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
                    "raw_payload": payload, "decrypt_receipt": self.transport.last_response})
            previous = envelope["seq"]
        if capture is not None:
            capture.save_batch(self.source_name, mode, page_receipt, entries)
        return MessageBatch(tuple(messages), str(previous), len(envelopes) == page_size)

    def _normalize(self, envelope: dict, payload: dict) -> NormalizedMessage:
        if not isinstance(payload, dict) or not isinstance(payload.get("msgid"), str) or payload.get("msgid") != envelope.get("msgid"):
            raise ArchiveProtocolError("decrypted msgid must match encrypted envelope")
        if payload.get("action") == "switch":
            sender, raw_time = payload.get("user"), payload.get("time")
            if not isinstance(sender, str) or not sender:
                raise ArchiveProtocolError("switch log missing user")
            utc = local = None
            if type(raw_time) is int and raw_time > 0:
                utc, local = normalize_sent_time(raw_time, unit="milliseconds", business_timezone=self.business_timezone)
            return NormalizedMessage(source_type="wecom_archive", source_message_id=payload["msgid"],
                source_seq=envelope["seq"], room_id="system:wecom_archive_switch", sender_id=sender,
                message_type="other", raw_content=json.dumps(payload, ensure_ascii=False),
                sent_at_raw=raw_time, sent_at_utc=utc, sent_at_local=local, raw_payload=payload,
                parse_status="system_event", business_timezone=self.business_timezone)
        sender, recipients = payload.get("from"), payload.get("tolist")
        if not isinstance(sender, str) or not sender or not isinstance(recipients, list) or not all(isinstance(x, str) and x for x in recipients):
            raise ArchiveProtocolError("archive message missing verified participants")
        room = payload.get("roomid", "")
        if not isinstance(room, str):
            raise ArchiveProtocolError("archive roomid must be a string")
        if not room:
            if not recipients:
                raise ArchiveProtocolError("direct message has no recipients")
            # Canonical participants, distinct from a server-supplied group roomid.
            room = "direct:" + json.dumps(sorted(set([sender, *recipients])), ensure_ascii=False, separators=(",", ":"))
        raw_time = payload.get("msgtime")
        utc = local = None
        if type(raw_time) is int and raw_time > 0:
            utc, local = normalize_sent_time(raw_time, unit="milliseconds", business_timezone=self.business_timezone)
        kind = payload.get("msgtype", "other")
        known = {"text", "image", "file", "voice", "video", "link"}
        media = payload.get(kind, {})
        media = media if isinstance(media, dict) else {}
        content = media.get("content", "") if kind == "text" else json.dumps(media, ensure_ascii=False)
        # Quote prefix is an unstructured text convention, never a reliable reply ID.
        if not isinstance(content, str):
            raise ArchiveProtocolError("archive text content must be a string")
        return NormalizedMessage(source_type="wecom_archive", source_message_id=payload["msgid"],
            source_seq=envelope["seq"], room_id=room, sender_id=sender,
            message_type=kind if kind in known else "other", raw_content=content,
            normalized_text=content if kind == "text" else "",
            media_id=media.get("sdkfileid") if kind in {"image", "file", "voice", "video"} else None,
            sent_at_raw=raw_time, sent_at_utc=utc, sent_at_local=local,
            raw_payload=payload, business_timezone=self.business_timezone)


def configuration_presence() -> dict[str, Any]:
    """Safe local inventory, never reads or prints credential contents."""
    import os
    names = ("WECOM_CORP_ID", "WECOM_ARCHIVE_SECRET", "WECOM_ARCHIVE_PRIVATE_KEY", "WECOM_ARCHIVE_SDK_PATH")
    return {"status": "NEEDS_MANUAL_VERIFICATION", "live_account_verified": False,
            "environment_presence": {name: bool(os.environ.get(name)) for name in names}}


def create_source(config: dict) -> WecomArchiveSource:
    """CLI factory. Config contains authorization and executable paths, never keys."""
    archive = config.get("archive", config)
    flags = ("enabled", "admin_authorized", "member_scope_verified", "consent_verified")
    if any(type(archive.get(key, False)) is not bool for key in flags):
        raise ValueError("archive authorization flags must be booleans")
    authorization = ArchiveAuthorization(**{key: archive.get(key, False) for key in flags},
        evidence_reference=archive.get("evidence_reference", ""))
    transport = SdkBridgeTransport(tuple(archive.get("bridge_command", ())), authorization=authorization,
        timeout_seconds=archive.get("timeout_seconds", 30),
        runtime_mode=archive.get("capture", {}).get("runtime_mode", "FIXTURE"),
        capture_root=archive.get("capture", {}).get("root", "private/archive-capture"),
        deployment_id=archive.get("capture", {}).get("deployment_id", ""),
        sdk_sha256=archive.get("capture", {}).get("sdk_sha256", ""))
    return WecomArchiveSource(transport, authorization,
        source_name=archive.get("source_name", "wecom_archive"),
        business_timezone=config.get("business_timezone", config.get("collector", {}).get("business_timezone", "Asia/Shanghai")),
        min_call_interval=archive.get("min_call_interval", 0.1))
