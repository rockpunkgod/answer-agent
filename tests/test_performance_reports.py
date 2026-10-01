import tempfile
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from helpdesk.storage import Store
from helpdesk.performance_reports import PerformanceReports, scheduled_due_dates
from tools.performance_report_schedule import run as run_schedule
from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent, new_id
from helpdesk.performance import PerformanceLedger
from helpdesk.history_coverage import record_observed_group, record_coverage
from helpdesk.service import Helpdesk, Incoming
from datetime import datetime, time


class FakeLedger:
    def __init__(self, units, unlinked=()):
        self.units = units
        self.unlinked = list(unlinked)

    def list_units(self):
        return self.units

    def list_unlinked_messages(self):
        return self.unlinked

    def reconcile_stale_deliveries(self):
        return []


def unit(uid, asked, done, category, question_type="阅读理解", measure="篇", quantity=1):
    return dict(id=uid, binding_id="b1", group_key="g1", display_name="演示学生",
                question_type=question_type, material_id="material-" + uid, topic_key="演示材料",
                first_message_id="message-" + uid, question_time=asked,
                question_time_source='{"source":"wecom_original"}',
                completed_at=done, completion_outbox_id="delivery-" + uid,
                category=category, category_basis="学生原始发送时间", measure_unit=measure,
                confirmed_quantity=quantity, actual_question_count=1, approved_conversion=None,
                requested_conversion=None, grouping_reason="同篇归并", status="CONFIRMED",
                review_evidence="人工确认记录", review_actor="演示负责人", review_at="2026-09-30T11:00:00+08:00",
                timeliness_status="PENDING", rule_version="section-15")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "report.db")
        self.store.execute("INSERT INTO bindings VALUES(?,?,?,?,?)", ("b1", "g1", "s1", "演示学生", 1))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_night_quantity_is_one_article_and_completion_day_is_separate(self):
        ledger = FakeLedger([
            unit("regular", "2026-09-29T22:50:00+08:00", "2026-09-29T23:20:00+08:00", "REGULAR"),
            unit("night", "2026-09-29T23:10:00+08:00", "2026-09-30T10:00:00+08:00", "NIGHT", "语法填空"),
        ])
        reports = PerformanceReports(self.store, ledger, attribution="completion_date")
        day1 = reports.build("2026-09-29")
        day2 = reports.build("2026-09-30")
        self.assertEqual(day1["summary"]["night_articles"], 0)
        self.assertEqual(day1["summary"]["regular_articles:阅读理解"], 1)
        self.assertEqual(day2["summary"]["night_articles"], 1)
        self.assertEqual(day2["details"][0]["question_time"], "2026-09-29T23:10:00+08:00")
        self.assertEqual(day2["details"][0]["timeliness_status"], "PENDING")
        self.assertNotIn("actual_questions:语法填空", day2["summary"])

    def test_three_columns_keep_daytime_units_separate_and_night_takes_priority(self):
        reading = unit("reading", "2026-09-29T08:00:00+08:00", "2026-09-29T12:00:00+08:00", "REGULAR")
        grammar = unit("grammar", "2026-09-29T10:00:00+08:00", "2026-09-29T12:00:00+08:00",
                       "REGULAR", "语法填空", "题", 5)
        grammar["actual_question_count"] = 5
        grammar["approved_conversion"] = 3
        listening = unit("listening", "2026-09-29T11:00:00+08:00", "2026-09-29T12:00:00+08:00",
                         "REGULAR", "听力", "题", 2)
        listening["actual_question_count"] = 2
        night = unit("night-listening", "2026-09-30T06:59:59+08:00", "2026-09-30T10:00:00+08:00",
                     "NIGHT", "听力")
        report = PerformanceReports(self.store, FakeLedger([reading, grammar, listening, night])).build("2026-09-29")
        self.assertEqual(report["summary"]["day_composite_articles"], 1)
        self.assertEqual(report["summary"]["grammar_listening_actual_questions"], 7)
        self.assertEqual(report["summary"]["grammar_listening_converted_questions"], 3)
        self.assertEqual(report["summary"]["night_articles"], 1)
        self.assertEqual(report["monthly_cumulative"]["grammar_listening_actual_questions"], 7)
        self.assertEqual(report["monthly_cumulative"]["night_articles"], 1)
        self.assertEqual(report["summary"]["regular_articles:阅读理解"], 1)

    def test_unknown_type_and_mismatched_time_remain_pending(self):
        unknown = unit("unknown", "2026-09-29T12:00:00+08:00", "2026-09-29T13:00:00+08:00",
                       "REGULAR", "未知题型")
        wrong_period = unit("wrong-period", "2026-09-29T23:00:00+08:00", "2026-09-30T01:00:00+08:00",
                            "REGULAR")
        report = PerformanceReports(self.store, FakeLedger([unknown, wrong_period])).build("2026-09-29")
        self.assertEqual(report["summary"]["day_composite_articles"], 0)
        self.assertEqual(report["summary"]["night_articles"], 0)
        self.assertTrue(any("题型归类待核验" in item["reason"] for item in report["pending"]))
        self.assertTrue(any("时段归类不符" in item["reason"] for item in report["pending"]))

    def test_repeat_is_idempotent_and_submitted_change_previews_diff(self):
        one = unit("night", "2026-09-29T23:10:00+08:00", "2026-09-30T00:20:00+08:00", "NIGHT")
        ledger = FakeLedger([one])
        reports = PerformanceReports(self.store, ledger, attribution="completion_date")
        first = reports.generate("2026-09-30")
        self.assertTrue(first["changed"])
        self.assertFalse(reports.generate("2026-09-30")["changed"])
        reports.mark_submitted("2026-09-30", 1, actor="负责人", evidence="提交记录")
        one["status"] = "PENDING"
        one["confirmed_quantity"] = 0
        preview = reports.generate("2026-09-30")
        self.assertTrue(preview["preview_only"])
        self.assertEqual(len(reports.list_versions("2026-09-30")), 1)
        self.assertIn("night_articles", preview["diff"]["summary"])
        revision = reports.generate("2026-09-30", retroactive=True)
        self.assertEqual(revision["version"], 2)
        self.assertEqual(len(reports.list_versions("2026-09-30")), 2)
        self.assertIsNotNone(reports.list_versions("2026-09-30")[0]["submitted_at"])

    def test_schedule_catches_missing_previous_days(self):
        reports = PerformanceReports(self.store, FakeLedger([]))
        missing = scheduled_due_dates(reports, now=datetime.fromisoformat("2026-10-01T08:30:00+08:00"),
                                      generated_at_local=time(8), start_date="2026-09-29")
        self.assertEqual(missing, ["2026-09-29", "2026-09-30"])

    def test_partial_coverage_has_verified_subtotals_and_unknown_formal_totals(self):
        verified = unit("verified", "2026-09-30T08:00:00+08:00", "2026-09-30T12:00:00+08:00", "REGULAR")
        missing = unit("missing", None, "2026-09-30T12:00:00+08:00", "NIGHT")
        report = PerformanceReports(self.store, FakeLedger([verified, missing])).build("2026-09-30")
        subtotal = report["verified_subtotals"]
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(subtotal["totals"], {"day_composite_articles": 1,
                                              "grammar_listening_actual_questions": 0, "night_articles": 0})
        self.assertEqual(subtotal["included_unit_ids"], ["verified"])
        self.assertEqual(subtotal["included_unit_count"], 1)
        self.assertTrue(all(value is None for value in report["formal_totals"].values()))
        self.assertEqual(report["missing_data"]["pending_unit_count"], 1)
        self.assertEqual(PerformanceReports(self.store, FakeLedger([])).build("2026-09-30")["formal_totals"]["night_articles"], None)

    def test_unknown_actual_question_count_or_delivery_timezone_never_counts(self):
        grammar = unit("grammar-unknown", "2026-09-30T08:00:00+08:00", "2026-09-30T12:00:00+08:00",
                       "REGULAR", "语法填空", "题", 5)
        grammar["actual_question_count"] = None
        naive = unit("naive-delivery", "2026-09-30T08:00:00+08:00", "2026-09-30T12:00:00", "REGULAR")
        report = PerformanceReports(self.store, FakeLedger([grammar, naive])).build("2026-09-30")
        self.assertEqual(report["verified_subtotals"]["included_unit_count"], 0)
        self.assertEqual(report["verified_subtotals"]["totals"]["grammar_listening_actual_questions"], 0)
        self.assertTrue(any("实际题数待核验" in item["reason"] for item in report["pending"]))
        self.assertTrue(any("须含时区" in item["reason"] for item in report["pending"]))

    def _schedule_config(self, extra=""):
        config = Path(self.temp.name) / "schedule.toml"
        output = Path(self.temp.name) / "out"
        config.write_text(f'database = "{self.store.path.replace(chr(92), "/")}"\n'
                          f'output_dir = "{str(output).replace(chr(92), "/")}"\n'
                          'teacher = "测试老师"\ncatch_up_start = "2026-09-30"\n'
                          'generate_at = "08:00"\nexport_xlsx = false\n' + extra, encoding="utf-8")
        return config, output

    def test_scheduler_recalculates_existing_files_when_verified_ledger_changes(self):
        config, output = self._schedule_config()
        now = datetime.fromisoformat("2026-10-01T08:30:00+08:00")
        first = unit("first", "2026-09-30T08:00:00+08:00", "2026-09-30T12:00:00+08:00", "REGULAR")
        ledger = FakeLedger([first])
        with patch("tools.performance_report_schedule.PerformanceLedger", return_value=ledger):
            self.assertEqual(run_schedule(str(config), now=now)[0]["version"], 1)
            self.assertEqual(run_schedule(str(config), now=now), [])
            ledger.units.append(unit("second", "2026-09-30T23:00:00+08:00", "2026-10-01T06:00:00+08:00", "NIGHT"))
            self.assertEqual(run_schedule(str(config), now=now)[0]["version"], 2)
            self.assertEqual(run_schedule(str(config), now=now), [])
        self.assertTrue((output / "每日答题统计-2026-09-30-v1.json").exists())
        self.assertTrue((output / "每日答题统计-2026-09-30-v2.json").exists())

    def test_scheduler_submitted_change_stays_preview_and_repeated_preview_is_idempotent(self):
        config, output = self._schedule_config()
        now = datetime.fromisoformat("2026-10-01T08:30:00+08:00")
        ledger = FakeLedger([])
        with patch("tools.performance_report_schedule.PerformanceLedger", return_value=ledger):
            run_schedule(str(config), now=now)
            report = PerformanceReports(self.store, ledger, teacher="测试老师")
            report.mark_submitted("2026-09-30", 1, actor="老师", evidence="原日报已提交")
            ledger.units.append(unit("late", "2026-09-30T08:00:00+08:00", "2026-09-30T12:00:00+08:00", "REGULAR"))
            self.assertTrue(run_schedule(str(config), now=now)[0]["preview_only"])
            self.assertEqual(run_schedule(str(config), now=now), [])
            self.assertEqual(len(report.list_versions("2026-09-30")), 1)
        self.assertTrue((output / "每日答题统计-2026-09-30-v2-重算预览.json").exists())

    def test_required_native_export_failure_prevents_report_snapshot(self):
        root = Path(self.temp.name) / "archives"
        root.mkdir()
        config, _ = self._schedule_config('require_native_export = true\n'
                                         f'native_archive_root = "{root.as_posix()}"\n'
                                         f'native_export_dir = "{(Path(self.temp.name) / "sources").as_posix()}"\n')
        def fail_native_export(*_args, **_kwargs):
            raise ValueError("original evidence invalid")
        with self.assertRaisesRegex(ValueError, "original evidence invalid"):
            run_schedule(str(config), now=datetime.fromisoformat("2026-10-01T08:30:00+08:00"),
                         native_exporter=fail_native_export)
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM performance_report_versions")[0], 0)

    def test_required_native_export_is_reused_without_performance_inference(self):
        root = Path(self.temp.name) / "archives"
        root.mkdir()
        sources = Path(self.temp.name) / "sources"
        config, output = self._schedule_config('require_native_export = true\n'
                                              f'native_archive_root = "{root.as_posix()}"\n'
                                              f'native_export_dir = "{sources.as_posix()}"\n')
        now = datetime.fromisoformat("2026-10-01T08:30:00+08:00")
        self.assertEqual(run_schedule(str(config), now=now)[0]["version"], 1)
        self.assertEqual(run_schedule(str(config), now=now), [])
        saved = json.loads((output / "每日答题统计-2026-09-30-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["native_export"]["coverage"], "partial")
        self.assertFalse(saved["native_export"]["formal_statistics_eligible"])
        self.assertEqual(saved["payload"]["verified_subtotals"]["included_unit_count"], 0)
        self.assertEqual(len(list(sources.iterdir())), 1)

    def test_changed_native_sources_keep_prior_package_without_counting_acquisitions(self):
        from helpdesk.chat_text_archive import archive_clipboard
        from helpdesk.native_intake import index_native_records
        root, sources = Path(self.temp.name) / "archives", Path(self.temp.name) / "sources"
        root.mkdir()
        config, output = self._schedule_config('require_native_export = true\n'
                                              f'native_archive_root = "{root.as_posix()}"\n'
                                              f'native_export_dir = "{sources.as_posix()}"\n')
        def acquire(acquisition):
            raw_result = Path(self.temp.name) / (acquisition + ".json")
            raw_result.write_text(json.dumps({"attempt_id": acquisition, "tool": "Clipboard", "is_error": False,
                "content": [{"type": "text", "text": "Clipboard content:\n为什么不选B？"}]}), encoding="utf-8")
            attempt = Path(self.temp.name) / ("attempt-" + acquisition + ".json")
            attempt.write_text(json.dumps({"attempt_id": acquisition, "tool": "Clipboard", "arguments": {"mode": "get"},
                "status": "TOOL_RETURNED", "started_at": "2026-09-30T03:00:00+00:00", "result_path": str(raw_result)}), encoding="utf-8")
            archive_clipboard(raw_result, root, observed_group="English答疑群")
            index_native_records(self.store, root)
        acquire("a" * 32)
        now = datetime.fromisoformat("2026-10-01T08:30:00+08:00")
        run_schedule(str(config), now=now)
        first_package = next(sources.iterdir())
        prior = (first_package / "原文与采集凭据.zip").read_bytes()
        acquire("b" * 32)
        self.assertEqual(run_schedule(str(config), now=now)[0]["version"], 2)
        self.assertEqual(run_schedule(str(config), now=now), [])
        self.assertEqual(len(list(sources.iterdir())), 2)
        self.assertEqual((first_package / "原文与采集凭据.zip").read_bytes(), prior)
        saved = json.loads((output / "每日答题统计-2026-09-30-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["native_export"]["collection_count"], 2)
        self.assertEqual(saved["payload"]["missing_data"]["native_acquisition_count"], 2)
        self.assertEqual(saved["payload"]["verified_subtotals"]["included_unit_count"], 0)

    def test_custom_cutoff_uses_business_day_window(self):
        ledger = FakeLedger([unit("after-cutoff", "2026-09-29T22:50:00+08:00",
                                  "2026-09-29T23:20:00+08:00", "REGULAR")])
        reports = PerformanceReports(self.store, ledger, attribution="completion_date", cutoff_time=time(20, 0))
        self.assertEqual(reports.build("2026-09-29")["summary"]["completed_units"], 0)
        self.assertEqual(reports.build("2026-09-30")["summary"]["regular_articles:阅读理解"], 1)
        due = scheduled_due_dates(reports, now=datetime.fromisoformat("2026-09-30T21:30:00+08:00"),
                                  generated_at_local=time(21), start_date="2026-09-30")
        self.assertEqual(due, ["2026-09-30"])

    def test_scheduler_retries_missing_xlsx_after_snapshot_saved(self):
        config = Path(self.temp.name) / "schedule.toml"
        output = Path(self.temp.name) / "out"
        config.write_text(f'database = "{self.store.path.replace(chr(92), "/")}"\n'
                          f'output_dir = "{str(output).replace(chr(92), "/")}"\n'
                          'teacher = "测试老师"\ncatch_up_start = "2026-09-30"\n'
                          'generate_at = "08:00"\nexport_xlsx = true\n', encoding="utf-8")
        now = datetime.fromisoformat("2026-10-01T08:30:00+08:00")
        def fail_export(_payload, _path):
            raise RuntimeError("export failed")
        with self.assertRaisesRegex(RuntimeError, "export failed"):
            run_schedule(str(config), now=now, exporter=fail_export)
        self.assertTrue((output / "每日答题统计-2026-09-30-v1.json").exists())
        def succeed_export(_payload, path):
            path.write_bytes(b"demo-file")
        result = run_schedule(str(config), now=now, exporter=succeed_export)
        self.assertEqual(result, [{"date": "2026-09-30", "version": 1, "preview_only": False}])
        self.assertTrue((output / "每日答题统计-2026-09-30-v1.xlsx").exists())
        self.assertEqual(run_schedule(str(config), now=now, exporter=succeed_export), [])

    def test_exited_groups_remain_in_scope_without_claiming_full_coverage(self):
        gid = record_observed_group(self.store, "曾加入的演示群", membership="EXITED", evidence={"demo": True})
        record_coverage(self.store, gid, "2026-09-17", "2026-09-30", status="PARTIAL",
                        actor="tester", evidence="仅观察部分记录")
        report = PerformanceReports(self.store, FakeLedger([])).build("2026-09-30")
        self.assertEqual(report["scope"], "ALL_HISTORICAL_GROUPS")
        self.assertEqual(report["scope_start_date"], "2026-09-17")
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["coverage"]["groups"][0]["membership"], "EXITED")
        self.assertTrue(any(item["group"] == "曾加入的演示群" for item in report["pending"]))

    def test_actual_ledger_and_report_reconcile_one_night_piece(self):
        app = Helpdesk(self.store)
        person = app.bind("real-group", "real-student", "测试学生", verified=True)
        ledger = PerformanceLedger(self.store, night_end_hour=7, night_end_inclusive=False)
        incoming = app.ingest(Incoming(person, "语法填空第1空", Intent.NEW,
                                      verified_question=demo_question(), verified_material="Grammar passage",
                                      observed_at="2026-09-29T23:05:00+08:00"))
        ledger.record_source_time(incoming.message_id, "2026-09-29T23:10:00+08:00", source="wecom_original",
                                  message_locator="wecom:sample", evidence={"raw_message_id": incoming.message_id})
        material = self.store.one("SELECT material_id FROM questions WHERE id=?", (incoming.question_id,))[0]
        uid = ledger.create_unit(incoming.message_id, "语法填空", scope_key="passage-1", grouping_reason="同一篇材料",
                                 question_id=incoming.question_id, material_id=material)
        q = self.store.one("SELECT current_version,context_revision FROM questions WHERE id=?", (incoming.question_id,))
        turn = self.store.one("SELECT id,message_id FROM turns WHERE question_id=?", (incoming.question_id,))
        oid = new_id()
        done = "2026-09-30T10:00:00+08:00"
        self.store.execute("""INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
            context_revision,idempotency_key,state,created_at,simulated,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, turn["message_id"], incoming.case_id, turn["id"], person, "ANSWER", "详细解答",
             q["current_version"], q["context_revision"], oid, "SENT_UI_CONFIRMED", done, 0, done))
        self.store.execute("INSERT INTO delivery_checks VALUES(?,?,?,?,?)", (new_id(), oid, "SENT_UI_CONFIRMED", "{}", done))
        ledger.record_delivery(uid, oid)
        ledger.confirm(uid, reviewer="teacher", evidence="内容与形式已核对", actual_question_count=5)
        reports = PerformanceReports(self.store, ledger, attribution="completion_date")
        prior = reports.build("2026-09-29")
        today = reports.generate("2026-09-30")["payload"]
        self.assertEqual(prior["summary"]["night_articles"], 0)
        self.assertEqual(prior["summary"]["unfinished_units"], 1)
        self.assertEqual(today["summary"]["night_articles"], 1)
        self.assertEqual(today["details"][0]["actual_question_count"], 5)
        self.assertEqual(today["details"][0]["measure_unit"], "篇")
        self.assertEqual(today["details"][0]["first_message_id"], incoming.message_id)

    def test_question_day_07_cross_midnight_and_exact_boundary(self):
        ledger = FakeLedger([
            unit("late", "2026-09-29T23:10:00+08:00", "2026-09-30T10:00:00+08:00", "NIGHT"),
            unit("early", "2026-09-30T06:59:59+08:00", "2026-09-30T10:00:00+08:00", "NIGHT"),
            unit("boundary", "2026-09-30T07:00:00+08:00", "2026-09-30T10:00:00+08:00", "REGULAR"),
        ])
        reports = PerformanceReports(self.store, ledger)
        prior = reports.build("2026-09-29")
        next_day = reports.build("2026-09-30")
        self.assertEqual(prior["summary"]["night_articles"], 2)
        self.assertEqual(prior["summary"]["new_units"], 2)
        self.assertEqual(prior["summary"]["completed_units"], 0)
        self.assertEqual(next_day["summary"]["night_articles"], 0)
        self.assertEqual(next_day["summary"]["regular_articles:阅读理解"], 1)
        self.assertEqual(next_day["summary"]["completed_units"], 3)
        self.assertEqual(prior["question_day_boundary"], "07:00:00")
        self.assertEqual(prior["reporting_cutoff"], "07:00:00")
        self.assertEqual(prior["activity_day_cutoff"], "23:59:59")
        self.assertEqual(scheduled_due_dates(reports, now=datetime.fromisoformat("2026-10-01T06:59:59+08:00"),
                                             generated_at_local=time(6), start_date="2026-09-30"), [])
        self.assertEqual(scheduled_due_dates(reports, now=datetime.fromisoformat("2026-10-01T07:00:00+08:00"),
                                             generated_at_local=time(6), start_date="2026-09-30"), ["2026-09-30"])

    def test_unfinished_at_question_business_day_boundary(self):
        ledger = FakeLedger([
            unit("before-seven", "2026-09-29T23:10:00+08:00", "2026-09-30T06:59:59+08:00", "NIGHT"),
            unit("at-seven", "2026-09-29T23:20:00+08:00", "2026-09-30T07:00:00+08:00", "NIGHT"),
        ])
        report = PerformanceReports(self.store, ledger).build("2026-09-29")
        self.assertEqual(report["summary"]["night_articles"], 2)
        self.assertEqual(report["summary"]["unfinished_units"], 1)
        self.assertEqual([p["counting_unit_id"] for p in report["pending"] if p["counting_unit_id"]], ["at-seven"])

    def test_persisted_date_policy_conflict_keeps_quantity_pending(self):
        self.store.execute("UPDATE performance_rules SET value='previous-confirmed-policy',status='CONFIRMED' WHERE key='night_date_attribution'")
        ledger = FakeLedger([unit("late", "2026-09-29T23:10:00+08:00",
                                  "2026-09-30T06:59:00+08:00", "NIGHT")])
        report = PerformanceReports(self.store, ledger).build("2026-09-29")
        self.assertEqual(report["reporting_cutoff"], "07:00:00")
        self.assertFalse(report["persisted_policy"]["aligned_with_question_day_07"])
        self.assertEqual(report["summary"]["night_articles"], 0)
        self.assertTrue(any("持久化" in p["reason"] for p in report["pending"]))

    def test_early_observed_unlinked_message_stays_pending_previous_business_day(self):
        ledger = FakeLedger([], [{"id": "observed-only", "observed_at": "2026-09-30T06:30:00+08:00",
                                  "source_sent_at": None, "group_key": "g1", "display_name": "演示学生"}])
        report = PerformanceReports(self.store, ledger).build("2026-09-29")
        self.assertTrue(any(p["first_message_id"] == "observed-only" for p in report["pending"]))
        self.assertEqual(report["summary"]["night_articles"], 0)
        self.assertEqual(report["summary"]["new_units"], 0)

    def test_early_observed_followup_without_source_time_stays_pending(self):
        app = Helpdesk(self.store)
        person = app.bind("g-follow", "s-follow", "追问学生", verified=True)
        ledger = PerformanceLedger(self.store)
        first = app.ingest(Incoming(person, "阅读第12题", Intent.NEW,
                                    verified_question=demo_question(), verified_material="Reading passage",
                                    observed_at="2026-09-30T06:20:00+08:00"))
        ledger.record_source_time(first.message_id, "2026-09-29T23:10:00+08:00", source="wecom_original",
                                  message_locator="wecom:first", evidence={"raw_message_id": first.message_id})
        material = self.store.one("SELECT material_id FROM questions WHERE id=?", (first.question_id,))[0]
        uid = ledger.create_unit(first.message_id, "阅读理解", scope_key="same-reading",
                                 grouping_reason="同篇", question_id=first.question_id, material_id=material)
        follow = app.ingest(Incoming(person, "为什么不选B", Intent.FOLLOWUP,
                                     quote_message_id=first.message_id, observed_at="2026-09-30T06:30:00+08:00"))
        ledger.link_activity(uid, follow.message_id, question_id=first.question_id,
                             kind="FOLLOWUP", reason="普通追问")
        report = PerformanceReports(self.store, ledger).build("2026-09-29")
        self.assertTrue(any(p["first_message_id"] == follow.message_id and "追问原始发送时间" in p["reason"]
                            for p in report["pending"]))
        self.assertEqual(report["summary"]["followup_turns"], 0)

    def test_rule_change_uses_submitted_revision_chain(self):
        ledger = FakeLedger([unit("late", "2026-09-29T23:10:00+08:00",
                                  "2026-09-30T10:00:00+08:00", "NIGHT")])
        old = PerformanceReports(self.store, ledger, attribution="completion_date")
        saved = old.generate("2026-09-29")
        old.mark_submitted("2026-09-29", saved["version"], actor="负责人", evidence="旧表提交记录")
        revised = PerformanceReports(self.store, ledger).generate("2026-09-29")
        self.assertTrue(revised["preview_only"])
        self.assertIn("attribution", revised["diff"])
        self.assertEqual(revised["payload"]["summary"]["night_articles"], 1)
        self.assertEqual(len(old.list_versions("2026-09-29")), 1)


if __name__ == "__main__":
    unittest.main()
