"""No live desktop calls. Verify the extra restrictions of the optional smoke probe."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.delivery import BoundMessage, PreflightFailure
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.test_routing import TestRecipient, TestRoutingPolicy
from helpdesk.windows_test import WeComVisualProbe
from helpdesk.workflow import Workflow


class ProbeGateTests(unittest.TestCase):
    def probe(self):
        probe=object.__new__(WeComVisualProbe)
        probe.pin={"session_key":"wecom-session-verified","outbox_id":"pinned-outbox"}
        probe.policy=TestRoutingPolicy(TestRecipient("wecom","wecom-session-verified","苇中鹤","fixture evidence",datetime.now(timezone.utc)),enabled=True)
        probe._frame=lambda expected: {"editor_blank":True}
        return probe

    def test_probe_only_accepts_pinned_outbox(self):
        with self.assertRaisesRegex(PreflightFailure,"NOT_PINNED"):
            self.probe().preflight(BoundMessage("other","b","g","s","TEST 123"))

    def test_probe_cannot_deliver_arbitrary_student_answer(self):
        for body in ("第12题选D", "Normal message", "TEST "+"x"*200):
            with self.subTest(body=body),self.assertRaises(PreflightFailure):
                self.probe().preflight(BoundMessage("pinned-outbox","b","g","s",body))

    def test_probe_will_not_overwrite_existing_draft(self):
        probe=self.probe()
        probe._frame=lambda expected:{"editor_blank":False}
        with self.assertRaisesRegex(PreflightFailure,"NOT_EMPTY"):
            probe.preflight(BoundMessage("pinned-outbox","b","g","s","TEST 123"))

    def test_real_probe_transport_cannot_send_regular_ack(self):
        class Probe:
            simulated=False
            test_only=True
            def preflight(self,message):
                raise AssertionError("Must never reach GUI for regular ACK")
        with tempfile.TemporaryDirectory() as folder:
            db=Store(Path(folder)/"db")
            try:
                app=Helpdesk(db)
                bid=app.bind("g","s","student",verified=True)
                first=app.ingest(Incoming(bid,"题",Intent.NEW,verified_question=demo_question(),verified_material="P"))
                probe=Probe()
                probe.lock_path=str(Path(folder)/"lock")
                oid=db.one("SELECT id FROM outbox WHERE message_id=?",(first.message_id,))[0]
                self.assertEqual(Workflow(db,desktop=probe).dispatch(oid),"STALE")
            finally:
                db.close()
