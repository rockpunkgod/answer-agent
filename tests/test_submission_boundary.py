"""A known unsent draft and a possibly submitted message have different recovery paths."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from helpdesk.__main__ import demo_question
from helpdesk.delivery import BoundMessage, MockDesktop, NotSubmitted
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.windows_test import WeComVisualProbe
from helpdesk.workflow import Workflow


class SubmissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Store(Path(self.temp.name)/"business.db")
        self.desktop=MockDesktop(Path(self.temp.name)/"receipts.db")
        self.flow=Workflow(self.db,desktop=self.desktop)
        app=Helpdesk(self.db)
        student=app.bind("g","s","demo",verified=True)
        first=app.ingest(Incoming(student,"题",Intent.NEW,verified_question=demo_question(),verified_material="P"))
        self.oid=self.flow.generate(first.turn_id)["outbox_id"]
        self.flow.approve(self.oid)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_known_unsent_draft_is_failed_and_never_auto_retried(self):
        self.desktop.send=Mock(side_effect=NotSubmitted("DRAFT_VERIFICATION_FAILED"))
        self.assertEqual(self.flow.dispatch(self.oid),"FAILED")
        self.assertEqual(self.flow.dispatch(self.oid),"FAILED")
        self.desktop.send.assert_called_once()
        evidence=json.loads(self.db.one("SELECT evidence FROM delivery_checks WHERE outbox_id=?",(self.oid,))[0])
        self.assertFalse(evidence["submission_attempted"])
        self.assertTrue(evidence["draft_may_remain"])
        self.assertTrue(self.db.one("SELECT id FROM human_tasks WHERE reason LIKE 'UNSENT_DRAFT_REVIEW:%'"))
        self.assertEqual(self.desktop.receipts(),[])

    def test_unknown_send_exception_stays_unknown(self):
        self.desktop.send=Mock(side_effect=RuntimeError("unclassified transport error"))
        self.assertEqual(self.flow.dispatch(self.oid),"SEND_UNKNOWN")
        self.assertEqual(self.flow.dispatch(self.oid),"SEND_UNKNOWN")
        self.desktop.send.assert_called_once()

    def test_visual_adapter_never_clicks_submit_after_draft_failure(self):
        probe=object.__new__(WeComVisualProbe)
        probe._prepare_draft=Mock(side_effect=NotSubmitted("DRAFT_VERIFICATION_FAILED"))
        probe._click=Mock()
        with self.assertRaises(NotSubmitted):
            probe.send(BoundMessage("o","b","g","s","TEST 123"))
        probe._click.assert_not_called()

    def test_visual_submit_click_error_cannot_be_reclassified_unsent(self):
        probe=object.__new__(WeComVisualProbe)
        probe._prepare_draft=Mock()
        probe._click=Mock(side_effect=OSError("mouse action result unknown"))
        with self.assertRaises(OSError):
            probe.send(BoundMessage("o","b","g","s","TEST 123"))
        probe._click.assert_called_once_with(1500,922)

    def test_failed_readonly_check_preserves_unknown(self):
        self.desktop.fault="unknown"
        self.assertEqual(self.flow.dispatch(self.oid),"SEND_UNKNOWN")
        self.desktop.reconcile=Mock(side_effect=RuntimeError("readback unavailable"))
        self.assertEqual(self.flow.inspect_unknown(self.oid),"SEND_UNKNOWN")
        self.assertEqual(len(self.desktop.receipts()),1)
