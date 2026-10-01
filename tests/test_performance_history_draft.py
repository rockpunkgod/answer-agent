import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.performance_history_draft import build_payload


class HistoryDraftTests(unittest.TestCase):
    def test_read_only_candidate_draft_never_infers_time_delivery_or_articles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "history.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE history_groups(id TEXT,display_name TEXT,membership TEXT,identity_status TEXT,evidence TEXT,created_at TEXT);
                CREATE TABLE history_coverage(id TEXT,group_id TEXT,start_date TEXT,end_date TEXT,status TEXT,evidence TEXT,actor TEXT,updated_at TEXT);
                CREATE TABLE history_inventory_reviews(id TEXT,complete INTEGER,actor TEXT,evidence TEXT,created_at TEXT);
            """)
            conn.execute("INSERT INTO history_groups VALUES(?,?,?,?,?,?)",
                         ("g1", "已退出演示群", "EXITED", "OBSERVED", json.dumps({"screenshot": str(root / "group.png")}), "2026-09-30"))
            conn.commit()
            conn.close()
            history = root / "capture"
            first = history / "group-one"
            first.mkdir(parents=True)
            (first / "manifest.json").write_text(json.dumps({"observed_group": "采集来源一"}), encoding="utf-8")
            (first / "curated_candidates.json").write_text(json.dumps({"groups": [{
                "group_id": "candidate-1", "business_date_candidate": "2026-09-23",
                "student_display_candidate": "演示学生", "question_type_candidate": "阅读理解",
                "first_prompt_id": "ocr-message-1", "first_time_candidate": "23:10",
                "possible_answer_ids": ["ocr-answer-1"], "evidence_frames": ["0001.png"],
                "material_grouping": "可能同篇", "remaining_checks": ["核对原始发送时间"]}]}), encoding="utf-8")
            before = db.read_bytes()
            report = build_payload(db, history, end_date="2026-09-30")
            self.assertEqual(db.read_bytes(), before)
            self.assertFalse(report["coverage"]["complete"])
            self.assertEqual(report["summary"]["night_articles"], None)
            self.assertIsNone(report["summary"]["day_composite_articles"])
            self.assertIsNone(report["summary"]["grammar_listening_actual_questions"])
            self.assertIsNone(report["summary"]["grammar_listening_converted_questions"])
            self.assertEqual(len(report["details"]), 1)
            detail = report["details"][0]
            self.assertEqual(detail["question_time"], "")
            self.assertEqual(detail["completed_at"], "")
            self.assertIsNone(detail["confirmed_quantity"])
            self.assertEqual(detail["candidate_id"], "group-one:candidate-1")
            self.assertIn("0001.png", detail["source_evidence"])
            self.assertTrue(any(row["group"] == "已退出演示群" for row in report["pending"]))
            second = history / "group-two"
            second.mkdir()
            (second / "manifest.json").write_text(json.dumps({"observed_group": "采集来源二"}), encoding="utf-8")
            (second / "curated_candidates.json").write_text(json.dumps({"groups": [{"group_id": "candidate-2"}]}), encoding="utf-8")
            rerun = build_payload(db, history, end_date="2026-09-30")
            self.assertEqual(len(rerun["details"]), 2)
            self.assertEqual(len({row["candidate_id"] for row in rerun["details"]}), 2)
            third = history / "ocr-only"
            third.mkdir()
            (third / "manifest.json").write_text(json.dumps({"observed_group": "OCR来源"}), encoding="utf-8")
            (third / "extracted.json").write_text(json.dumps({"messages": [{"id": "m1", "evidence": [{"file": "0001.png"}]}],
                                                         "candidate_units": [{"id": "c1", "first_message_candidate_id": "m1",
                                                                              "original_send_time_candidate": "06:30"}]}), encoding="utf-8")
            ocr_report = build_payload(db, history, end_date="2026-09-30")
            self.assertEqual(len(ocr_report["details"]), 2)
            self.assertTrue(any(p["candidate_id"] == "ocr-only:c1" and "0001.png" in p["source_evidence"]
                                and not p["question_time"] for p in ocr_report["pending"]))


if __name__ == "__main__":
    unittest.main()
