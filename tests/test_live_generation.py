"""Generation boundary tests; no browser or desktop is contacted."""
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
from helpdesk.test_answer_queue import (queue_test_answer, render_test_body, validate_test_copy,
                                       LEGACY_BODY_FORMAT, PLAIN_BODY_FORMAT, TEST_PREFIX)
from helpdesk.delivery import BoundMessage
from helpdesk.test_routing import TestRecipient
from helpdesk.workflow import Workflow
from datetime import datetime, timezone


class StubAdapter:
    identity = "TEST_WEB_GENERATOR"
    simulated = False

    def __init__(self, *, fails=False):
        self.calls = 0
        self.fails = fails

    def generate(self, snapshot):
        self.calls += 1
        if self.fails:
            raise RuntimeError("Ambiguous browser completion")
        option = next(o for o in snapshot["student_question"]["options"]
                      if o["verified_text"] == "To look after his mother.")
        return dict(adapter=self.identity, simulated=False, run_id=snapshot["run_id"],
                    session_id=snapshot["session_id"], web_session_evidence="web-session:test-123",
                    uploaded_teaching_hashes={s["path"]: s["sha256"] for s in snapshot["teaching_skills"]},
                    uploads_confirmed=True, complete=True, correct_option_id=option["id"],
                    text=f"第12题选{option['label']}，根据文章目的判断。")


class LiveGenerationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Store(self.base / "workflow.db")
        self.app = Helpdesk(self.db)
        student = self.app.bind("test-group", "test-student", "学生", verified=True)
        self.turn = self.app.ingest(Incoming(student, "请讲第12题", Intent.NEW,
            verified_question=demo_question(), raw_material="Passage", verified_material="Passage")).turn_id
        self.skill = self.base / "course.md"
        self.skill.write_text("# approved course snapshot\n", encoding="utf-8")
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def flow(self, adapter):
        digest = sha256(self.skill.read_bytes()).hexdigest()
        verified = {"answer_generation_allowed_by_course": True,
                    "workflow_teaching_paths": [str(self.skill)],
                    "files": [{"snapshot_path": str(self.skill), "snapshot_sha256": digest}],
                    "reviewed_policy_id": "test-reviewed-policy", "question_type": "阅读理解"}
        with patch("helpdesk.workflow.verify_bundle", return_value=verified) as verifier:
            flow = Workflow(self.db, generation_adapter=adapter, teaching_manifest=self.manifest)
            # Start invokes the production verifier again; keep the stub within each test.
        return flow, verified

    def start(self, adapter):
        flow, verified = self.flow(adapter)
        with patch("helpdesk.workflow.verify_bundle", return_value=verified) as verifier:
            run_id = flow.start(self.turn)
            verifier.assert_called_once()
        return flow, run_id

    def approved_test_copy(self):
        flow, run_id = self.start(StubAdapter())
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
        source_id = flow.finish(run_id, StubAdapter().generate(snapshot))["outbox_id"]
        flow.approve(source_id)
        recipient = TestRecipient("wecom", "verified-test-session", "苇中鹤", "fixture verification", datetime.now(timezone.utc))
        return source_id, queue_test_answer(self.db, source_id, recipient), recipient

    def test_legacy_migration_keeps_frozen_body_hashes_and_states(self):
        source_id, copy_id, recipient = self.approved_test_copy()
        source_body = self.db.one("SELECT body FROM outbox WHERE id=?", (source_id,))[0]
        legacy_body = render_test_body(copy_id, source_body, LEGACY_BODY_FORMAT)
        for state in ("SENT_UI_CONFIRMED", "SEND_UNKNOWN"):
            with self.subTest(state=state):
                self.db.execute("UPDATE outbox SET body=?,state=? WHERE id=?", (legacy_body, state, copy_id))
                self.db.execute("INSERT INTO delivery_checks VALUES(?,?,?,?,?)", (
                    "receipt-" + state, copy_id, state,
                    json.dumps({"body_hash": sha256(legacy_body.encode("utf-8")).hexdigest(),
                                "target_key": recipient.stable_key}), "frozen-timestamp"))
                before = dict(self.db.one("SELECT * FROM outbox WHERE id=?", (copy_id,)))
                receipts_before = [dict(row) for row in self.db.all("SELECT * FROM delivery_checks WHERE outbox_id=?", (copy_id,))]
                # Recreate the pre-migration schema in a disposable database.
                self.db.execute("ALTER TABLE test_answer_copies DROP COLUMN body_format")
                self.db.execute("DROP TABLE deepseek_chats")
                self.db.execute("DROP TABLE session_owners")
                self.db.execute("DELETE FROM schema_migrations WHERE version>=5")
                self.db.close()
                self.db = Store(self.base / "workflow.db")
                after = self.db.one("SELECT * FROM outbox WHERE id=?", (copy_id,))
                self.assertEqual(dict(after), before)
                self.assertEqual([dict(row) for row in self.db.all("SELECT * FROM delivery_checks WHERE outbox_id=?", (copy_id,))], receipts_before)
                mapping = self.db.one("SELECT * FROM test_answer_copies WHERE test_outbox_id=?", (copy_id,))
                self.assertEqual(mapping["body_format"], LEGACY_BODY_FORMAT)
                self.assertEqual(mapping["source_body_hash"], sha256(source_body.encode("utf-8")).hexdigest())
                self.assertEqual(validate_test_copy(self.db, after).body_hash, sha256(legacy_body.encode("utf-8")).hexdigest())
                self.assertEqual(queue_test_answer(self.db, source_id, recipient), copy_id)
                self.assertEqual(dict(self.db.one("SELECT * FROM outbox WHERE id=?", (copy_id,))), before)

    def test_test_copy_body_format_requires_exact_body(self):
        source_id, copy_id, recipient = self.approved_test_copy()
        source_body = self.db.one("SELECT body FROM outbox WHERE id=?", (source_id,))[0]
        for body_format in (PLAIN_BODY_FORMAT, LEGACY_BODY_FORMAT):
            expected = render_test_body(copy_id, source_body, body_format)
            self.db.execute("UPDATE test_answer_copies SET body_format=? WHERE test_outbox_id=?", (body_format, copy_id))
            self.db.execute("UPDATE outbox SET body=? WHERE id=?", (expected, copy_id))
            validate_test_copy(self.db, self.db.one("SELECT * FROM outbox WHERE id=?", (copy_id,)))
            for tampered in (expected + " ", source_body if body_format == LEGACY_BODY_FORMAT else render_test_body(copy_id, source_body, LEGACY_BODY_FORMAT)):
                with self.subTest(body_format=body_format, tampered=tampered):
                    self.db.execute("UPDATE outbox SET body=? WHERE id=?", (tampered, copy_id))
                    with self.assertRaisesRegex(ValueError, "TEST_COPY_STALE"):
                        queue_test_answer(self.db, source_id, recipient)

    def test_real_requires_manifest_and_freezes_adapter_and_mode(self):
        with self.assertRaisesRegex(ValueError, "verified teaching manifest"):
            Workflow(self.db, generation_adapter=StubAdapter())
        flow, run_id = self.start(StubAdapter())
        run = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        snapshot = json.loads(run["input_json"])
        self.assertEqual((snapshot["generation_adapter"], snapshot["simulated"]), ("TEST_WEB_GENERATOR", False))
        self.assertEqual(self.db.one("SELECT adapter FROM sessions WHERE id=?", (run["session_id"],))[0], "TEST_WEB_GENERATOR")
        self.assertEqual(json.loads(self.db.one("SELECT details FROM audit WHERE run_id=? AND event='GENERATION_STARTED'", (run_id,))[0])["simulated"], False)
        result = StubAdapter().generate(snapshot)
        result["simulated"] = True
        self.assertEqual(flow.finish(run_id, result)["reason"], "ADAPTER_MODE_MISMATCH")

    def test_verified_real_result_is_draft_without_simulation_prefix(self):
        flow, run_id = self.start(StubAdapter())
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
        completed = flow.finish(run_id, StubAdapter().generate(snapshot))
        self.assertEqual(completed["state"], "GENERATED")
        outbox = self.db.one("SELECT * FROM outbox WHERE id=?", (completed["outbox_id"],))
        self.assertEqual((outbox["simulated"], outbox["state"]), (0, "PENDING"))
        self.assertNotIn("模拟答复", outbox["body"])
        self.assertEqual(self.db.one("SELECT simulated FROM answer_evidence WHERE answer_id=?", (completed["answer_id"],))[0], 0)
        flow.approve(outbox["id"])
        self.assertEqual(self.db.one("SELECT review_status FROM outbox WHERE id=?", (outbox["id"],))[0], "APPROVED")
        self.assertEqual(flow.dispatch(outbox["id"]), "STALE")
        self.assertEqual(self.db.one("SELECT last_error FROM outbox WHERE id=?", (outbox["id"],))[0], "REAL_SEND_DISABLED")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM delivery_checks WHERE outbox_id=?", (outbox["id"],))[0], 0)

    def test_test_answer_copy_is_pinned_and_does_not_complete_student_answer(self):
        flow, run_id = self.start(StubAdapter())
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
        source_id = flow.finish(run_id, StubAdapter().generate(snapshot))["outbox_id"]
        recipient = TestRecipient("wecom", "verified-test-session", "苇中鹤", "fixture verification", datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "MANUAL_REVIEW_REQUIRED"):
            queue_test_answer(self.db, source_id, recipient)
        flow.approve(source_id)
        copy_id = queue_test_answer(self.db, source_id, recipient)
        self.assertEqual(queue_test_answer(self.db, source_id, recipient), copy_id)
        copy = self.db.one("SELECT * FROM outbox WHERE id=?", (copy_id,))
        self.assertEqual(copy["body"], render_test_body(copy_id, self.db.one("SELECT body FROM outbox WHERE id=?", (source_id,))[0]))
        self.assertEqual(copy["body"], self.db.one("SELECT body FROM outbox WHERE id=?", (source_id,))[0])
        self.assertNotIn(TEST_PREFIX, copy["body"])
        self.assertEqual(self.db.one("SELECT body_format FROM test_answer_copies WHERE test_outbox_id=?", (copy_id,))[0], PLAIN_BODY_FORMAT)
        self.assertNotIn("\n", copy["body"])
        provenance = self.db.one("SELECT verification_evidence,verified_at FROM test_answer_copies WHERE test_outbox_id=?", (copy_id,))
        self.assertEqual(provenance["verification_evidence"], recipient.verification_evidence)
        self.assertEqual(provenance["verified_at"], recipient.verified_at.isoformat())

        class TestTransport:
            sends = 0
            simulated = False
            test_only = True
            test_answer_transport = True
            lock_path = str(self.base / "test-transport.lock")
            def authorize(self, bound):
                if (bound.outbox_id, bound.binding_id, bound.group_key, bound.student_key, bound.body_hash) != self.pin:
                    raise ValueError("NOT_PINNED")
            def preflight(self, bound):
                self.authorize(bound)
            def send(self, bound):
                self.authorize(bound)
                self.sends += 1
                return {"confirmed": True, "simulated": False, "body_hash": bound.body_hash,
                        "target_platform":"wecom", "target_key":recipient.stable_key,
                        "source_student_delivered":False}
            def reconcile(self, bound):
                return self.send(bound)
        transport = TestTransport()
        source = self.db.one("SELECT binding_id FROM outbox WHERE id=?", (source_id,))
        bound = BoundMessage(copy_id, source[0], "wecom", recipient.stable_key, copy["body"])
        transport.pin = (bound.outbox_id, bound.binding_id, bound.group_key, bound.student_key, bound.body_hash)
        self.assertEqual(Workflow(self.db, desktop=transport).dispatch(copy_id), "SENT_UI_CONFIRMED")
        self.assertEqual(queue_test_answer(self.db, source_id, recipient), copy_id)
        self.assertEqual(Workflow(self.db, desktop=transport).dispatch(copy_id), "SENT_UI_CONFIRMED")
        self.assertEqual(transport.sends, 1)
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (source_id,))[0], "PENDING")
        self.assertEqual(self.db.one("SELECT status FROM questions WHERE id=(SELECT question_id FROM turns WHERE id=?)", (self.turn,))[0], "AWAITING_REVIEW")
        self.assertEqual(self.app.context(self.turn)["sent_history"], [])
        repeated = flow.generate(self.turn)
        self.assertEqual(repeated['outbox_id'], source_id)
        self.assertEqual(repeated['state'], 'PENDING')
        self.assertEqual(flow.finish(run_id, StubAdapter().generate(snapshot))['outbox_id'], source_id)
        # A claimed receipt with the right body but a different target cannot
        # become confirmation for this test copy.
        with self.db.transaction():
            row=self.db.one('SELECT * FROM outbox WHERE id=?',(copy_id,))
            state=Workflow(self.db,desktop=transport)._record_check(row,{
                'confirmed':True,'simulated':False,'body_hash':bound.body_hash,
                'target_platform':'wecom','target_key':'different-session','source_student_delivered':False})
        self.assertEqual(state,'SEND_UNKNOWN')
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM human_tasks WHERE reason=?",('TEST_SEND_UNKNOWN:'+copy_id,))[0],1)

    def test_source_change_invalidates_test_answer_copy(self):
        flow, run_id = self.start(StubAdapter())
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
        source_id = flow.finish(run_id, StubAdapter().generate(snapshot))["outbox_id"]
        flow.approve(source_id)
        recipient = TestRecipient("wecom", "verified-test-session", "苇中鹤", "fixture verification", datetime.now(timezone.utc))
        copy_id = queue_test_answer(self.db, source_id, recipient)
        self.db.execute("UPDATE outbox SET body='changed' WHERE id=?", (source_id,))
        with self.assertRaisesRegex(ValueError, "ANSWER_INVALID"):
            queue_test_answer(self.db, source_id, recipient)
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (copy_id,))[0], "PENDING")

    def test_reference_revocation_stops_test_copy_before_transport(self):
        from helpdesk.reference_resolution import ReferenceResolution
        question = self.app.context(self.turn)
        resolution = ReferenceResolution(self.db)
        candidate, _ = resolution.add(question['question_version'], 'anonymous-reference',
            self.app.question(question['question_version']), question['student_material'], 'SELF_AUTHORED_FIXTURE',
            provenance={'source_policy': {'kind': 'SELF_AUTHORED_OFFLINE', 'business_record_storage_allowed': True}})
        review = dict(question_version=question['question_version'], context_revision=question['context_revision'],
            reviewer='匿名核对人', reason='匿名题面逐字段一致')
        resolution.review(candidate, decision='confirm', consume=True, **review)
        source, copy, recipient = self.approved_test_copy()
        resolution.review(candidate, decision='reject', **(review | {'reason': '撤销匿名候选使用'}))
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CONFIRMATION_CHANGED'):
            queue_test_answer(self.db, source, recipient)
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CONFIRMATION_CHANGED'):
            validate_test_copy(self.db, self.db.one('SELECT * FROM outbox WHERE id=?', (copy,)))
        class NeverSend:
            simulated = False
            test_only = True
            test_answer_transport = True
            lock_path = str(self.base / 'never-send.lock')
            def authorize(self, bound):
                raise AssertionError('Revoked reference must not reach the desktop')
        self.assertEqual(Workflow(self.db, desktop=NeverSend()).dispatch(copy), 'STALE')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_student_correction_invalidates_queued_test_copy(self):
        flow, run_id = self.start(StubAdapter())
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
        source_id = flow.finish(run_id, StubAdapter().generate(snapshot))["outbox_id"]
        flow.approve(source_id)
        recipient = TestRecipient("wecom", "verified-test-session", "苇中鹤", "fixture verification", datetime.now(timezone.utc))
        copy_id = queue_test_answer(self.db, source_id, recipient)
        original_message = self.db.one("SELECT message_id FROM turns WHERE id=?", (self.turn,))[0]
        corrected_question = demo_question(stem="Why did he NOT return home?")
        self.app.ingest(Incoming(self.db.one("SELECT binding_id FROM outbox WHERE id=?", (source_id,))[0],
            "拍错了", Intent.CORRECTION, quote_message_id=original_message,
            verified_question=corrected_question))
        with self.assertRaisesRegex(ValueError, "Approved real pending answer required"):
            queue_test_answer(self.db, source_id, recipient)
        class NeverSend:
            simulated = False
            test_only = True
            test_answer_transport = True
            lock_path = str(self.base / "never-send.lock")
            def authorize(self, bound):
                raise AssertionError("Stale copy must not reach desktop")
        self.assertEqual(Workflow(self.db, desktop=NeverSend()).dispatch(copy_id), "STALE")

    def test_real_evidence_mismatch_rejected(self):
        for change, reason in (({"adapter": "OTHER"}, "ADAPTER_MODE_MISMATCH"),
                               ({"run_id": "other"}, "RUN_MISMATCH"),
                               ({"web_session_evidence": ""}, "WEB_SESSION_EVIDENCE_MISSING"),
                               ({"uploaded_teaching_hashes": {}}, "TEACHING_UPLOAD_MISMATCH")):
            with self.subTest(reason=reason):
                # A fresh turn prevents a rejected run from affecting the next case.
                adapter = StubAdapter()
                flow, run_id = self.start(adapter)
                snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (run_id,))[0])
                result = adapter.generate(snapshot) | change
                completed = flow.finish(run_id, result)
                self.assertEqual(completed["reason"], reason)
                self.assertIsNone(completed["outbox_id"])

    def test_exception_is_durable_and_generate_does_not_retry(self):
        adapter = StubAdapter(fails=True)
        flow, verified = self.flow(adapter)
        with patch("helpdesk.workflow.verify_bundle", return_value=verified):
            first = flow.generate(self.turn)
            second = flow.generate(self.turn)
        self.assertEqual(first["reason"], "GENERATION_UNCERTAIN")
        self.assertEqual(second["reason"], "GENERATION_UNCERTAIN")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM human_tasks WHERE reason='GENERATION_UNCERTAIN'")[0], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)

    def test_existing_running_run_is_not_invoked_again(self):
        adapter = StubAdapter()
        flow, run_id = self.start(adapter)
        result = flow.generate(self.turn)
        self.assertEqual(result["state"], "RUNNING")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM runs WHERE id=?", (run_id,))[0], 1)

    def test_default_mock_still_generates_simulated_answer(self):
        result = Workflow(self.db).generate(self.turn)
        self.assertEqual(result["state"], "GENERATED")
        row = self.db.one("SELECT simulated,body FROM outbox WHERE id=?", (result["outbox_id"],))
        self.assertEqual(row["simulated"], 1)
        self.assertIn("模拟答复", row["body"])


if __name__ == "__main__":
    unittest.main()
