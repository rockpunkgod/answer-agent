"""Read-only, evidence-only historical draft; never creates formal counting units."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from helpdesk.performance_rules import RULE_VERSION
from tools.performance_report import export_xlsx


START_DATE = "2026-09-17"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _private_path(value):
    return str(Path(value).resolve()) if value else ""


def _read_coverage(db_path: Path, end_date: str):
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    # SQLite read-only URI guarantees the historical draft cannot change ledger
    # or an already submitted report version.
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        groups = []
        for row in conn.execute("SELECT * FROM history_groups ORDER BY created_at,id"):
            item = dict(row)
            try:
                item["evidence"] = json.loads(item["evidence"])
            except (TypeError, ValueError):
                item["evidence"] = {"raw": item["evidence"]}
            coverage = [dict(r) for r in conn.execute("SELECT * FROM history_coverage WHERE group_id=? ORDER BY updated_at,id", (item["id"],))]
            item["coverage_records"] = coverage
            item["coverage_complete"] = item["identity_status"] == "VERIFIED" and any(
                r["status"] == "COMPLETE" and r["start_date"] <= START_DATE and r["end_date"] >= end_date for r in coverage)
            groups.append(item)
        inventory = conn.execute("SELECT complete FROM history_inventory_reviews ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
        complete = bool(inventory and inventory["complete"] and groups and all(g["coverage_complete"] for g in groups))
        return {"scope": "ALL_HISTORICAL_GROUPS", "start_date": START_DATE, "end_date": end_date,
                "inventory_complete": bool(inventory and inventory["complete"]), "complete": complete,
                "groups": groups, "notes": ["仅部分本机记录；观察到群聊不等于已扫描全部群聊", "截图和OCR候选不能直接确认为绩效篇数"]}
    finally:
        conn.close()


def _candidate_date(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return ""


def build_payload(db_path: Path, history_root: Path, *, end_date: str) -> dict:
    end_date = date.fromisoformat(end_date).isoformat()
    coverage = _read_coverage(db_path, end_date)
    pending = []
    details = []
    for group in coverage["groups"]:
        evidence = group.get("evidence") or {}
        source = evidence.get("screenshot") or evidence.get("file") or ""
        pending.append({"counting_unit_id": "", "student": "", "group": group["display_name"],
                        "question_time": "", "reason": f"已观察群聊（{group['membership']}）；完整群清单及9月17日起记录尚未核验",
                        "first_message_id": "", "status": "数据覆盖待核验", "candidate_id": group["id"],
                        "source_evidence": source})
    pending.append({"counting_unit_id": "", "student": "", "group": "历史日期范围",
                    "question_time": "", "reason": "9月17日至21日历史界面为灰色且不可跳转，当前本机证据无法核验该区间；不能据此认定没有提问或确认篇数为0",
                    "first_message_id": "", "status": "历史区间待核验",
                    "candidate_id": "coverage:2026-09-17-to-2026-09-21",
                    "source_evidence": _private_path(db_path)})
    for folder in sorted(p for p in history_root.iterdir() if p.is_dir()):
        manifest_file = folder / "manifest.json"
        if not manifest_file.exists():
            continue
        manifest = _json(manifest_file)
        observed_group = manifest.get("observed_group_display") or manifest.get("observed_group") or folder.name
        curated_file = folder / "curated_candidates.json"
        extracted_file = folder / "extracted.json"
        source = _json(extracted_file) if extracted_file.exists() else {}
        review_notes = manifest.get("review_notes") or []
        if isinstance(review_notes, str):
            review_notes = [review_notes]
        for index, note in enumerate(review_notes, start=1):
            pending.append({"counting_unit_id": "", "student": "", "group": observed_group,
                            "question_time": "", "reason": f"截图复核提示：{note}",
                            "first_message_id": "", "status": "截图遮挡待核验",
                            "candidate_id": f"{folder.name}:review-note-{index}",
                            "source_evidence": _private_path(manifest_file)})
        curated = _json(curated_file).get("groups", []) if curated_file.exists() else source.get("curated_material_candidates", [])
        if not curated:
            no_question_candidates = extracted_file.exists() and not source.get("candidate_units")
            reason = ("当前可见截图的OCR未识别到问句候选，但该批截图未证明覆盖全期，不能据此认定0篇；核对完整历史范围"
                      if no_question_candidates else
                      "本机截图和OCR候选已保存，材料归并及原始时间尚待人工核验"
                      if extracted_file.exists() else "本机截图已保存，OCR尚未完成；提问与材料归并待核验")
            pending.append({"counting_unit_id": "", "student": "", "group": observed_group,
                            "question_time": "", "reason": reason,
                            "first_message_id": "", "status": "覆盖范围待核验" if no_question_candidates else "归并待核验", "candidate_id": folder.name,
                            "source_evidence": _private_path(extracted_file if extracted_file.exists() else manifest_file)})
            by_message = {item.get("id"): item for item in source.get("messages", [])}
            for item in source.get("candidate_units", []):
                candidate_id = item.get("id") or item.get("first_message_candidate_id")
                first = by_message.get(item.get("first_message_candidate_id"), {})
                files = [_private_path(folder / evidence.get("file")) for evidence in first.get("evidence", [])
                         if isinstance(evidence, dict) and evidence.get("file")]
                pending.append({"counting_unit_id": "", "student": item.get("student_ocr") or "",
                                "group": observed_group, "question_time": "",
                                "reason": "OCR问句候选；原始发送时间、材料归并和真实交付均未核验",
                                "first_message_id": "", "status": "OCR候选待核验",
                                "candidate_id": f"{folder.name}:{candidate_id}",
                                "source_evidence": "；".join(files) or _private_path(extracted_file)})
            continue
        for item in curated:
            cid = f"{folder.name}:{item.get('group_id') or item.get('id') or 'candidate'}"
            candidate_date = _candidate_date(item.get("business_date_candidate"))
            if candidate_date and candidate_date < START_DATE:
                continue
            frames = [_private_path(folder / frame) for frame in item.get("evidence_frames", [])]
            evidence = "；".join(frames) or _private_path(curated_file if curated_file.exists() else extracted_file)
            checks = item.get("remaining_checks") or []
            if isinstance(checks, str):
                checks = [checks]
            common = {"candidate_id": cid, "candidate_date": candidate_date,
                      "source_evidence": evidence, "remaining_checks": checks,
                      "group": observed_group, "student": item.get("student_display_candidate") or "",
                      "question_type": item.get("question_type_candidate") or "待核验",
                      "candidate_message_id": item.get("first_prompt_id") or "",
                      "possible_answer_ids": item.get("possible_answer_ids") or []}
            details.append({**common, "counting_unit_id": "", "student_key": "", "material_id": "",
                            "topic_key": item.get("material_grouping") or "OCR材料候选",
                            "first_message_id": "", "linked_message_ids": [], "linked_question_ids": [],
                            "linked_question_versions": [], "student_question_numbers": [],
                            "question_time": "", "question_time_source": "", "night_window_date": "",
                            "first_response_at": "", "completed_at": "", "completion_outbox_id": "",
                            "category": "待核验", "category_basis": "原始消息日期/时分和材料归属未核验",
                            "measure_unit": "待核验", "confirmed_quantity": None,
                            "actual_question_count": None, "approved_conversion": None, "followup_turns": None,
                            "grouping_reason": item.get("material_grouping") or "待核验",
                            "status": "PENDING", "timeliness_status": "PENDING",
                            "review_actor": "", "review_at": "", "review_evidence": "",
                            "rule_version": RULE_VERSION, "attributed_date": "", "included": False,
                            "exclusion_reason": "候选证据尚未形成已核验计量单元"})
            pending.append({"counting_unit_id": "", "student": common["student"], "group": observed_group,
                            "question_time": "", "reason": "材料候选；需核对原始提问时间、同篇归并、真实交付与人工确认"
                            + ("；" + "；".join(checks) if checks else ""),
                            "first_message_id": "", "status": "计量待核验", "candidate_id": cid,
                            "source_evidence": evidence})
    return {"history_draft": True, "simulation": False, "report_date": end_date,
            "teacher": "待核验", "timezone": "Asia/Shanghai", "attribution": "question_day_07",
            "question_day_boundary": "07:00:00", "reporting_cutoff": "07:00:00",
            "activity_day_cutoff": "23:59:59", "generated_at": datetime.now(timezone.utc).isoformat(),
            "rule_version": RULE_VERSION, "scope": "ALL_HISTORICAL_GROUPS", "scope_start_date": START_DATE,
            "coverage": coverage, "template": "仅部分本机记录，9/17起全量覆盖未完成；自拟待核验草稿",
            "source": "本地历史截图、OCR整理候选及只读群覆盖台账；不等于正式绩效台账",
            "summary": {"day_composite_articles": None, "grammar_listening_actual_questions": None,
                        "grammar_listening_converted_questions": None, "night_articles": None,
                        "regular_articles": None},
            "monthly_cumulative": {"day_composite_articles": None, "grammar_listening_actual_questions": None,
                                   "grammar_listening_converted_questions": None, "night_articles": None,
                                   "regular_articles": None},
            "groups": {}, "details": details, "pending": pending}


def main(argv=None):
    parser = argparse.ArgumentParser(description="从本机证据生成历史答疑待核验草稿；不写正式台账")
    parser.add_argument("--db", default="data/history-statistics.db")
    parser.add_argument("--history-root", default="data/private/history")
    parser.add_argument("--end-date", default="2026-09-30")
    parser.add_argument("--output-dir", default="outputs/performance-history-draft")
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = build_payload(Path(args.db), Path(args.history_root), end_date=args.end_date)
    (output / "历史答疑统计待核验草稿.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    export_xlsx(payload, output / "历史答疑统计待核验草稿.xlsx", previews=output / "previews")
    print(json.dumps({"coverage_complete": payload["coverage"]["complete"],
                      "observed_groups": len(payload["coverage"]["groups"]),
                      "curated_evidence_rows": len(payload["details"]),
                      "confirmed_articles": None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
