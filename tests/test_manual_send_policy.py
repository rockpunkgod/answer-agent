"""Durable operator policy that leaves delivery to a person."""

from pathlib import Path
import json
import tempfile
import unittest

from helpdesk.delivery import MockDesktop
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


class CountingDesktop(MockDesktop):
    def __init__(self, path, *, fault=None, before_preflight=None):
        super().__init__(path, fault=fault)
        self.calls = []
        self.before_preflight = before_preflight

    def preflight(self, message):
        self.calls.append("preflight")
        if self.before_preflight:
            self.before_preflight()
        return super().preflight(message)

    def send(self, message):
        self.calls.append("send")
        return super().send(message)

    def reconcile(self, message):
        self.calls.append("reconcile")
        return super().reconcile(message)


class ManualSendPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "business.db"
        self.desktop_path = Path(self.temp.name) / "desktop.db"
        self.db = Store(self.db_path)
        self.service = Helpdesk(self.db)
        self.desktop = CountingDesktop(self.desktop_path)
        self.workflow = Workflow(self.db, desktop=self.desktop)
        binding = self.service.bind("group", "student", "学生", verified=True)
        incoming = Incoming(binding, "好的，谢谢", Intent.IRRELEVANT, platform_id="ack-1")
        self.service.ingest(incoming)
        self.pending_id = self.db.one(
            "SELECT id FROM outbox WHERE purpose='ACK' ORDER BY rowid DESC LIMIT 1"
        )[0]

    def tearDown(self):
        try:
            self.db.close()
        finally:
            self.temp.cleanup()

    def test_policy_persists_and_blocks_pending_before_transport(self):
        self.workflow.set_manual_send(True)
        self.assertEqual(
            self.db.one("SELECT event FROM audit ORDER BY rowid DESC LIMIT 1")[0],
            "MANUAL_SEND_POLICY_CHANGED",
        )
        self.db.close()
        self.db = Store(self.db_path)
        self.service = Helpdesk(self.db)
        self.desktop = CountingDesktop(self.desktop_path)
        self.workflow = Workflow(self.db, desktop=self.desktop)

        self.assertTrue(self.workflow.dashboard()["health"]["manual_send_required"])
        self.assertEqual(self.workflow.dispatch(self.pending_id), "MANUAL_SEND_REQUIRED")
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (self.pending_id,))[0], "PENDING")
        self.assertEqual(self.desktop.calls, [])

    def test_preflight_policy_change_restores_pending_without_send(self):
        def configure_manual():
            # A stage policy can change after first preflight but before send.
            self.db.execute(
                """INSERT INTO settings(key,value) VALUES('delivery_policy','{}')
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
            )
            self.db.execute(
                """INSERT INTO settings(key,value) VALUES('delivery_stage_name','qa-stage')
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
            )

        self.desktop.before_preflight = configure_manual
        self.assertEqual(self.workflow.dispatch(self.pending_id), "MANUAL_SEND_REQUIRED")
        row = self.db.one("SELECT state,last_error FROM outbox WHERE id=?", (self.pending_id,))
        self.assertEqual(tuple(row), ("PENDING", "MANUAL_SEND_REQUIRED"))
        self.assertEqual(self.desktop.calls, ["preflight"])
        self.assertEqual(
            self.db.one("SELECT COUNT(*) FROM audit WHERE event='DELIVERY_POLICY_BLOCKED'")[0], 1
        )

    def test_manual_policy_does_not_rewrite_or_reconcile_unknown(self):
        self.desktop.fault = "unknown"
        self.assertEqual(self.workflow.dispatch(self.pending_id), "SEND_UNKNOWN")
        before = tuple(self.db.one(
            "SELECT state,sent_at,last_error FROM outbox WHERE id=?", (self.pending_id,)
        ))
        calls_before = list(self.desktop.calls)

        self.workflow.set_manual_send(True)
        self.assertEqual(self.workflow.dispatch(self.pending_id), "SEND_UNKNOWN")
        after = tuple(self.db.one(
            "SELECT state,sent_at,last_error FROM outbox WHERE id=?", (self.pending_id,)
        ))
        self.assertEqual(after, before)
        self.assertEqual(self.desktop.calls, calls_before)

    def test_disabling_policy_restores_legacy_dispatch(self):
        self.workflow.set_manual_send(True)
        self.workflow.set_manual_send(False)
        self.assertFalse(self.workflow.dashboard()["health"]["manual_send_required"])
        self.assertEqual(self.workflow.dispatch(self.pending_id), "SENT_UI_CONFIRMED")
        self.assertIn("send", self.desktop.calls)

    def test_policy_requires_exact_boolean(self):
        for value in (None, 0, 1, "true", "false"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.workflow.set_manual_send(value)
        self.assertIsNone(self.db.one("SELECT value FROM settings WHERE key='manual_send_required'"))

    def test_delivery_policy_persists_stage_and_audits_old_and_new_values(self):
        policy = {"ACK": "AUTO", "ANSWER": "MANUAL", "CORRECTION": "MANUAL"}
        self.workflow.set_delivery_policy(policy, "student-intake-v1")
        event = self.db.one("SELECT details FROM audit WHERE event='DELIVERY_POLICY_CHANGED'")
        self.assertIsNotNone(event)
        details = json.loads(event[0])
        self.assertIsNone(details["old_policy"])
        self.assertEqual(details["new_policy"], policy)
        self.assertEqual(details["new_stage_name"], "student-intake-v1")

        self.db.close()
        self.db = Store(self.db_path)
        self.service = Helpdesk(self.db)
        self.desktop = CountingDesktop(self.desktop_path)
        self.workflow = Workflow(self.db, desktop=self.desktop)
        health = self.workflow.dashboard()["health"]
        self.assertEqual(health["delivery_policy"], policy)
        self.assertEqual(health["delivery_stage_name"], "student-intake-v1")

    def test_auto_ack_policy_overrides_legacy_manual_gate_only_for_ack(self):
        self.workflow.set_manual_send(True)
        self.workflow.set_delivery_policy(
            {"ACK": "AUTO", "ANSWER": "MANUAL", "CORRECTION": "MANUAL"},
            "student-intake-v1",
        )
        self.assertEqual(self.workflow.dispatch(self.pending_id), "SENT_UI_CONFIRMED")
        self.assertIn("send", self.desktop.calls)

    def test_answer_manual_mode_blocks_even_with_legacy_auto_setting(self):
        self.workflow.set_manual_send(False)
        self.workflow.set_delivery_policy({"ANSWER": "MANUAL"}, "teaching-v1")
        self.db.execute("UPDATE outbox SET purpose='ANSWER' WHERE id=?", (self.pending_id,))
        self.assertEqual(self.workflow.dispatch(self.pending_id), "MANUAL_SEND_REQUIRED")
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (self.pending_id,))[0], "PENDING")
        self.assertEqual(self.desktop.calls, [])

    def test_disabled_mode_blocks_without_changing_pending_state(self):
        self.workflow.set_delivery_policy({"ACK": "DISABLED"}, "maintenance")
        self.assertEqual(self.workflow.dispatch(self.pending_id), "DELIVERY_DISABLED")
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (self.pending_id,))[0], "PENDING")
        self.assertEqual(self.desktop.calls, [])

    def test_unlisted_purpose_defaults_to_manual(self):
        self.workflow.set_delivery_policy({"ACK": "AUTO"}, "partial-policy")
        self.db.execute("UPDATE outbox SET purpose='REQUEST_IMAGE' WHERE id=?", (self.pending_id,))
        self.assertEqual(self.workflow.dispatch(self.pending_id), "MANUAL_SEND_REQUIRED")
        self.assertEqual(self.desktop.calls, [])

    def test_auto_policy_does_not_bypass_test_answer_transport_gate(self):
        self.workflow.set_manual_send(True)
        self.workflow.set_delivery_policy({"TEST_ANSWER": "AUTO"}, "test-lane")
        self.db.execute("UPDATE outbox SET purpose='TEST_ANSWER' WHERE id=?", (self.pending_id,))
        self.assertEqual(self.workflow.dispatch(self.pending_id), "TEST_ANSWER_TRANSPORT_DISABLED")
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE id=?", (self.pending_id,))[0], "PENDING")
        self.assertEqual(self.desktop.calls, [])

    def test_delivery_policy_rejects_invalid_schema_and_empty_stage(self):
        invalid = (
            (True, "stage"),
            ({"UNKNOWN": "AUTO"}, "stage"),
            ({"ACK": True}, "stage"),
            ({"ACK": "auto"}, "stage"),
            ({"ACK": "DISABLED"}, " "),
            ({"ACK": "AUTO"}, False),
        )
        for policy, stage in invalid:
            with self.subTest(policy=policy, stage=stage), self.assertRaises(ValueError):
                self.workflow.set_delivery_policy(policy, stage)
        self.assertIsNone(self.db.one("SELECT value FROM settings WHERE key='delivery_policy'"))
        self.assertIsNone(self.db.one("SELECT value FROM settings WHERE key='delivery_stage_name'"))


if __name__ == "__main__":
    unittest.main()
