"""One input review keeps answer validation and the manual delivery boundary."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.test_answer_queue import validate_source_answer
from helpdesk.workflow import Workflow
from tests.test_live_generation import StubAdapter
from tests.test_manual_send_policy import CountingDesktop


class SingleInputReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / "business.db")
        self.app = Helpdesk(self.db)
        self.desktop = CountingDesktop(self.base / "desktop.db")
        self.flow = Workflow(self.db, desktop=self.desktop)
        self.binding = self.app.bind("group", "student", "学生", verified=True)
        self.first = self.app.ingest(Incoming(self.binding, "请讲第12题", Intent.NEW,
            verified_question=demo_question(), raw_material="Passage", verified_material="Passage"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def row(self, oid):
        return self.db.one("SELECT * FROM outbox WHERE id=?", (oid,))

    def real_flow(self):
        skill = self.base / "course.md"
        skill.write_text("# approved course snapshot\n", encoding="utf-8")
        manifest = self.base / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        verified = {"answer_generation_allowed_by_course": True,
            "workflow_teaching_paths": [str(skill)],
            "files": [{"snapshot_path": str(skill), "snapshot_sha256": sha256(skill.read_bytes()).hexdigest()}],
            "reviewed_policy_id": "reviewed", "question_type": "阅读理解"}
        return Workflow(self.db, desktop=self.desktop, generation_adapter=StubAdapter(),
                        teaching_manifest=manifest), verified

    def real_answer(self, turn=None):
        flow, verified = self.real_flow()
        with patch("helpdesk.workflow.verify_bundle", return_value=verified):
            rid = flow.start(turn or self.first.turn_id)
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (rid,))[0])
        result = StubAdapter().generate(snapshot)
        return flow, flow.finish(rid, result), result["text"]

    def test_false_real_answer_preserves_body_and_manual_send_never_calls_desktop(self):
        self.flow.set_answer_review_required(False, actor="owner")
        self.flow.set_delivery_policy({"ANSWER": "MANUAL", "CORRECTION": "MANUAL"}, "single-input-review")
        flow, done, original = self.real_answer()
        row = self.row(done["outbox_id"])
        self.assertEqual((row["body"], row["review_status"]), (original, "NOT_REQUIRED"))
        self.assertEqual(self.db.one("SELECT status FROM questions WHERE id=?", (self.first.question_id,))[0], "AWAITING_MANUAL_SEND")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM reviews")[0], 0)
        flow._validate(row, transport=False)
        self.assertEqual(flow.dispatch(row["id"]), "MANUAL_SEND_REQUIRED")
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(len(self.desktop.receipts()), 0)
        with self.assertRaisesRegex(ValueError, "MANUAL_REVIEW_REQUIRED"):
            validate_source_answer(self.db, row)  # Test copies still require review.
        with self.assertRaisesRegex(ValueError, "REAL_SEND_DISABLED"):
            flow._validate(row)

    def test_default_and_reenabled_true_keep_human_gate(self):
        self.assertTrue(self.flow.dashboard()["health"]["answer_review_required"])
        done = self.flow.generate(self.first.turn_id)
        row = self.row(done["outbox_id"])
        with self.assertRaisesRegex(ValueError, "MANUAL_REVIEW_REQUIRED"):
            self.flow._validate(row, transport=False)
        self.assertEqual(self.flow.dispatch(row["id"]), "AWAITING_REVIEW")
        self.flow.set_answer_review_required(False)
        self.flow._validate(row, transport=False)
        self.flow.set_answer_review_required(True)
        with self.assertRaisesRegex(ValueError, "MANUAL_REVIEW_REQUIRED"):
            self.flow._validate(row, transport=False)
        self.flow.approve(row["id"])
        self.flow._validate(self.row(row["id"]), transport=False)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM reviews")[0], 1)

    def test_simulated_manual_status_does_not_forge_review(self):
        self.flow.set_answer_review_required(False)
        self.flow.set_manual_send(True)
        done = self.flow.generate(self.first.turn_id)
        self.assertEqual(self.row(done["outbox_id"])["review_status"], "NOT_REQUIRED")
        self.db.execute("UPDATE outbox SET state='CANCELLED' WHERE purpose='ACK'")
        self.assertEqual(self.flow.dispatch(), "MANUAL_SEND_REQUIRED")
        self.assertEqual(self.desktop.calls, [])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM reviews")[0], 0)

    def test_false_keeps_body_binding_evidence_and_version_checks(self):
        self.flow.set_answer_review_required(False)
        done = self.flow.generate(self.first.turn_id)
        oid = done["outbox_id"]
        original = self.row(oid)["body"]
        self.db.execute("UPDATE outbox SET body=body || ' tampered' WHERE id=?", (oid,))
        with self.assertRaisesRegex(ValueError, "ANSWER_INVALID"):
            self.flow._validate(self.row(oid), transport=False)
        self.db.execute("UPDATE outbox SET body=? WHERE id=?", (original, oid))
        self.db.execute("UPDATE answer_evidence SET complete=0 WHERE answer_id=?", (done["answer_id"],))
        with self.assertRaisesRegex(ValueError, "ANSWER_EVIDENCE_MISSING"):
            self.flow._validate(self.row(oid), transport=False)
        self.db.execute("UPDATE answer_evidence SET complete=1 WHERE answer_id=?", (done["answer_id"],))
        self.db.execute("UPDATE bindings SET verified=0 WHERE id=?", (self.binding,))
        with self.assertRaisesRegex(ValueError, "RECIPIENT_BINDING_MISMATCH"):
            self.flow._validate(self.row(oid), transport=False)
        self.db.execute("UPDATE bindings SET verified=1 WHERE id=?", (self.binding,))
        self.app.ingest(Incoming(self.binding, "题目发错了", Intent.CORRECTION,
            quote_message_id=self.first.message_id,
            verified_question=demo_question(stem="Why did he NOT return home?")))
        with self.assertRaisesRegex(ValueError, "STALE_VERSION|Stale turn"):
            self.flow._validate(self.row(oid), transport=False)
        self.assertEqual(self.row(oid)["state"], "STALE")

    def test_manual_question_status_only_for_manual_delivery(self):
        self.flow.set_answer_review_required(False)
        for mode in ("AUTO", "DISABLED"):
            with self.subTest(mode=mode):
                self.flow.set_delivery_policy({"ANSWER": mode}, mode)
                incoming = self.app.ingest(Incoming(self.binding, "请讲题", Intent.NEW,
                    platform_id="question-" + mode, verified_question=demo_question(),
                    raw_material="Passage", verified_material="Passage"))
                done = self.flow.generate(incoming.turn_id)
                self.assertEqual(self.row(done["outbox_id"])["review_status"], "NOT_REQUIRED")
                status = self.db.one("SELECT status FROM questions WHERE id=?", (incoming.question_id,))[0]
                self.assertNotEqual(status, "AWAITING_MANUAL_SEND")

    def test_false_never_promotes_incomplete_output(self):
        self.flow.set_answer_review_required(False)
        rid = self.flow.start(self.first.turn_id)
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (rid,))[0])
        result = self.flow.generation_adapter.generate(snapshot)
        result["complete"] = False
        done = self.flow.finish(rid, result)
        self.assertEqual(done["state"], "REJECTED")
        self.assertIsNone(done["outbox_id"])
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM reviews")[0], 0)

    def test_policy_is_strict_persistent_audited_and_does_not_rewrite_existing_answer(self):
        done = self.flow.generate(self.first.turn_id)
        before = dict(self.row(done["outbox_id"]))
        for bad in ("false", 0, 1, None, [], {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.flow.set_answer_review_required(bad)
        with self.assertRaises(ValueError):
            self.flow.set_answer_review_required(False, actor=" ")
        self.flow.set_answer_review_required(False, actor=" owner ")
        self.assertEqual(dict(self.row(done["outbox_id"])), before)
        details = json.loads(self.db.one("SELECT details FROM audit WHERE event='ANSWER_REVIEW_POLICY_CHANGED'")[0])
        self.assertEqual(details, {"actor": "owner", "answer_review_required": False, "old_value": None})
        self.flow.set_answer_review_required(False, actor="owner")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_REVIEW_POLICY_CHANGED'")[0], 1)
        self.db.close()
        self.db = Store(self.base / "business.db")
        self.flow = Workflow(self.db, desktop=self.desktop)
        self.assertFalse(self.flow.dashboard()["health"]["answer_review_required"])

    def test_real_correction_keeps_generator_text(self):
        self.flow.set_answer_review_required(False)
        flow, first, _ = self.real_answer()
        # Historical delivery fixture only; no desktop interaction occurs.
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED' WHERE id=?", (first["outbox_id"],))
        follow = self.app.ingest(Incoming(self.binding, "请重新核验", Intent.DISPUTE,
                                        quote_message_id=self.first.message_id))
        _, done, original = self.real_answer(follow.turn_id)
        row = self.row(done["outbox_id"])
        self.assertEqual((row["purpose"], row["body"], row["review_status"]), ("CORRECTION", original, "NOT_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
