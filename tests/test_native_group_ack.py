"""Anonymous local persistence and scripted native observations; no real desktop."""
from copy import deepcopy
from hashlib import sha256
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from helpdesk.automatic_delivery_runtime import AutomaticDeliveryRuntime
from helpdesk.delivery import BoundMessage, PreflightFailure
from helpdesk.delivery_tasks import AutomaticDelivery
from helpdesk.native_group_ack import NativeGroupAckDesktop
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


def config():
    return {"backend": "NATIVE_ACK", "allow_real_send": True, "platform": "wecom",
            "controls_verified": True, "reviewer": "synthetic fixture", "verification_evidence": "MOCK ONLY",
            "groups": [{"group_key": "room", "header_name": "English-A", "student_keys": ["student"],
                        "pin": {"platform": "wecom", "target_key": "room", "display_name": "English-A",
                                "image_size": [200, 200], "display_bounds": [0, 0, 200, 200], "display_index": 0,
                                "window_geometry_handle": [200, 200, 1], "expires_at": time.time()+300,
                                "header_box": [0, 0, 200, 20], "header_hash": "a"*64,
                                "avatar_box": [0, 20, 20, 40], "avatar_hash": "b"*64,
                                "editor_box": [0, 150, 150, 180], "compose_point": [50, 160],
                                "send_point": [170, 170], "send_button_box": [160, 160, 190, 180],
                                "send_button_hash": "c"*64, "outgoing_viewport": [20, 40, 190, 140],
                                "outgoing_right": 190, "outgoing_color": [200, 230, 255]}}]}


class NativeAckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "business.db")
        self.addCleanup(self.store.close)
        app = Helpdesk(self.store)
        self.binding = app.bind("room", "student", "匿名学生", verified=True)
        incoming = app.ingest(Incoming(self.binding, "请问这道题怎么做", source="collector:windows_gui"))
        self.row = self.store.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (incoming.message_id,))
        self.assertIsNotNone(self.row)
        self.store.execute("UPDATE outbox SET simulated=0 WHERE id=?", (self.row["id"],))
        self.row = self.store.one("SELECT * FROM outbox WHERE id=?", (self.row["id"],))
        self.bound = BoundMessage(self.row["id"], self.binding, "room", "student", "收到")
        self.desktop = NativeGroupAckDesktop(config(), self.root, self.store, lambda db, row: True)
        self.desktop.guard = lambda message: None

    def test_exact_recipient_fixed_ack_and_source_required(self):
        self.assertEqual(self.desktop.authorize(self.bound)["group_key"], "room")
        for bad in (replace(self.bound, body="答案"), replace(self.bound, student_key="other"),
                    replace(self.bound, group_key="other"), replace(self.bound, binding_id="other")):
            with self.assertRaises(PreflightFailure):
                self.desktop.authorize(bad)
        self.desktop.source_validator = lambda db, row: False
        with self.assertRaisesRegex(PreflightFailure, "ACTUAL_ACK_SOURCE_REQUIRED"):
            self.desktop.authorize(self.bound)

    def test_rejects_answer_even_if_body_is_received(self):
        self.store.execute("UPDATE outbox SET purpose='ANSWER' WHERE id=?", (self.row["id"],))
        with self.assertRaises(PreflightFailure):
            self.desktop.authorize(self.bound)

    def test_expiry_duplicate_group_and_control_bounds_rejected(self):
        self.desktop.config["groups"][0]["pin"]["expires_at"] = 1
        with self.assertRaisesRegex(PreflightFailure, "EXPIRED"):
            self.desktop.authorize(self.bound)
        for change in (lambda c: c["groups"].append(deepcopy(c["groups"][0])),
                       lambda c: c["groups"][0]["pin"].update(compose_point=[170, 170]),
                       lambda c: c["groups"][0]["pin"].update(expires_at=float("nan")),
                       lambda c: c["groups"][0].update(header_name="wrong")):
            value = config()
            change(value)
            with self.assertRaises(ValueError):
                NativeGroupAckDesktop.validate_config(value)

    def test_native_backend_selected_without_starting_mcp_or_native_input(self):
        runtime = AutomaticDeliveryRuntime(self.store.path, {"enabled": True, "desktop": config()})
        with patch("helpdesk.wecom_native.NativeWeComTransport", side_effect=AssertionError("must remain lazy")):
            engine = runtime._engine(self.store, None)
            self.assertIsInstance(engine.flow.desktop, NativeGroupAckDesktop)
            self.assertIsNone(engine.flow.desktop.transport)
        with self.assertRaises(ValueError):
            AutomaticDeliveryRuntime(self.store.path, {"enabled": True, "desktop": {"backend": "arbitrary"}})

    def test_missing_verified_collector_blocks_before_any_native_operation(self):
        flow = Workflow(self.store)
        flow.set_delivery_policy({"ACK": "AUTO"}, "synthetic-policy")
        runtime = AutomaticDeliveryRuntime(self.store.path, {"enabled": True, "desktop": config()})
        with patch("helpdesk.wecom_native.NativeWeComTransport", side_effect=AssertionError("must not input")):
            result = runtime.tick()
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(self.store.one("SELECT last_error FROM outbox WHERE id=?", (self.row["id"],))[0], "ACK_SOURCE_REQUIRES_REVIEW")

    def test_status_does_not_reuse_other_backend_receipts_as_native_validation(self):
        import json
        self.store.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED' WHERE id=?", (self.row["id"],))
        self.store.execute("INSERT INTO audit(event,outbox_id,details,created_at) VALUES(?,?,?,?)",
                           ("TASK_SEND_CONFIRMED", self.row["id"], "{}", "fixture"))
        self.store.execute("INSERT INTO delivery_checks VALUES(?,?,?,?,?)",
                           ("check", self.row["id"], "SENT_UI_CONFIRMED", "{}", "fixture"))
        runtime = AutomaticDeliveryRuntime(self.store.path, {"enabled": True, "desktop": config()})
        state = runtime.snapshot(self.store)
        self.assertEqual(state["allowed_purposes"], ["ACK"])
        self.assertEqual(state["live_validation"], "NOT_VERIFIED")
        self.assertEqual(state["real_confirmed_tasks"], 0)
        self.store.execute("UPDATE delivery_checks SET evidence=? WHERE id='check'", (json.dumps({"transport": "NATIVE_ACK"}),))
        self.assertEqual(runtime.snapshot(self.store)["real_confirmed_tasks"], 1)

    def test_preflight_never_inputs_existing_draft_or_replays_attempt(self):
        self.desktop._observe = lambda message: {"editor_blank": False}
        with self.assertRaisesRegex(PreflightFailure, "EDITOR_OCCUPIED"):
            self.desktop.preflight(self.bound)
        self.desktop._save(self.desktop._path(self.bound), {"state": "SUBMISSION_UNCONFIRMED"}, exclusive=True)
        self.desktop._observe = lambda message: {"editor_blank": True}
        with self.assertRaisesRegex(PreflightFailure, "EXISTING_GROUP_ATTEMPT"):
            self.desktop.preflight(self.bound)

    def test_draft_failure_does_not_click_send_and_cannot_be_automatically_replayed(self):
        calls = []
        self.desktop._observe = lambda message: {"editor_blank": True, "native": {}}
        self.desktop._native_call = lambda message, tool, args: calls.append((tool, args))
        self.desktop._clipboard_native = lambda message: "other text"
        from helpdesk.delivery import NotSubmitted
        with self.assertRaises(NotSubmitted):
            self.desktop.send(self.bound)
        self.assertFalse(any(tool == "Click" and args.get("loc") == [170, 170] for tool, args in calls))
        with self.assertRaises(PreflightFailure):
            self.desktop.preflight(self.bound)

    def test_submit_exception_happens_after_durable_unknown_checkpoint_without_retry(self):
        calls = []
        self.desktop._observe = lambda message: {"editor_blank": True, "native": {}, "send_enabled": True}
        self.desktop._clipboard_native = lambda message: "收到"
        def call(message, tool, args):
            calls.append((tool, args))
            if tool == "Click" and args["loc"] == [170, 170]:
                raise TimeoutError("synthetic submit timeout")
        self.desktop._native_call = call
        with self.assertRaises(TimeoutError):
            self.desktop.send(self.bound)
        import json
        record = json.loads(self.desktop._path(self.bound).read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "SUBMISSION_UNCONFIRMED")
        self.assertEqual(sum(tool == "Click" and args.get("loc") == [170, 170] for tool, args in calls), 1)

    def test_unknown_readback_without_baseline_never_sends(self):
        self.store.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (self.row["id"],))
        self.assertFalse(self.desktop.reconcile(self.bound)["confirmed"])
        self.assertIsNone(self.desktop.transport)
        self.assertFalse(self.desktop._reading_receipt)

    def test_fresh_native_frame_checks_header_and_window_before_input(self):
        from PIL import Image
        from helpdesk.mcp_test_delivery import pixel_hash
        from helpdesk.mcp_window_probe import FOREGROUND_COMMAND
        import json
        image = Image.new("RGB", (200, 200), "white")
        path = self.root / "frame.png"
        image.save(path)
        pin = self.desktop.config["groups"][0]["pin"]
        for field in ("header", "avatar", "send_button"):
            pin[field+"_hash"] = pixel_hash(image, pin[field+"_box"])
        calls = []
        class NativeFixture:
            def __init__(self, *args, **kwargs):
                pass
            def call(self, tool, arguments):
                calls.append((tool, arguments))
                if tool == "PowerShell":
                    self.assert_command = arguments["command"] == FOREGROUND_COMMAND
                    if not self.assert_command:
                        raise AssertionError("unknown command")
                    content = [{"type": "text", "text": 'Response: ' + json.dumps({"process": "WXWork", "width": 200,
                        "height": 200, "handle": 1, "left": 0, "top": 0}) + '\nStatus Code: 0'}]
                elif tool == "Screenshot":
                    content = [{"type": "image", "path": str(path)}]
                else:
                    raise AssertionError("preflight must not input")
                return {"tool": tool, "is_error": False, "content": content}
        self.desktop.native_factory = NativeFixture
        self.desktop.preflight(self.bound)
        self.assertTrue(all(tool in ("PowerShell", "Screenshot") for tool, _ in calls))
        image.putpixel((1, 1), (0, 0, 0))
        image.save(path)
        with self.assertRaises(PreflightFailure):
            self.desktop.preflight(self.bound)

    def test_exact_appended_receipt_confirms_without_send_or_paste_in_unknown_state(self):
        from PIL import Image
        path = self.root / "receipt.png"
        Image.new("RGB", (200, 200), "white").save(path)
        import json
        recipe_hash = sha256(json.dumps(self.desktop.config, sort_keys=True).encode()).hexdigest()
        record = {"state": "SUBMISSION_UNCONFIRMED", "recipe_sha256": recipe_hash,
                  "before": {"outgoing": ["old-bubble"]}}
        record.update({k: getattr(self.bound, k) for k in ("outbox_id", "binding_id", "group_key", "student_key", "body_hash")})
        self.desktop._save(self.desktop._path(self.bound), record, exclusive=True)
        self.store.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (self.row["id"],))
        frame = {"editor_blank": True, "outgoing": ["old-bubble", "new-bubble"], "path": str(path),
                 "boxes": [[100, 40, 150, 60], [100, 80, 150, 100]], "record": {"attempt_id": "fixture"}}
        self.desktop._observe = lambda message: frame
        calls = []
        self.desktop._native_call = lambda message, tool, args: calls.append((tool, args))
        self.desktop._clipboard_native = lambda message: "收到"
        with patch("helpdesk.wecom_control_ocr.locate_copy", return_value={"loc": [120, 110]}):
            result = self.desktop.reconcile(self.bound)
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["server_receipt"])
        self.assertTrue(all(tool == "Click" and args["loc"] != [170, 170] for tool, args in calls))
        frame["outgoing"] = ["old-bubble"]
        self.assertFalse(self.desktop.reconcile(self.bound)["confirmed"])

    def test_native_ack_worker_skips_answers_instead_of_invalidating_them(self):
        flow = Workflow(self.store)
        flow.set_delivery_policy({"ACK": "DISABLED", "ANSWER": "AUTO"}, "synthetic-policy")
        self.store.execute("UPDATE outbox SET purpose='ANSWER',review_status='APPROVED' WHERE id=?", (self.row["id"],))
        engine = AutomaticDelivery(self.store, self.desktop, ack_source_validator=lambda db, row: True)
        self.assertIsNone(engine._candidate())
        self.assertEqual(self.store.one("SELECT state FROM outbox WHERE id=?", (self.row["id"],))[0], "PENDING")

    def test_reused_transport_guards_current_ack_instead_of_first_completed_ack(self):
        observed = []
        class NativeFixture:
            def __init__(self, pin, workspace, *, guard):
                self.guard = guard
            def call(self, tool, arguments):
                self.guard()
                return {"is_error":False}
        self.desktop.native_factory = NativeFixture
        self.desktop.guard = lambda message: observed.append(message.outbox_id)
        self.desktop._native_call(self.bound, 'Screenshot', {})
        app = Helpdesk(self.store)
        incoming = app.ingest(Incoming(self.binding, '请问另一道题', source='collector:windows_gui'))
        row = self.store.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (incoming.message_id,))
        self.store.execute("UPDATE outbox SET simulated=0 WHERE id=?", (row['id'],))
        second = replace(self.bound, outbox_id=row['id'])
        self.desktop._native_call(second, 'Screenshot', {})
        self.assertEqual(observed[-2:], [second.outbox_id, second.outbox_id])

    def test_other_groups_ack_remains_pending_instead_of_being_invalidated(self):
        self.store.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED' WHERE id=?", (self.row['id'],))
        app = Helpdesk(self.store)
        binding = app.bind('other-room', 'other-student', '另一匿名学生', verified=True)
        incoming = app.ingest(Incoming(binding, '请问另一道题', source='collector:windows_gui'))
        row = self.store.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (incoming.message_id,))
        flow = Workflow(self.store)
        flow.set_delivery_policy({'ACK':'AUTO'}, 'synthetic-policy')
        engine = AutomaticDelivery(self.store, self.desktop, ack_source_validator=lambda db, row: True)
        self.assertEqual(engine.tick()['state'], 'IDLE')
        self.assertEqual(self.store.one('SELECT state FROM outbox WHERE id=?', (row['id'],))[0], 'PENDING')
        self.assertIsNone(self.desktop.transport)


if __name__ == "__main__":
    unittest.main()
