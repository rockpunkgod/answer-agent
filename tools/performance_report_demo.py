"""Build a clearly labeled local sample from invented records only."""
from pathlib import Path
from datetime import datetime, timezone
import json
import tempfile

from helpdesk.storage import Store
from helpdesk.performance_reports import PerformanceReports
from tools.performance_report import export_xlsx


class DemoLedger:
    def __init__(self, units):
        self.units = units

    def list_units(self):
        return self.units

    def list_unlinked_messages(self):
        return []

    def reconcile_stale_deliveries(self):
        return []


def main():
    output = Path("outputs/performance-demo")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        db = Store(Path(temp) / "demo.db")
        try:
            db.execute("INSERT INTO bindings VALUES(?,?,?,?,?)", ("demo-binding", "演示群", "demo-student", "演示学生", 1))
            common = dict(binding_id="demo-binding", group_key="演示群", display_name="演示学生",
                          question_time_source='{"source":"wecom_original","demo":true}',
                          first_response_at="2026-09-29T23:11:00+08:00", review_actor="演示核验人",
                          review_at="2026-09-30T11:00:00+08:00", review_evidence="模拟交付与内容核验记录",
                          actual_question_count=1, approved_conversion=None, requested_conversion=None,
                          timeliness_status="PENDING", rule_version="user-section-15-2026-09-30")
            units = [
                dict(common, id="DEMO-READING", material_id="DEMO-M1", topic_key="阅读材料 A",
                     question_type="阅读理解", first_message_id="DEMO-MSG-1", question_time="2026-09-29T22:50:00+08:00",
                     completed_at="2026-09-29T23:20:00+08:00", completion_outbox_id="DEMO-DELIVERY-1",
                     category="REGULAR", category_basis="学生原始提问于23:00前", measure_unit="篇",
                     confirmed_quantity=1, grouping_reason="同一阅读材料归并", status="CONFIRMED"),
                dict(common, id="DEMO-GRAMMAR", material_id="DEMO-M2", topic_key="语法填空材料 B",
                     question_type="语法填空", first_message_id="DEMO-MSG-2", question_time="2026-09-29T23:10:00+08:00",
                     completed_at="2026-09-30T10:00:00+08:00", completion_outbox_id="DEMO-DELIVERY-2",
                     category="NIGHT", category_basis="学生原始提问于23:10；完成时间不改变归类", measure_unit="篇",
                     confirmed_quantity=1, grouping_reason="同篇多个空归并", status="CONFIRMED"),
                dict(common, id="DEMO-PENDING", material_id="DEMO-M3", topic_key="待查材料 C",
                     question_type="听力", first_message_id="DEMO-MSG-3", question_time=None,
                     question_time_source=None, completed_at=None, completion_outbox_id=None,
                     review_evidence=None, review_actor=None, review_at=None,
                     category="PENDING", category_basis="原消息时间待核验", measure_unit="待核验",
                     confirmed_quantity=None, grouping_reason="待核验", status="PENDING"),
            ]
            report = PerformanceReports(db, DemoLedger(units), teacher="演示老师").build("2026-09-29")
            report["template"] = "演示数据｜自拟日报草稿｜非机构原始填报模板"
            report["source"] = "人工构造的演示数据，不对应真实学生或薪酬"
            report["simulation"] = True
            report["generated_at"] = datetime.now(timezone.utc).isoformat()
            (output / "演示日报.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            export_xlsx(report, output / "演示日报.xlsx", previews=output / "previews")
        finally:
            db.close()


if __name__ == "__main__":
    main()
