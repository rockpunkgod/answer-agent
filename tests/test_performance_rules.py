import unittest

from helpdesk.performance_rules import classify_night, assess_timeliness, estimate_amount, RULE_HISTORY, question_business_date


class PerformanceRuleTests(unittest.TestCase):
    def night(self, when, **kw):
        return classify_night(when, time_evidence="original-message-1",
                              end_hour=7, end_inclusive=False, **kw)

    def test_original_time_is_only_classification_input(self):
        self.assertEqual(self.night("2026-09-29T22:50:00+08:00")["classification"], "REGULAR")
        self.assertEqual(self.night("2026-09-29T22:58:00+08:00")["classification"], "REGULAR")
        self.assertEqual(self.night("2026-09-29T23:10:00+08:00")["classification"], "NIGHT")

    def test_night_boundaries_and_utc_conversion(self):
        for value in ("2026-09-29T23:00:00+08:00", "2026-09-29T15:00:00+00:00"):
            self.assertEqual(self.night(value)["classification"], "NIGHT")
        self.assertEqual(self.night("2026-09-30T06:59:59+08:00")["window_date"], "2026-09-29")
        self.assertEqual(self.night("2026-09-30T07:00:00+08:00")["classification"], "REGULAR")

    def test_missing_time_or_unconfirmed_boundary_is_pending(self):
        for value in (None, "2026-09-29T23:10:00", "invalid"):
            self.assertEqual(self.night(value)["classification"], "PENDING")
        self.assertEqual(classify_night("2026-09-30T07:00:00+08:00", time_evidence="m", end_hour=7, end_inclusive=None)["classification"], "PENDING")
        self.assertEqual(classify_night("2026-09-30T06:00:00+08:00", time_evidence="m", end_hour=None)["classification"], "PENDING")
        self.assertEqual(classify_night("2026-09-30T23:00:00+08:00", time_evidence="")["classification"], "PENDING")

    def test_confirmed_default_seven_boundary(self):
        before = classify_night("2026-09-30T06:59:59.999999+08:00", time_evidence="m")
        self.assertEqual((before["classification"], before["window_date"]), ("NIGHT", "2026-09-29"))
        self.assertEqual(classify_night("2026-09-30T07:00:00+08:00", time_evidence="m")["classification"], "REGULAR")

    def test_business_date_at_seven_and_offset(self):
        self.assertEqual(str(question_business_date("2026-09-30T06:59:59+08:00")), "2026-09-29")
        self.assertEqual(str(question_business_date("2026-09-30T07:00:00+08:00")), "2026-09-30")
        self.assertEqual(str(question_business_date("2026-09-29T23:00:00+00:00")), "2026-09-30")

    def policy(self, **extra):
        return {"confirmed": True, "evidence": "test-approved-roster",
                "coverage_start": "2026-09-29T00:00:00+08:00", "coverage_end": "2026-09-30T00:00:00+08:00",
                "working_intervals": [["2026-09-29T09:00:00+08:00", "2026-09-29T18:00:00+08:00"]],
                "response_minutes": 15, "answer_minutes": 60} | extra

    def test_unconfirmed_schedule_preserves_raw_wait(self):
        result = assess_timeliness("2026-09-29T08:00:00+08:00", "2026-09-29T09:10:00+08:00",
                                   "2026-09-29T09:55:00+08:00", time_evidence="m")
        self.assertEqual(result["raw_response_minutes"], 70)
        self.assertIsNone(result["adjusted_response_minutes"])
        self.assertEqual(result["status"], "PENDING")

    def test_confirmed_shift_adjusts_raw_wait_separately(self):
        result = assess_timeliness("2026-09-29T08:00:00+08:00", "2026-09-29T09:10:00+08:00",
                                   "2026-09-29T09:55:00+08:00", time_evidence="m", policy=self.policy())
        self.assertEqual(result["raw_answer_minutes"], 115)
        self.assertEqual(result["adjusted_response_minutes"], 10)
        self.assertEqual(result["adjusted_answer_minutes"], 55)
        self.assertEqual(result["status"], "COMPLIANT")

    def test_waiting_for_image_does_not_stop_timer_without_approval(self):
        result = assess_timeliness("2026-09-29T09:00:00+08:00", None, None, time_evidence="m",
                                   as_of="2026-09-29T11:00:00+08:00", policy=self.policy())
        self.assertEqual(result["status"], "SUSPECTED_LATE")
        self.assertFalse(result["automatic_penalty"])
        pending = assess_timeliness("2026-09-29T09:00:00+08:00", None, None, time_evidence="m",
                                   as_of="2026-09-29T11:00:00+08:00", policy=self.policy(),
                                   exceptions=[{"approved": False, "reason": "waiting_image"}])
        self.assertEqual(pending["status"], "PENDING")
        self.assertEqual(pending["raw_answer_minutes"], 120)

    def test_overlapping_approved_exceptions_subtracted_once(self):
        exceptions = [{"approved": True, "evidence": "approved-meal", "start": f"2026-09-29T{a}+08:00",
                       "end": f"2026-09-29T{b}+08:00"} for a, b in (("12:00:00", "12:30:00"), ("12:15:00", "12:45:00"))]
        result = assess_timeliness("2026-09-29T12:00:00+08:00", "2026-09-29T12:50:00+08:00",
                                   "2026-09-29T13:30:00+08:00", time_evidence="m", policy=self.policy(), exceptions=exceptions)
        self.assertEqual(result["adjusted_response_minutes"], 5)
        self.assertEqual(result["adjusted_answer_minutes"], 45)

    def test_bulk_exception_uses_two_milestones(self):
        policy = self.policy(bulk_required=True, bulk={"first_two_completed_at": "2026-09-29T09:50:00+08:00",
                        "first_two_deadline": "2026-09-29T10:00:00+08:00", "all_deadline": "2026-09-29T18:00:00+08:00",
                        "delivery_evidence": ["delivery-1", "delivery-2", "delivery-all"]})
        result = assess_timeliness("2026-09-29T09:00:00+08:00", "2026-09-29T09:05:00+08:00",
                                   "2026-09-29T17:00:00+08:00", time_evidence="m", policy=policy)
        self.assertTrue(result["bulk_compliant"])
        self.assertEqual(result["status"], "COMPLIANT")

    def test_incomplete_roster_does_not_produce_false_zero(self):
        result = assess_timeliness("2026-09-28T09:00:00+08:00", None, None, time_evidence="m", policy=self.policy())
        self.assertIsNone(result["adjusted_answer_minutes"])
        self.assertEqual(result["status"], "PENDING")

    def test_amount_requires_confirmed_method_and_night_price(self):
        self.assertIsNone(estimate_amount(regular_articles=301)["amount"])
        policy = {"confirmed": True, "evidence": "approval", "tier_method": "whole_tier"}
        self.assertEqual(estimate_amount(regular_articles=301, policy=policy)["amount"], "1354.50")
        self.assertEqual(estimate_amount(regular_articles=301, policy=policy | {"tier_method": "progressive"})["amount"], "1054.50")
        self.assertIsNone(estimate_amount(regular_articles=0, night_articles=1, policy=policy)["amount"])
        self.assertIsNone(estimate_amount(regular_articles=0, listening_grammar_units=6, policy=policy)["amount"])

    def test_estimate_never_claims_approved_salary(self):
        policy = {"confirmed": True, "evidence": "approval", "tier_method": "whole_tier",
                  "night_price_unit": "篇", "night_price": "6", "listening_grammar_additive": True}
        result = estimate_amount(regular_articles=0, night_articles=1, listening_grammar_units=6, policy=policy)
        self.assertEqual(result["status"], "暂估")
        self.assertEqual(result["amount"], "9.00")
        self.assertFalse(RULE_HISTORY[0]["original_document_read"])
        self.assertFalse(RULE_HISTORY[-1]["night_price_confirmed"])


if __name__ == "__main__":
    unittest.main()
