import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.performance_report_schedule import run_logged
from tools.performance_report import export_xlsx


class BackgroundRunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "schedule.toml"
        self.logs = self.root / "logs"
        self.config.write_text('database = "unused.db"\n'
                               f'run_log_dir = "{self.logs.as_posix()}"\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_success_and_idempotent_no_change_have_distinct_run_evidence(self):
        result = [{"date": "2026-09-29", "version": 1, "preview_only": False}]
        with patch("tools.performance_report_schedule.run", side_effect=[result, []]):
            self.assertEqual(run_logged(str(self.config)), result)
            self.assertEqual(run_logged(str(self.config)), [])
        last = json.loads((self.logs / "last-run.json").read_text(encoding="utf-8"))
        self.assertEqual(last["status"], "SUCCESS")
        self.assertEqual(last["exit_code"], 0)
        self.assertEqual(last["generated_or_repaired_count"], 0)
        events = [json.loads(line) for line in (self.logs / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["status"] for event in events], ["RUNNING", "SUCCESS", "RUNNING", "SUCCESS"])
        self.assertNotEqual(events[0]["run_id"], events[2]["run_id"])

    def test_export_failure_is_recorded_and_propagated_for_task_failure(self):
        with patch("tools.performance_report_schedule.run", side_effect=RuntimeError("XLSX export failed")):
            with self.assertRaisesRegex(RuntimeError, "XLSX export failed"):
                run_logged(str(self.config))
        last = json.loads((self.logs / "last-run.json").read_text(encoding="utf-8"))
        self.assertEqual(last["status"], "FAILED")
        self.assertEqual(last["exit_code"], 1)
        self.assertEqual(last["error_type"], "RuntimeError")
        self.assertIn("finished_at", last)
        self.assertFalse(list(self.logs.glob("*.tmp")))

    def test_xlsx_fallback_children_are_hidden_on_windows(self):
        runtime = self.root / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool"
        runtime.mkdir(parents=True)
        with patch("tools.performance_report.Path.home", return_value=self.root), \
             patch("tools.performance_report.os.symlink", side_effect=OSError("force junction fallback")), \
             patch("tools.performance_report.subprocess.run") as child:
            export_xlsx({}, self.root / "report.xlsx")
        self.assertEqual([call.args[0][0] for call in child.call_args_list], ["powershell", "node"])
        for call in child.call_args_list:
            self.assertTrue(call.kwargs["check"])
            if os.name == "nt":
                self.assertEqual(call.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            else:
                self.assertNotIn("creationflags", call.kwargs)


if __name__ == "__main__":
    unittest.main()
