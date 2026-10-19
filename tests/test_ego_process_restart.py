"""Real Python hard exits with a mocked ego worker, never a real browser."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.browser_adapter import BrowserPaused
from helpdesk.ego_browser import EgoDeepSeek


ROOT = Path(__file__).resolve().parents[1]
ARGS = ('restart-test', 'https://chat.deepseek.com/a/chat/s/anonymous', '第12题')
EXIT = 86


def complete(root):
    state_path = root / 'receipts/restart-test.state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    raw = json.dumps({'text': 'anonymous draft', 'delivered': False}).encode()
    (root / 'receipts/restart-test.answer.json').write_bytes(raw)
    state.update(phase='CAPTURED', output_sha256=sha256(raw).hexdigest())
    state_path.write_text(json.dumps(state), encoding='utf-8')


def crash_worker(root, phase):
    def abrupt_exit(*args, **kwargs):
        state_path = root / 'receipts/restart-test.state.json'
        if phase == 'CAPTURED':
            complete(root)
        else:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['phase'] = phase
            state_path.write_text(json.dumps(state), encoding='utf-8')
        os._exit(EXIT)

    with patch('helpdesk.ego_browser.page_control_directory', return_value=root / 'controls'), \
            patch('helpdesk.ego_browser.subprocess.run', side_effect=abrupt_exit):
        EgoDeepSeek(1, root / 'receipts', executable=root / 'unused.exe').submit(*ARGS)
    raise AssertionError('Crash point not reached')


class EgoProcessRestartTests(unittest.TestCase):
    def test_hard_exit_never_replays_and_only_capture_releases_page(self):
        for phase in ('PREPARING', 'SUBMISSION_INTENT', 'SUBMITTED', 'CAPTURED'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                child = subprocess.run(
                    [sys.executable, '-X', 'utf8', '-B', '-m', 'tests.test_ego_process_restart',
                     '--crash-worker', str(root), phase], cwd=ROOT, capture_output=True,
                    timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(child.returncode, EXIT, child.stderr.decode('utf-8', errors='replace'))
                owner = root / 'controls/space-1-p1.owner'
                state_path = root / 'receipts/restart-test.state.json'
                self.assertEqual(Path(owner.read_text(encoding='utf-8')), state_path)
                original = state_path.read_bytes()

                # A fresh adapter and released OS lock do not authorize replay.
                with patch('helpdesk.ego_browser.page_control_directory', return_value=root / 'controls'), \
                        patch('helpdesk.ego_browser.subprocess.run') as browser:
                    adapter = EgoDeepSeek(1, root / 'receipts', executable=root / 'unused.exe')
                    if phase == 'CAPTURED':
                        result = adapter.submit(*ARGS)
                        self.assertEqual(result['text'], 'anonymous draft')
                        self.assertFalse(result['delivered'])
                        self.assertFalse(owner.exists())
                    else:
                        with self.assertRaisesRegex(BrowserPaused, 'PRIOR_ATTEMPT'):
                            adapter.submit(*ARGS)
                        other = EgoDeepSeek(1, root / 'other', executable=root / 'unused.exe')
                        with self.assertRaisesRegex(BrowserPaused, 'PAGE_HAS_UNRESOLVED_REQUEST'):
                            other.submit('other-task', ARGS[1], 'another question')
                        self.assertFalse((root / 'other/other-task.state.json').exists())
                        self.assertEqual(state_path.read_bytes(), original)
                        if phase == 'PREPARING':
                            with self.assertRaisesRegex(BrowserPaused, 'PREPARATION_INCOMPLETE'):
                                adapter.submit(*ARGS, capture_only=True)
                            self.assertTrue(owner.exists())
                        else:
                            def capture(*args, **kwargs):
                                scripts = list((root / 'receipts').glob('round-*.mjs'))
                                # Only the new round explicitly requests read-only capture.
                                latest = max(scripts, key=lambda p: p.stat().st_mtime_ns)
                                self.assertIn('"capture_only": true', latest.read_text(encoding='utf-8'))
                                complete(root)
                                return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
                            browser.side_effect = capture
                            self.assertFalse(adapter.submit(*ARGS, capture_only=True)['delivered'])
                            self.assertFalse(owner.exists())
                            self.assertEqual(browser.call_count, 1)
                            adapter.submit(*ARGS)
                            self.assertEqual(browser.call_count, 1)
                    if phase in ('PREPARING', 'CAPTURED'):
                        browser.assert_not_called()


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--crash-worker':
        crash_worker(Path(sys.argv[2]), sys.argv[3])
    else:
        unittest.main()
