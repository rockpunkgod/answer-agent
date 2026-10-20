"""Synthetic hints and scripted subprocesses only; no actual desktop or sending."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from helpdesk.unread_wakeup import UnreadWakeMonitor


def reviewed():
    return {"enabled": True, "reviewer": "synthetic", "verification_evidence": "fixture only",
            "image_size": [200, 100], "indicator_box": [10, 10, 30, 30],
            "poll_seconds": 5, "repeat_seconds": 60, "timeout_seconds": 2}


class UnreadWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.instant = 0
        self.current = {"unread": False, "signature": "clear"}
        self.monitor = UnreadWakeMonitor(reviewed(), lambda: self.calls.append(self.instant),
            root=self.root, probe=lambda: self.current, clock=lambda: self.instant)
        self.addCleanup(self.monitor.stop)

    def sample(self, instant, unread, signature):
        self.instant = instant
        self.current = {"unread": unread, "signature": signature}
        self.monitor.sample_once()

    def test_unread_change_debounce_deferred_change_and_persistent_hint_repeat(self):
        self.sample(0, False, "clear")
        self.assertEqual(self.calls, [])
        self.sample(5, True, "one")
        self.sample(10, True, "one")
        self.sample(11, True, "two")
        self.sample(12, True, "three")  # Too soon; retained until next due sample.
        self.sample(16, True, "three")
        self.sample(75, True, "three")
        self.sample(76, True, "three")
        self.sample(81, False, "clear")
        self.sample(86, True, "one")
        self.assertEqual(self.calls, [5, 11, 16, 76, 86])
        state = self.monitor.snapshot()
        self.assertEqual(state["wake_count"], 5)
        self.assertFalse(state["message_source_verified"])
        self.assertFalse(state["freshness_verified"])
        self.assertFalse(state["can_send"])

    def test_failure_is_unknown_not_no_messages_and_recovery_rechecks(self):
        self.sample(0, True, "one")
        self.monitor.probe = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE CHAT MUST NOT LEAK"))
        self.monitor.sample_once()
        state = self.monitor.snapshot()
        self.assertEqual(state["state"], "UNAVAILABLE")
        self.assertIsNone(state["unread_hint"])
        self.assertNotIn("PRIVATE CHAT", json.dumps(state))
        self.monitor.probe = lambda: self.current
        self.sample(10, True, "one")
        self.assertEqual(self.calls, [0, 10])

    def test_disabled_or_stopped_monitor_never_probes_or_wakes(self):
        self.monitor.probe = lambda: self.fail("stopped monitor must not probe")
        self.monitor.stop()
        self.monitor.sample_once()
        self.assertEqual(self.monitor.snapshot()["state"], "STOPPED")
        disabled = UnreadWakeMonitor({"enabled": False}, lambda: self.fail("must not wake"),
                                    probe=lambda: self.fail("must not probe"))
        disabled.sample_once()
        disabled.start()
        self.assertFalse(disabled.snapshot()["worker_alive"])

    def test_background_thread_stop_interrupts_wait_without_next_probe(self):
        entered = Event()
        count = []
        self.monitor.probe = lambda: (count.append(True), entered.set(), self.current)[-1]
        self.monitor.start()
        self.assertTrue(entered.wait(1))
        self.monitor.stop()
        self.assertFalse(self.monitor.snapshot()["worker_alive"])
        self.assertEqual(count, [True])

    def test_red_mask_ignores_non_indicator_changes_and_detects_counter_change(self):
        image = Image.new("RGB", (200, 100), "white")
        first = UnreadWakeMonitor.indicator(image, reviewed())
        ImageDraw.Draw(image).rectangle([100, 50, 150, 80], fill="red")
        self.assertEqual(UnreadWakeMonitor.indicator(image, reviewed()), first)
        ImageDraw.Draw(image).rectangle([12, 12, 25, 25], fill=(240, 40, 50))
        red = UnreadWakeMonitor.indicator(image, reviewed())
        self.assertTrue(red["unread"])
        image.putpixel((15, 15), (255, 255, 255))
        self.assertNotEqual(UnreadWakeMonitor.indicator(image, reviewed())["signature"], red["signature"])

    def test_bad_configuration_rejected_without_probe(self):
        for change in (lambda c: c.update(image_size=[True, 100]),
                       lambda c: c.update(indicator_box=[0, 0, 201, 50]),
                       lambda c: c.update(min_red_pixels=1000),
                       lambda c: c.update(poll_seconds=0), lambda c: c.update(timeout_seconds=50),
                       lambda c: c.update(reviewer="")):
            value = reviewed()
            change(value)
            with self.assertRaises(ValueError):
                UnreadWakeMonitor(value, lambda: None)

    def prepare_native_probe(self):
        self.monitor.probe = self.monitor._probe
        self.monitor.output.parent.mkdir(parents=True)
        image = Image.new("RGB", (200, 100), "white")
        ImageDraw.Draw(image).rectangle([12, 12, 25, 25], fill=(240, 40, 50))
        image.save(self.monitor.output)
        return {"App": "WXWork", "MainWindowOnly": True, "CaptureBackend": "PRINT_WINDOW",
                "CaptureResult": True, "Minimized": False, "Geometry": [100, 200, 200, 100],
                "ImagePath": str(self.monitor.output)}

    def test_probe_is_fixed_background_command_without_activation_or_visible_capture(self):
        metadata = self.prepare_native_probe()
        result = SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode())
        with patch("helpdesk.unread_wakeup.subprocess.run", return_value=result) as run:
            self.monitor.sample_once()
        command = run.call_args.args[0]
        self.assertIn("-MainWindowOnly", command)
        self.assertNotIn("-CaptureVisible", command)
        self.assertNotIn("-Command", command)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertEqual(self.calls, [0])

    def test_failed_minimized_wrong_layout_or_wrong_path_cannot_emit_hint(self):
        metadata = self.prepare_native_probe()
        for change in ({"CaptureResult": False}, {"Minimized": True}, {"MainWindowOnly": False},
                       {"CaptureBackend": "VISIBLE_SCREEN"}, {"Geometry": [0, 0, 100, 100]},
                       {"ImagePath": str(self.root / "other.png")}):
            with self.subTest(change=change):
                result = SimpleNamespace(returncode=0, stdout=json.dumps({**metadata, **change}).encode())
                with patch("helpdesk.unread_wakeup.subprocess.run", return_value=result):
                    self.monitor.sample_once()
                self.assertEqual(self.monitor.snapshot()["state"], "UNAVAILABLE")
                self.assertEqual(self.calls, [])

    def test_blank_capture_and_timeout_are_unavailable_not_clear(self):
        metadata = self.prepare_native_probe()
        Image.new("RGB", (200, 100), "black").save(self.monitor.output)
        with patch("helpdesk.unread_wakeup.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(metadata).encode())):
            self.monitor.sample_once()
        self.assertIsNone(self.monitor.snapshot()["unread_hint"])
        with patch("helpdesk.unread_wakeup.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 2)):
            self.monitor.sample_once()
        self.assertEqual(self.monitor.snapshot()["error_type"], "TimeoutExpired")
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell unavailable; real parser not verified")
    def test_windows_powershell_parses_capture_script_without_executing_it(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "tools/probe_wecom.ps1"
        self.assertTrue(script.read_bytes().startswith(b"\xef\xbb\xbf"), "Legacy PowerShell requires explicit UTF-8 encoding")
        code = "$unreadTokens=$null; $unreadParseErrors=$null; " \
               "[void][System.Management.Automation.Language.Parser]::ParseFile(" \
               "(Resolve-Path -LiteralPath 'tools/probe_wecom.ps1'),[ref]$unreadTokens,[ref]$unreadParseErrors); " \
               "if($unreadParseErrors.Count){exit 1}; Write-Output 'SYNTAX_OK'"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", code], cwd=root,
                                capture_output=True, timeout=10, shell=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout.decode().strip(), "SYNTAX_OK")


if __name__ == "__main__":
    unittest.main()
