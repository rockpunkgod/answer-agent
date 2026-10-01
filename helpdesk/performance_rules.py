"""Deterministic policy helpers. Unknown policy is never a zero or a penalty.

Section 15 is a user amendment, not a modification of the unavailable PDF.
All supplied timestamps must carry an offset; acquisition time is not evidence.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RULE_VERSION = "2026-09-30-user-night-0700-v2"
RULE_HISTORY = (
    {"version": "user-section14-excerpt", "source": "用户提供第十四节摘录",
     "source_location": "D:/Codex/attachments/d118cc96-e5d0-4a45-8634-070978fba792/已粘贴的文本.txt",
     "source_sha256": "74c02c0f8f82c9c8ee813adccc0fa11b0f00e0d6727d32b3a15f9a60e0e51c0f",
     "original_document": "2026英语答疑老师绩效考核方案(3).pdf",
     "original_document_read": False,
     "night_basis": "提问与完成均在同一夜间窗口（已被第十五节替代）"},
    {"version": "2026-09-30-user-section15-v1", "source": "用户最新确认第十五节",
     "source_location": "本会话：十五、修正夜间答疑的绩效统计口径",
     "night_basis": "仅学生首次独立提问原始发送时间；23:00含",
     "night_unit": "篇", "night_price_confirmed": False,
     "retroactive_application_authorized": False},
    {"version": RULE_VERSION, "source": "用户确认次日七点前归前一天",
     "source_location": "本会话：到次日早上七点前问的都算先天的",
     "interpretation": "先天按上下文理解为前一天",
     "night_start": "23:00", "night_start_inclusive": True,
     "night_end": "07:00", "night_end_inclusive": False,
     "reporting_cutoff": "07:00", "attribution": "original_question_business_day",
     "night_unit": "篇", "night_price_confirmed": False,
     "retroactive_application_authorized": False},
)


def business_zone(name="Asia/Shanghai"):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # Windows without tzdata: the explicitly supported initial fixed UTC+8.
        if name in ("Asia/Shanghai", "UTC+08:00"):
            return timezone(timedelta(hours=8), "UTC+08:00")
        raise ValueError("Business timezone unavailable; install/configure timezone data")


def timestamp(value):
    if not value:
        raise ValueError("Missing original timestamp")
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp must include an explicit UTC offset")
    return result


def question_business_date(value, timezone_name="Asia/Shanghai"):
    """Date label for the user-confirmed [07:00, next 07:00) window."""
    return (timestamp(value).astimezone(business_zone(timezone_name)) - timedelta(hours=7)).date()


def classify_night(asked_at, *, time_evidence, timezone_name="Asia/Shanghai",
                   end_hour=7, end_inclusive=False):
    result = {"classification": "PENDING", "reason": "时间待核验",
              "window_date": None, "rule_version": RULE_VERSION}
    if not isinstance(time_evidence, (str, dict)) or not time_evidence or (isinstance(time_evidence, str) and not time_evidence.strip()):
        return result
    try:
        local = timestamp(asked_at).astimezone(business_zone(timezone_name))
    except (ValueError, TypeError):
        return result
    if end_hour is not None and (isinstance(end_hour, bool) or not 0 <= end_hour < 23):
        raise ValueError("Night end hour must be within [0,23)")
    clock = local.hour + local.minute / 60 + local.second / 3600 + local.microsecond / 3.6e9
    if clock >= 23:
        result.update(classification="NIGHT", reason="原始提问时间达到23:00（含）；与完成时间无关",
                      window_date=local.date().isoformat())
    elif end_hour is None:
        result["reason"] = "夜间结束时刻尚未确认，不能判断23:00以前时段"
    elif clock == end_hour and end_inclusive is None:
        result["reason"] = "夜间结束边界是否包含尚未确认"
    elif clock < end_hour or (clock == end_hour and end_inclusive is True):
        result.update(classification="NIGHT", reason="原始提问处于已配置的跨午夜夜间窗口",
                      window_date=(local.date() - timedelta(days=1)).isoformat())
    else:
        result.update(classification="REGULAR", reason="原始提问时间不在已配置夜间窗口")
    return result


def assess_timeliness(asked_at, replied_at, completed_at, *, time_evidence,
                      policy=None, as_of=None, exceptions=()):
    """Evaluate only a fully confirmed schedule using explicit aware intervals.

    policy: confirmed, evidence, coverage_start/end, working_intervals (pairs),
    response_minutes, answer_minutes. Calendar/holiday/vacation schedules are
    expanded by an operator into dated intervals, avoiding guessed work hours.
    Meal/pause exclusions require approved=True and documentary evidence.
    Optional carryover_deadline and bulk {first_two_completed_at, deadline}
    remain independent checks; missing required facts are pending.
    """
    out = {"status": "PENDING", "reason": "时段及例外规则待确认",
           "raw_response_minutes": None, "raw_answer_minutes": None,
           "adjusted_response_minutes": None, "adjusted_answer_minutes": None,
           "response_compliant": None, "answer_compliant": None,
           "carryover_compliant": None, "bulk_compliant": None,
           "automatic_penalty": False}
    try:
        asked = timestamp(asked_at)
        reply = timestamp(replied_at) if replied_at else None
        done = timestamp(completed_at) if completed_at else None
        observed = timestamp(as_of) if as_of else None
        if not time_evidence:
            return out | {"reason": "原始提问时间证据缺失"}
        if any(t is not None and t < asked for t in (reply, done, observed)):
            return out | {"reason": "时间先后矛盾"}
        for field, end in (("raw_response_minutes", reply or observed),
                           ("raw_answer_minutes", done or observed)):
            if end is not None:
                out[field] = (end - asked).total_seconds() / 60
        if not policy or policy.get("confirmed") is not True or not policy.get("evidence"):
            return out
        coverage_start, coverage_end = timestamp(policy["coverage_start"]), timestamp(policy["coverage_end"])
        latest = max(t for t in (asked, reply, done, observed) if t is not None)
        if asked < coverage_start or latest > coverage_end:
            return out | {"reason": "值班表未覆盖完整考核期间"}
        intervals = sorted((timestamp(a), timestamp(b)) for a, b in policy["working_intervals"])
        if any(a >= b or a < coverage_start or b > coverage_end for a, b in intervals):
            raise ValueError("Invalid working interval")
        if any(intervals[i][0] < intervals[i-1][1] for i in range(1, len(intervals))):
            raise ValueError("Overlapping working intervals")
        excluded = []
        for item in exceptions:
            if item.get("approved") is not True or not item.get("evidence"):
                return out | {"reason": "用餐或顺延例外尚未核准，不自动停表"}
            a, b = timestamp(item["start"]), timestamp(item["end"])
            if a >= b:
                raise ValueError("Invalid exception interval")
            excluded.append((a, b))
        # Union exceptions so an overlapping exception never subtracts twice.
        merged = []
        for a, b in sorted(excluded):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))

        def elapsed(end):
            if end is None:
                return None
            seconds = 0
            for start, stop in intervals:
                a, b = max(start, asked), min(stop, end)
                if a >= b:
                    continue
                seconds += (b - a).total_seconds()
                for x, y in merged:
                    seconds -= max(0, (min(b, y) - max(a, x)).total_seconds())
            return seconds / 60

        for label, actual, limit in (("response", reply, policy["response_minutes"]),
                                     ("answer", done, policy["answer_minutes"])):
            if isinstance(limit, bool) or limit <= 0:
                raise ValueError("Invalid SLA threshold")
            duration = elapsed(actual or observed)
            out[f"adjusted_{label}_minutes"] = duration
            out[f"{label}_compliant"] = (duration <= limit if actual is not None else
                                         False if duration is not None and duration > limit else None)
        if policy.get("carryover_required"):
            deadline = policy.get("carryover_deadline")
            if not deadline:
                return out | {"reason": "夜间遗留题截止时间待确认"}
            deadline = timestamp(deadline)
            out["carryover_compliant"] = done <= deadline if done else False if observed and observed > deadline else None
        if policy.get("bulk_required"):
            bulk = policy.get("bulk", {})
            if not bulk.get("first_two_deadline") or not bulk.get("all_deadline"):
                return out | {"reason": "高峰或超过5道题的特殊截止规则待确认"}
            first_two = timestamp(bulk["first_two_completed_at"]) if bulk.get("first_two_completed_at") else None
            # The caller must supply actual delivery evidence for both milestones.
            if not bulk.get("delivery_evidence") or not first_two or not done:
                return out | {"reason": "至少2道及全部完成的交付证据待核验"}
            out["bulk_compliant"] = first_two <= timestamp(bulk["first_two_deadline"]) and done <= timestamp(bulk["all_deadline"])
            # Approved bulk treatment replaces the ordinary answer deadline.
            out["answer_compliant"] = out["bulk_compliant"]
        checks = [out["response_compliant"], out["answer_compliant"]]
        if policy.get("carryover_required"):
            checks.append(out["carryover_compliant"])
        if policy.get("bulk_required"):
            checks.append(out["bulk_compliant"])
        out["status"] = "SUSPECTED_LATE" if False in checks else "COMPLIANT" if all(v is True for v in checks) else "PENDING"
        out["reason"] = "按已确认时段核算；超时仅为疑似异常，不能自动扣款"
        return out
    except (ValueError, TypeError, KeyError) as exc:
        return out | {"status": "PENDING", "reason": f"时间或规则数据待核验：{exc}"}


def estimate_amount(*, regular_articles, listening_grammar_units=0, night_articles=0,
                    policy=None):
    """Independent estimate, never approved wages. Decimal results as strings."""
    pending = {"status": "规则待确认", "amount": None, "currency": "CNY", "reasons": []}
    quantities = (regular_articles, listening_grammar_units, night_articles)
    if any(isinstance(q, bool) or not isinstance(q, int) or q < 0 for q in quantities):
        return pending | {"reasons": ["数量必须为已核准的非负整数，未知不能填零"]}
    if not policy or policy.get("confirmed") is not True or not policy.get("evidence"):
        return pending | {"reasons": ["计价规则未确认"]}
    method = policy.get("tier_method")
    if method not in ("progressive", "whole_tier"):
        pending["reasons"].append("常规综合梯度为分段或全量尚未确认")
    if listening_grammar_units and policy.get("listening_grammar_additive") is not True:
        pending["reasons"].append("听力语法收入归入方式未确认")
    if night_articles and (policy.get("night_price_unit") != "篇" or policy.get("night_price") is None):
        pending["reasons"].append("夜间每篇单价未确认，不能沿用旧每题价格")
    if pending["reasons"]:
        return pending
    q = regular_articles
    if method == "whole_tier":
        rate = Decimal("3.5" if q <= 300 else "4.5" if q <= 500 else "5.5" if q <= 800 else "6")
        regular = q * rate
    else:
        regular = (min(q, 300) * Decimal("3.5") + min(max(q-300, 0), 200) * Decimal("4.5")
                   + min(max(q-500, 0), 300) * Decimal("5.5") + max(q-800, 0) * Decimal("6"))
    try:
        night_rate = Decimal(str(policy.get("night_price", 0)))
        if not night_rate.is_finite() or night_rate < 0:
            raise ValueError("Invalid night rate")
    except Exception:
        return pending | {"reasons": ["夜间单价无效"]}
    amounts = {"regular": regular, "listening_grammar": listening_grammar_units * Decimal("0.5"),
               "night": night_articles * night_rate}
    return {"status": "暂估", "amount": str(sum(amounts.values()).quantize(Decimal("0.01"))),
            "components": {k: str(v.quantize(Decimal("0.01"))) for k, v in amounts.items()},
            "currency": "CNY", "rule_evidence": policy["evidence"],
            "note": "不含未核准奖金、全勤、正式好评、异常系数；不是实发或已核定薪酬"}
