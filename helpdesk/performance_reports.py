"""Deterministic local performance report snapshots.

The ledger owns counting decisions. Reports only aggregate confirmed units with
verified completion evidence; generation never mutates counting units.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .performance_rules import RULE_VERSION
from .performance_review import PerformanceReview
from .locking import resource_lock


COMPOSITE_TYPES = {"阅读理解", "阅读", "七选五", "完形填空", "完形", "写作", "应用文", "读后续写"}
QUESTION_TYPES = {"听力", "语法填空"}
CONFIRMED = {"CONFIRMED", "已确认"}
EXCLUDED = {"EXCLUDED", "REVOKED", "不计入", "已撤销"}
NIGHT = {"NIGHT", "夜间"}
REGULAR = {"REGULAR", "常规"}


def _date(value: str | None, zone) -> str | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(zone).date().isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PerformanceReports:
    def __init__(self, store, ledger, *, teacher: str = "未指定", timezone_name: str = "Asia/Shanghai",
                 rule_version: str = RULE_VERSION, attribution: str = "question_day_07",
                 cutoff_time: time = time(23, 59, 59), scope_start_date: str = "2026-09-17",
                 coverage_store=None):
        if attribution not in {"completion_date", "question_date", "question_day_07", "approval_date"}:
            raise ValueError("Unsupported attribution")
        self.store = store
        self.coverage_store = coverage_store or store
        self.ledger = ledger
        self.teacher = teacher
        try:
            self.zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name != "Asia/Shanghai":
                raise
            # Windows Python installations often omit the IANA database.
            # These 2026 business reports use the current China UTC+8 offset.
            self.zone = timezone(timedelta(hours=8), name="Asia/Shanghai")
        self.timezone_name = timezone_name
        self.rule_version = rule_version
        self.attribution = attribution
        self.cutoff_time = cutoff_time
        self.scope_start_date = date.fromisoformat(scope_start_date).isoformat()
        # One revision chain per teacher: changing a date rule must surface a diff.
        self.report_identity = teacher
        self.policy_snapshot = self._policy_snapshot()
        self._ensure_schema()
        self.review = PerformanceReview(self.store)
        from .history_coverage import ensure_schema
        ensure_schema(self.coverage_store)

    def _policy_snapshot(self):
        try:
            rows = self.store.all("SELECT key,value,status,source FROM performance_rules WHERE key IN ('reporting_cutoff','night_date_attribution')")
        except Exception:
            rows = []
        policy = {row["key"]: dict(row) for row in rows}
        persisted_cutoff = policy.get("reporting_cutoff", {}).get("value")
        try:
            boundary = time.fromisoformat(persisted_cutoff).isoformat(timespec="seconds") if persisted_cutoff else None
        except ValueError:
            boundary = None
        persisted_attribution = policy.get("night_date_attribution", {}).get("value")
        aligned = (boundary == "07:00:00" and persisted_attribution == "original_question_business_day_07:00"
                   and all(item.get("status") == "CONFIRMED" for item in policy.values()) and len(policy) == 2)
        return {"reporting_cutoff": boundary, "night_date_attribution": persisted_attribution,
                "aligned_with_question_day_07": aligned, "rules": policy}

    def _ensure_schema(self):
        self.store.connection.executescript("""
        CREATE TABLE IF NOT EXISTS performance_report_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          report_date TEXT NOT NULL,
          report_identity TEXT NOT NULL,
          version INTEGER NOT NULL,
          content_hash TEXT NOT NULL,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL,
          submitted_at TEXT,
          submitted_by TEXT,
          submission_evidence TEXT,
          previous_version INTEGER,
          diff TEXT NOT NULL,
          UNIQUE(report_identity, report_date, version)
        );
        CREATE INDEX IF NOT EXISTS performance_report_date_idx
          ON performance_report_versions(report_identity, report_date, version);
        """)

    def _bindings(self):
        return {row["id"]: dict(row) for row in self.store.all(
            "SELECT id, student_key, group_key, display_name FROM bindings")}

    def _links(self):
        try:
            rows = self.store.all("""SELECT l.unit_id, l.message_id, l.question_id, l.turn_id,
                l.question_version, l.link_kind, m.source_sent_at, m.observed_at
                FROM performance_links l JOIN messages m ON m.id=l.message_id""")
        except Exception:
            return defaultdict(list)
        links = defaultdict(list)
        for row in rows:
            links[row["unit_id"]].append(dict(row))
        return links

    def _attributed_date(self, unit):
        if self.attribution == "question_day_07":
            return self._question_business_date(unit.get("question_time"))
        if self.attribution == "question_date":
            return self._period_date(unit.get("question_time"))
        if self.attribution == "approval_date":
            return self._period_date(unit.get("review_at"))
        return self._period_date(unit.get("completed_at"))

    def _question_business_date(self, value):
        if not value:
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if stamp.tzinfo is None:
            return None
        local = stamp.astimezone(self.zone)
        return (local.date() - timedelta(days=1) if local.time().replace(tzinfo=None) < time(7)
                else local.date()).isoformat()

    def _question_numbers(self, unit_links):
        values = set()
        for link in unit_links:
            version = link.get("question_version")
            if not version:
                continue
            row = self.store.one("SELECT payload FROM question_versions WHERE id=?", (version,))
            if not row:
                continue
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            number = payload.get("number") or payload.get("question_number")
            if number:
                values.add(str(number))
        return sorted(values)

    def _period_date(self, value):
        if not value:
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if stamp.tzinfo is None:
            return None
        local = stamp.astimezone(self.zone)
        return (local.date() + timedelta(days=1) if local.time().replace(tzinfo=None) > self.cutoff_time
                else local.date()).isoformat()

    def _reason(self, unit):
        reasons = []
        eligibility = getattr(self.ledger, 'delivery_eligibility', None)
        if eligibility is not None:
            result = eligibility(unit)
            if not result['eligible']:
                reasons.append('实际交付需重新核验：' + result['reason'])
        if unit.get("status") not in CONFIRMED:
            reasons.append("计量尚未确认")
        if not unit.get("completed_at") or not unit.get("completion_outbox_id"):
            reasons.append("实际交付时间或证据缺失")
        elif _date(unit.get("completed_at"), self.zone) is None:
            reasons.append("实际交付时间待核验（须含时区）")
        if not unit.get("question_time") or not unit.get("first_message_id"):
            reasons.append("学生原始提问时间或消息证据缺失")
        if not unit.get("question_time_source"):
            reasons.append("原始提问时间来源缺失")
        if unit.get("status") in CONFIRMED and not unit.get("review_evidence"):
            reasons.append("人工核验依据缺失")
        if unit.get("category") not in NIGHT | REGULAR:
            reasons.append("时段归类待核验")
        if unit.get("question_type") not in COMPOSITE_TYPES | QUESTION_TYPES | {"独立知识点"}:
            reasons.append("题型归类待核验")
        if unit.get("question_time"):
            try:
                stamp = datetime.fromisoformat(unit["question_time"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("timezone missing")
                local = stamp.astimezone(self.zone).time().replace(tzinfo=None)
                expected = NIGHT if local >= time(23) or local < time(7) else REGULAR
                if unit.get("category") not in expected:
                    reasons.append("原始提问时间与时段归类不符")
            except (TypeError, ValueError):
                reasons.append("原始提问时间待核验")
        if unit.get("category") in NIGHT and unit.get("measure_unit") != "篇":
            reasons.append("夜间计量单位须为篇")
        if unit.get("category") in NIGHT and unit.get("confirmed_quantity") not in (1, 1.0):
            reasons.append("夜间单元须核准为1篇")
        if unit.get("confirmed_quantity") is None:
            reasons.append("核准数量缺失")
        elif (type(unit["confirmed_quantity"]) not in (int, float)
              or not math.isfinite(unit["confirmed_quantity"]) or unit["confirmed_quantity"] <= 0):
            reasons.append("核准数量须为有效正数")
        if unit.get("category") in REGULAR and unit.get("question_type") in COMPOSITE_TYPES and unit.get("measure_unit") != "篇":
            reasons.append("白天综合计量单位须为篇")
        if unit.get("category") in REGULAR and unit.get("question_type") in QUESTION_TYPES and unit.get("measure_unit") != "题":
            reasons.append("语法听力计量单位须为题")
        if unit.get("category") in REGULAR and unit.get("question_type") in QUESTION_TYPES:
            actual = unit.get("actual_question_count")
            if type(actual) not in (int, float) or not math.isfinite(actual) or actual < 1 or actual != int(actual):
                reasons.append("语法听力实际题数待核验")
        return "；".join(reasons)

    def build(self, report_date: str) -> dict:
        day = date.fromisoformat(report_date).isoformat()
        if day < self.scope_start_date:
            raise ValueError("Report date precedes configured scope start")
        bindings = self._bindings()
        links = self._links()
        units = [dict(unit) for unit in self.ledger.list_units()]
        summary = Counter()
        groups = defaultdict(Counter)
        details = []
        pending = []
        month_prefix = day[:7]
        month = Counter()
        for unit in sorted(units, key=lambda item: item["id"]):
            binding = bindings.get(unit.get("binding_id"), {})
            group = unit.get("group_key") or binding.get("group_key") or "未知群聊"
            student = unit.get("display_name") or unit.get("student_key") or binding.get("display_name") or binding.get("student_key") or "未知学生"
            asked_date = (self._question_business_date(unit.get("question_time")) if self.attribution == "question_day_07"
                          else self._period_date(unit.get("question_time")))
            completed_date = (_date(unit.get("completed_at"), self.zone) if self.attribution == "question_day_07"
                              else self._period_date(unit.get("completed_at")))
            review_date = (_date(unit.get("review_at"), self.zone) if self.attribution == "question_day_07"
                           else self._period_date(unit.get("review_at")))
            attributed = self._attributed_date(unit)
            unit_links = sorted(links.get(unit["id"], []), key=lambda item: (item.get("message_id") or "", item.get("link_kind") or ""))
            followups = sum(1 for link in unit_links if str(link.get("link_kind") or "").upper() in {"FOLLOWUP", "追问"})
            daily_followups = sum(1 for link in unit_links if str(link.get("link_kind") or "").upper() in {"FOLLOWUP", "追问"}
                                  and (self._question_business_date(link.get("source_sent_at")) if self.attribution == "question_day_07"
                                       else self._period_date(link.get("source_sent_at"))) == day)
            reason = self._reason(unit)
            if self.attribution == "question_day_07" and not self.policy_snapshot["aligned_with_question_day_07"]:
                reason = (reason + "；" if reason else "") + "提问业务日配置与持久化已确认规则不一致"
            included = not reason and attributed is not None and unit.get("status") in CONFIRMED
            category = "夜间" if unit.get("category") in NIGHT else "常规" if unit.get("category") in REGULAR else "待核验"
            detail = {
                "counting_unit_id": unit["id"], "student": student, "group": group,
                "question_type": unit.get("question_type"), "material_id": unit.get("material_id"),
                "topic_key": unit.get("topic_key"), "first_message_id": unit.get("first_message_id"),
                "linked_message_ids": sorted({link["message_id"] for link in unit_links if link.get("message_id")}),
                "linked_question_ids": sorted({link["question_id"] for link in unit_links if link.get("question_id")}),
                "linked_question_versions": sorted({link["question_version"] for link in unit_links if link.get("question_version")}),
                "student_question_numbers": self._question_numbers(unit_links),
                "student_key": unit.get("student_key") or binding.get("student_key"),
                "question_time": unit.get("question_time"), "question_time_source": unit.get("question_time_source"),
                "night_window_date": unit.get("night_window_date"),
                "first_response_at": unit.get("first_response_at"),
                "completed_at": unit.get("completed_at"), "completion_outbox_id": unit.get("completion_outbox_id"),
                "category": category, "category_basis": unit.get("category_basis"),
                "measure_unit": unit.get("measure_unit"), "confirmed_quantity": unit.get("confirmed_quantity"),
                "actual_question_count": unit.get("actual_question_count"),
                "approved_conversion": unit.get("approved_conversion"), "followup_turns": followups,
                "grouping_reason": unit.get("grouping_reason"), "status": unit.get("status"),
                "review_actor": unit.get("review_actor"), "review_at": unit.get("review_at"),
                "review_evidence": unit.get("review_evidence"),
                "timeliness_status": unit.get("timeliness_status"),
                "rule_version": unit.get("rule_version") or self.rule_version,
                "attributed_date": attributed, "included": included and attributed == day,
                "exclusion_reason": reason or ("不属于本日报归属日期" if attributed != day else None),
            }
            if asked_date == day:
                summary["new_units"] += 1
                groups[group]["new_units"] += 1
            delivery_valid = (not hasattr(self.ledger, 'delivery_eligibility')
                              or self.ledger.delivery_eligibility(unit)['eligible'])
            if completed_date == day and unit.get("completion_outbox_id") and delivery_valid:
                summary["completed_units"] += 1
                groups[group]["completed_units"] += 1
                summary["first_answer_units"] += 1
                groups[group]["first_answer_units"] += 1
            if review_date == day and unit.get("status") in CONFIRMED and unit.get("confirmed_quantity") and delivery_valid:
                approval_key = "approved_night_articles" if category == "夜间" else (
                    "approved_regular_articles" if unit.get("measure_unit") == "篇" else
                    f"approved_quantity:{unit.get('measure_unit') or '待核验'}:{unit.get('question_type') or '其他'}")
                summary[approval_key] += unit["confirmed_quantity"]
                groups[group][approval_key] += unit["confirmed_quantity"]
            if daily_followups:
                summary["followup_turns"] += daily_followups
                groups[group]["followup_turns"] += daily_followups
            if attributed == day or asked_date == day or completed_date == day or review_date == day or daily_followups:
                details.append(detail)
            if included and attributed and attributed[:7] == month_prefix and self.scope_start_date <= attributed <= day:
                qty = unit["confirmed_quantity"]
                if category == "夜间":
                    month["night_articles"] += qty
                elif unit.get("question_type") in COMPOSITE_TYPES:
                    month["regular_articles"] += qty
                    month["day_composite_articles"] += qty
                elif unit.get("question_type") in QUESTION_TYPES:
                    month["grammar_listening_actual_questions"] += unit.get("actual_question_count") if unit.get("actual_question_count") is not None else qty
                    if unit.get("approved_conversion") is not None:
                        month["grammar_listening_converted_questions"] += unit["approved_conversion"]
            if included and attributed == day:
                qty = unit["confirmed_quantity"]
                if category == "夜间":
                    summary["night_articles"] += qty
                    groups[group]["night_articles"] += qty
                elif unit.get("question_type") in COMPOSITE_TYPES:
                    key = f"regular_articles:{unit.get('question_type') or '其他'}"
                    summary[key] += qty
                    groups[group][key] += qty
                    summary["day_composite_articles"] += qty
                    groups[group]["day_composite_articles"] += qty
                elif unit.get("question_type") in QUESTION_TYPES:
                    key = f"actual_questions:{unit.get('question_type')}"
                    actual = unit.get("actual_question_count") if unit.get("actual_question_count") is not None else qty
                    summary[key] += actual
                    groups[group][key] += actual
                    summary["grammar_listening_actual_questions"] += actual
                    groups[group]["grammar_listening_actual_questions"] += actual
                    if unit.get("approved_conversion") is not None:
                        key = f"approved_converted_questions:{unit.get('question_type')}"
                        summary[key] += unit["approved_conversion"]
                        groups[group][key] += unit["approved_conversion"]
                        summary["grammar_listening_converted_questions"] += unit["approved_conversion"]
                        groups[group]["grammar_listening_converted_questions"] += unit["approved_conversion"]
                elif unit.get("measure_unit") == "独立知识点":
                    summary["independent_knowledge"] += qty
                    groups[group]["independent_knowledge"] += qty
            completion_asof_day = (self._question_business_date(unit.get("completed_at"))
                                   if self.attribution == "question_day_07" else completed_date)
            unfinished_at_day_end = completion_asof_day is None or completion_asof_day > day
            if unit.get("status") not in EXCLUDED and (asked_date is None or asked_date <= day) and (reason or unfinished_at_day_end):
                pending.append({"counting_unit_id": unit["id"], "student": student, "group": group,
                                "question_time": unit.get("question_time"),
                                "reason": reason or "截至该提问业务日结束尚未完成",
                                "first_message_id": unit.get("first_message_id"),
                                "status": "后续已确认" if not reason and unit.get("status") in CONFIRMED else unit.get("status")})
            if unit.get("requested_conversion") and unit.get("approved_conversion") is None and (asked_date is None or asked_date <= day):
                pending.append({"counting_unit_id": unit["id"], "student": student, "group": group,
                                "question_time": unit.get("question_time"), "reason": "扩展讲解折算待核准",
                                "first_message_id": unit.get("first_message_id"), "status": unit.get("status")})
            for link in unit_links:
                if str(link.get("link_kind") or "").upper() not in {"FOLLOWUP", "追问"} or link.get("source_sent_at"):
                    continue
                observed_date = (self._question_business_date(link.get("observed_at"))
                                 if self.attribution == "question_day_07" else self._period_date(link.get("observed_at")))
                if observed_date is None or observed_date <= day:
                    pending.append({"counting_unit_id": unit["id"], "student": student, "group": group,
                                    "question_time": None, "reason": "追问原始发送时间待核验，未归入每日追问轮次",
                                    "first_message_id": link.get("message_id"), "status": "时间待核验"})
        for item in self.ledger.list_unlinked_messages():
            activity_date = (self._question_business_date(item.get("source_sent_at")) if self.attribution == "question_day_07"
                             else self._period_date(item.get("source_sent_at")))
            activity_date = activity_date or (self._question_business_date(item.get("observed_at"))
                                              if self.attribution == "question_day_07" else self._period_date(item.get("observed_at")))
            if activity_date and activity_date > day:
                continue
            pending.append({"counting_unit_id": "", "student": item.get("display_name") or item.get("student_key") or "未知学生",
                            "group": item.get("group_key") or "未知群聊", "question_time": item.get("source_sent_at"),
                            "reason": "消息尚未归入绩效计量单元", "first_message_id": item.get("id"), "status": "待核验"})
        start = (datetime.combine(date.fromisoformat(day) - timedelta(days=1), self.cutoff_time, self.zone)
                 + timedelta(microseconds=1)).astimezone(timezone.utc).isoformat()
        end = (datetime.combine(date.fromisoformat(day), self.cutoff_time, self.zone)
               + timedelta(microseconds=1)).astimezone(timezone.utc).isoformat()
        anomaly = self.review.anomaly_summary(start=start, end=end)
        for state, prefix in (("SUSPECTED", "suspected_anomalies"), ("CONFIRMED", "confirmed_anomalies")):
            summary[prefix + "_events"] = anomaly["counts"][state]["events"]
            summary[prefix + "_units"] = anomaly["counts"][state]["units"]
        for row in self.store.all("""SELECT a.id,a.unit_id,a.kind,a.evidence,u.first_message_id,u.question_time,
            b.display_name,b.group_key FROM performance_anomalies a
            JOIN performance_units u ON u.id=a.unit_id JOIN bindings b ON b.id=u.binding_id
            WHERE a.state='SUSPECTED' AND a.created_at>=? AND a.created_at<?""", (start, end)):
            pending.append({"counting_unit_id": row["unit_id"], "student": row["display_name"], "group": row["group_key"],
                            "question_time": row["question_time"], "reason": "疑似异常待人工核实：" + row["kind"],
                            "first_message_id": row["first_message_id"], "status": "待核验"})
        try:
            from .history_coverage import coverage_summary
            coverage = coverage_summary(self.coverage_store, self.scope_start_date, day, ensure=False)
        except ImportError:
            coverage = {"scope": "ALL_HISTORICAL_GROUPS", "start_date": self.scope_start_date,
                        "end_date": day, "inventory_complete": False, "complete": False,
                        "groups": [], "notes": ["历史群聊清单和日期覆盖尚未核验"]}
        if not coverage.get("complete"):
            pending.append({"counting_unit_id": "", "student": "", "group": "全部群聊（含已退出）",
                            "question_time": "", "reason": "历史群聊及记录覆盖待核验；当前数量仅代表已入账证据",
                            "first_message_id": "", "status": "数据覆盖待核验"})
        for group_info in coverage.get("groups", []):
            if not group_info.get("coverage_complete"):
                pending.append({"counting_unit_id": "", "student": "", "group": group_info.get("display_name"),
                                "question_time": "", "reason": "群聊历史覆盖待核验（" + str(group_info.get("membership")) + "）",
                                "first_message_id": "", "status": "数据覆盖待核验"})
        if self.attribution == "question_day_07" and not self.policy_snapshot["aligned_with_question_day_07"]:
            pending.append({"counting_unit_id": "", "student": "", "group": "统计规则",
                            "question_time": "", "reason": "07:00提问业务日配置与持久化规则不一致，须复核后计入正式数量",
                            "first_message_id": "", "status": "规则待核验"})
        for key in ("new_units", "completed_units", "first_answer_units", "followup_turns", "night_articles",
                    "day_composite_articles", "grammar_listening_actual_questions"):
            summary.setdefault(key, 0)
        for key in ("night_articles", "day_composite_articles", "grammar_listening_actual_questions"):
            month.setdefault(key, 0)
        summary["pending_records"] = len(pending)
        summary["unfinished_units"] = len({item["counting_unit_id"] for item in pending
                                           if item["counting_unit_id"] and ("尚未完成" in item["reason"] or "交付" in item["reason"])})
        # Coverage describes discovery completeness, not whether the evidenced
        # subset has useful counts. Keep these two questions explicit.
        fields = ("day_composite_articles", "grammar_listening_actual_questions", "night_articles")
        counted = [item for item in details if item["included"]]
        verified_subtotals = {
            "scope": "VERIFIED_LOCAL_LEDGER_ONLY", "report_date": day,
            "totals": {key: summary[key] for key in fields},
            "included_unit_count": len(counted),
            "included_unit_ids": [item["counting_unit_id"] for item in counted],
            "group_count": len({item["group"] for item in counted}),
            "monthly_totals": {key: month[key] for key in fields},
            "monthly_start_date": max(self.scope_start_date, day[:7] + "-01"),
            "complete_history": coverage.get("complete") is True,
            "label": "已核验本地记录小计；不代表全部群历史总数",
        }
        native_table = self.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='native_chat_staging'")
        native_count = self.store.one("SELECT COUNT(*) FROM native_chat_staging")[0] if native_table else 0
        missing_data = {
            "scope": "LOCAL_DISCOVERY_QUEUE_AS_OF_REPORT",
            "pending_unit_count": len({item["counting_unit_id"] for item in pending if item["counting_unit_id"]}),
            "unlinked_message_count": len({item["first_message_id"] for item in pending
                                            if not item["counting_unit_id"] and item["first_message_id"]}),
            "native_acquisition_count": native_count,
            "native_acquisition_scope": "ALL_STAGED_ACQUISITIONS_UNKNOWN_ORIGINAL_DATE",
            "native_acquisitions_are_performance": False,
            "reason_counts": dict(sorted(Counter(item["reason"] for item in pending).items())),
            "history_complete": coverage.get("complete") is True,
        }
        return {
            "report_date": day, "teacher": self.teacher, "timezone": self.timezone_name,
            "rule_version": self.rule_version, "attribution": self.attribution,
            "reporting_cutoff": self.policy_snapshot["reporting_cutoff"],
            "activity_day_cutoff": self.cutoff_time.isoformat(),
            "question_day_boundary": "07:00:00" if self.attribution == "question_day_07" else None,
            "persisted_policy": self.policy_snapshot,
            "scope": "ALL_HISTORICAL_GROUPS", "scope_start_date": self.scope_start_date,
            "coverage": coverage,
            "template": "自拟日报草稿；非机构原始填报模板", "source": "本地绩效台账",
            "summary": dict(sorted(summary.items())),
            "verified_subtotals": verified_subtotals,
            "formal_totals": {key: summary[key] if coverage.get("complete") is True else None for key in fields},
            "missing_data": missing_data,
            "monthly_cumulative": dict(sorted(month.items())),
            "groups": {key: dict(sorted(value.items())) for key, value in sorted(groups.items())},
            "details": details, "pending": pending, "anomalies": anomaly,
        }

    def generate(self, report_date: str, *, retroactive: bool = False) -> dict:
        with resource_lock(self.store.path + ".performance-report.lock"):
            return self._generate_locked(report_date, retroactive=retroactive)

    def _generate_locked(self, report_date: str, *, retroactive: bool = False) -> dict:
        self.ledger.reconcile_stale_deliveries()
        payload = self.build(report_date)
        content = _canonical(payload)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        previous = self.store.one(
            "SELECT * FROM performance_report_versions WHERE report_identity=? AND report_date=? ORDER BY version DESC LIMIT 1",
            (self.report_identity, payload["report_date"]))
        if previous and previous["content_hash"] == digest:
            return {"version": previous["version"], "changed": False, "submitted": bool(previous["submitted_at"]),
                    "payload": {**json.loads(previous["payload"]), "generated_at": previous["created_at"]},
                    "diff": json.loads(previous["diff"])}
        if previous and previous["submitted_at"] and not retroactive:
            return {"version": previous["version"] + 1, "changed": True, "preview_only": True,
                    "payload": {**payload, "generated_at": datetime.now(timezone.utc).isoformat()},
                    "diff": self._diff(json.loads(previous["payload"]), payload)}
        version = previous["version"] + 1 if previous else 1
        diff = self._diff(json.loads(previous["payload"]), payload) if previous else {}
        created_at = datetime.now(timezone.utc).isoformat()
        with self.store.transaction():
            self.store.execute("""INSERT INTO performance_report_versions
                (report_date, report_identity, version, content_hash, payload, created_at, previous_version, diff)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["report_date"], self.report_identity, version, digest, content,
                 created_at, previous["version"] if previous else None, _canonical(diff)))
        return {"version": version, "changed": True, "preview_only": False,
                "payload": {**payload, "generated_at": created_at}, "diff": diff}

    @staticmethod
    def _diff(before: dict, after: dict) -> dict:
        change = {}
        for section in ("summary", "monthly_cumulative", "groups", "anomalies", "coverage", "persisted_policy",
                        "verified_subtotals", "formal_totals", "missing_data"):
            left, right = before.get(section, {}), after.get(section, {})
            item = {key: {"before": left.get(key), "after": right.get(key)}
                    for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)}
            if item:
                change[section] = item
        old = {x["counting_unit_id"]: x for x in before.get("details", [])}
        new = {x["counting_unit_id"]: x for x in after.get("details", [])}
        changed = {key: {"before": old.get(key), "after": new.get(key)}
                   for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)}
        if changed:
            change["details"] = changed
        old_pending = {_canonical(x) for x in before.get("pending", [])}
        new_pending = {_canonical(x) for x in after.get("pending", [])}
        if old_pending != new_pending:
            change["pending"] = {"removed": [json.loads(x) for x in sorted(old_pending - new_pending)],
                                 "added": [json.loads(x) for x in sorted(new_pending - old_pending)]}
        for field in ("rule_version", "attribution", "timezone", "teacher", "reporting_cutoff", "activity_day_cutoff", "question_day_boundary", "scope", "scope_start_date"):
            if before.get(field) != after.get(field):
                change[field] = {"before": before.get(field), "after": after.get(field)}
        return change

    def mark_submitted(self, report_date: str, version: int, *, actor: str, evidence: str):
        if not actor.strip() or not evidence.strip():
            raise ValueError("Submission actor and evidence are required")
        with self.store.transaction():
            result = self.store.execute("""UPDATE performance_report_versions
                SET submitted_at=?, submitted_by=?, submission_evidence=?
                WHERE report_identity=? AND report_date=? AND version=? AND submitted_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(), actor, evidence, self.report_identity, date.fromisoformat(report_date).isoformat(), version))
        if result.rowcount != 1:
            raise ValueError("Report version missing or already submitted")

    def list_versions(self, report_date: str) -> list[dict]:
        return [dict(row) for row in self.store.all("""SELECT id, report_date, version, content_hash,
            created_at, submitted_at, submitted_by, submission_evidence, previous_version, diff
            FROM performance_report_versions WHERE report_identity=? AND report_date=? ORDER BY version""", (self.report_identity, report_date))]

    def catch_up(self, start_date: str, end_date: str, *, retroactive: bool = False) -> list[dict]:
        from datetime import timedelta
        current, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if end < current:
            raise ValueError("end_date precedes start_date")
        results = []
        while current <= end:
            results.append(self.generate(current.isoformat(), retroactive=retroactive))
            current += timedelta(days=1)
        return results


def scheduled_due_dates(reports: PerformanceReports, *, now: datetime, generated_at_local: time,
                        start_date: str, include_saved: bool = False) -> list[str]:
    """Return missing dates eligible for local catch-up; caller schedules the process."""
    if now.tzinfo is None:
        raise ValueError("now needs a timezone")
    local_now = now.astimezone(reports.zone)
    scheduled_day = local_now.date() if local_now.time().replace(tzinfo=None) >= generated_at_local else local_now.date() - timedelta(days=1)
    last = scheduled_day if generated_at_local > reports.cutoff_time else scheduled_day - timedelta(days=1)
    if reports.attribution == "question_day_07":
        # A question day labelled d remains open until d+1 at 07:00.
        latest_closed_question_day = (local_now.date() - timedelta(days=1)
                                      if local_now.time().replace(tzinfo=None) >= time(7)
                                      else local_now.date() - timedelta(days=2))
        last = min(last, latest_closed_question_day)
    first = date.fromisoformat(start_date)
    if first > last:
        return []
    existing = set() if include_saved else {row[0] for row in reports.store.all("SELECT DISTINCT report_date FROM performance_report_versions WHERE report_identity=?", (reports.report_identity,))}
    return [date.fromordinal(n).isoformat() for n in range(first.toordinal(), last.toordinal() + 1)
            if date.fromordinal(n).isoformat() not in existing]
