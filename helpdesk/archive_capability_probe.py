"""Explicitly authorized, read-only account checks with credential-free reports."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tomllib
from typing import Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .wecom_archive import ArchiveAuthorization


def _official_request(path: str, params: dict, body: dict | None, timeout: int) -> dict:
    # Fixed primary host and allowlisted read-only operations; no arbitrary URLs.
    if path not in {"gettoken", "msgaudit/get_permit_user_list",
                    "msgaudit/check_single_agree", "msgaudit/check_room_agree"}:
        raise ValueError("operation is not an allowed read-only account check")
    request = Request("https://qyapi.weixin.qq.com/cgi-bin/" + path + "?" + urlencode(params),
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"})
    # Default SSL verification remains enabled. No exception text is reported.
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("account response too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("account response must be an object")
    return result


def probe_archive(config: dict, *, environment: Mapping[str, str] | None = None,
                  request=_official_request) -> dict:
    """No network unless *all* explicitly configured prerequisites are present.

    Environment contains values; returned report contains only presence flags,
    integer error codes/counts and digest evidence, never values or server errmsg.
    """
    environment = os.environ if environment is None else environment
    archive, probe = config.get("archive", {}), config.get("archive_probe", {})
    names = ("WECOM_CORP_ID", "WECOM_ARCHIVE_SECRET", "WECOM_ARCHIVE_PRIVATE_KEY", "WECOM_ARCHIVE_SDK_PATH")
    presence = {name: bool(environment.get(name)) for name in names}
    report = {"status": "NEEDS_ADMIN_CONFIGURATION", "account_permission": "NOT_CHECKED",
        "checked_at": datetime.now(timezone.utc).isoformat(), "network_attempted": False,
        "environment_presence": presence, "checks": [], "sdk_data_pull_verified": False,
        "collector_readiness": "NOT_VERIFIED", "read_only_checks_passed": False}
    blockers = ["missing_" + name for name, exists in presence.items() if not exists]
    try:
        auth = ArchiveAuthorization(**{name: archive.get(name, False) for name in (
            "enabled", "admin_authorized", "member_scope_verified", "consent_verified")},
            evidence_reference=archive.get("evidence_reference", ""))
        auth.require()
    except (ValueError, RuntimeError, TypeError):
        blockers.append("explicit_archive_authorization_missing")
    if probe.get("allow_network_read_only") is not True:
        blockers.append("read_only_account_probe_not_enabled")
    for name in ("WECOM_ARCHIVE_PRIVATE_KEY", "WECOM_ARCHIVE_SDK_PATH"):
        value = environment.get(name, "")
        try:
            exists = bool(value) and Path(value).is_absolute() and Path(value).is_file()
        except (OSError, ValueError):
            exists = False
        report[name.lower() + "_file_present"] = exists
        if not exists:
            blockers.append(name + "_absolute_file_required")
    command = archive.get("bridge_command", [])
    if not isinstance(command, list) or not command or not isinstance(command[0], str):
        blockers.append("sdk_bridge_not_configured")
    else:
        try:
            if not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
                blockers.append("sdk_bridge_executable_missing")
        except (OSError, ValueError):
            blockers.append("sdk_bridge_executable_missing")
    timeout = archive.get("timeout_seconds", 30)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        blockers.append("invalid_timeout")
    rooms, pairs = probe.get("room_ids", []), probe.get("single_pairs", [])
    if not isinstance(rooms, list) or not all(isinstance(x, str) and x for x in rooms) or len(rooms) > 100:
        blockers.append("invalid_room_ids")
    if not isinstance(pairs, list) or len(pairs) > 100 or not all(isinstance(x, dict)
            and set(x) == {"userid", "exteranalopenid"} and all(isinstance(v, str) and v for v in x.values()) for x in pairs):
        blockers.append("invalid_single_pairs")
    if blockers:
        report["blockers"] = blockers
        return report
    report["status"] = "NEEDS_MANUAL_VERIFICATION"
    report["network_attempted"] = True
    current = "gettoken"
    try:
        token_response = request(current, {"corpid": environment["WECOM_CORP_ID"],
            "corpsecret": environment["WECOM_ARCHIVE_SECRET"]}, None, timeout)
        if not _record(report, current, token_response):
            return report
        token = token_response.get("access_token")
        if not isinstance(token, str) or not token:
            report["blockers"] = ["token_response_missing_credential"]
            return report
        current = "msgaudit/get_permit_user_list"
        members = request(current, {"access_token": token}, {}, timeout)
        if not _record(report, current, members):
            return report
        ids = members.get("ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) and x for x in ids):
            report["blockers"] = ["invalid_permitted_member_response"]
            return report
        report["permitted_member_count"] = len(ids)
        report["account_permission"] = "PERMITTED_MEMBERS_VERIFIED" if ids else "NEEDS_ADMIN_CONFIGURATION"
        if not ids:
            report["status"] = "NEEDS_ADMIN_CONFIGURATION"
            report["blockers"] = ["no_effective_archive_members"]
            return report
        consent_results = []
        if pairs:
            current = "msgaudit/check_single_agree"
            result = request(current, {"access_token": token}, {"info": pairs}, timeout)
            if not _record(report, current, result):
                return report
            consent_results.append(_consent_summary(result))
        for room in rooms:
            current = "msgaudit/check_room_agree"
            result = request(current, {"access_token": token}, {"roomid": room}, timeout)
            if not _record(report, current, result):
                return report
            consent_results.append(_consent_summary(result))
        report["consent_checks"] = consent_results
        report["read_only_checks_passed"] = True
        report["blockers"] = ["official_sdk_bridge_data_pull_not_yet_verified"]
        if not rooms and not pairs:
            report["blockers"].append("student_room_consent_not_queried")
        if any(item["disagree_count"] or item["unknown_count"] for item in consent_results):
            report["blockers"].append("external_consent_incomplete")
    except Exception as exc:
        report["checks"].append({"operation": current, "success": False,
                                  "failure_type": type(exc).__name__})
        report["blockers"] = ["read_only_account_check_failed"]
    return report


def _record(report, operation, response):
    code = response.get("errcode")
    success = type(code) is int and code == 0
    # Token payload is never hashed/stored. Other raw responses also never saved.
    entry = {"operation": operation, "success": success,
             "errcode": code if type(code) is int else None}
    if operation != "gettoken":
        entry["response_sha256"] = hashlib.sha256(json.dumps(response, sort_keys=True,
            ensure_ascii=False).encode("utf-8")).hexdigest()
    report["checks"].append(entry)
    if not success:
        report["blockers"] = ["official_read_only_api_rejected_request"]
    return success


def _consent_summary(response):
    rows = response.get("agreeinfo")
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        raise ValueError("invalid consent response")
    states = [row.get("agree_status") for row in rows]
    return {"participant_count": len(states), "agree_count": states.count("Agree"),
        "disagree_count": states.count("Disagree"),
        "unknown_count": sum(x not in {"Agree", "Disagree"} for x in states)}


def probe_exit_code(report: dict) -> int:
    """2 = blocked/failed check; 3 = read-only success, collector still unverified.

    This probe does not execute the SDK. It deliberately never returns 0, which
    callers might otherwise treat as an operational collector readiness signal.
    """
    return 3 if (report.get("read_only_checks_passed") is True and
                 report.get("account_permission") == "PERMITTED_MEMBERS_VERIFIED") else 2


def main():
    parser = argparse.ArgumentParser(description="Read-only authorized archive capability probe")
    parser.add_argument("--config", default="config/collector.example.toml")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        with Path(args.config).open("rb") as stream:
            config = tomllib.load(stream)
        report = probe_archive(config)
    except Exception as exc:
        report = {"status": "NEEDS_ADMIN_CONFIGURATION", "network_attempted": False,
                  "failure_type": type(exc).__name__}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return probe_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
