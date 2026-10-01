import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from helpdesk.mcp_transport import MCPCallError, MCPProcess, MCPTimeout, MCPTransportError


FAKE = r'''import json, sys, time
mode, counter = sys.argv[1], sys.argv[2]
print("starting fake service", flush=True)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    if line.strip() == "quit": break
    req = json.loads(line)
    with open(counter, "a", encoding="utf-8") as f: f.write(req["tool"] + "\n")
    if mode == "delay": time.sleep(.25)
    if mode == "fail":
        print(json.dumps({"tool":req["tool"], "error":"ValueError", "detail":"bad args", "automatic_retry_allowed":False}), flush=True)
    elif mode == "iserror":
        print(json.dumps({"tool":req["tool"], "content":[], "is_error":True}), flush=True)
    elif mode == "mismatch":
        print(json.dumps({"tool":"OtherTool", "content":[], "is_error":False}), flush=True)
        break
    elif mode == "guarded":
        if req.get("expected_foreground_process") != "WXWork":
            print(json.dumps({"tool":req["tool"], "error":"ValueError", "detail":"missing foreground guard"}), flush=True)
        else:
            print(json.dumps({"tool":req["tool"], "content":[], "is_error":False}), flush=True)
    else:
        print(json.dumps({"tool":req["tool"], "content":[{"type":"text","text":req["tool"]}], "is_error":False}), flush=True)
'''


class MCPTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake = self.root / "fake.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.counter = self.root / "calls.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def make_process(self, mode="ok"):
        def factory(_command, **kwargs):
            return subprocess.Popen([sys.executable, str(self.fake), mode, str(self.counter)], **kwargs)
        return MCPProcess(sys.executable, root=self.root, timeout=1, stderr_path=self.root / "stderr.log",
                          process_factory=factory)

    def test_ready_ignores_plain_stdout_log_and_calls(self):
        with self.make_process() as mcp:
            result = mcp.call("Snapshot", {"x": 1})
            self.assertEqual(result["content"][0]["text"], "Snapshot")
            self.assertEqual(mcp.ignored_stdout_lines, 1)
            self.assertEqual(mcp.pid is not None, True)
        self.assertEqual(self.counter.read_text(encoding="utf-8").splitlines(), ["Snapshot"])

    def test_input_foreground_guard_is_preserved_in_native_request(self):
        with self.make_process('guarded') as mcp:
            mcp.call('Click', {'loc': [50, 70]}, expected_foreground_process='WXWork')
            mcp.call('Shortcut', {'shortcut': 'ctrl+v'}, expected_foreground_process='WXWork')
        self.assertEqual(self.counter.read_text(encoding='utf-8').splitlines(), ['Click', 'Shortcut'])

    def test_error_returned_without_retry(self):
        with self.make_process("fail") as mcp:
            with self.assertRaisesRegex(MCPCallError, '^MCP_TOOL_REPORTED_ERROR$') as caught:
                mcp.call("Click", {"x": 1})
            self.assertNotIn('bad args',str(caught.exception))
        self.assertEqual(self.counter.read_text(encoding="utf-8").splitlines(), ["Click"])

    def test_is_error_and_mismatched_tool_are_rejected(self):
        with self.make_process("iserror") as mcp:
            with self.assertRaises(MCPCallError):
                mcp.call("Snapshot", {})
            self.assertFalse(mcp.uncertain)
        with self.make_process("mismatch") as mcp:
            with self.assertRaises(MCPTransportError):
                mcp.call("Snapshot", {})
            self.assertTrue(mcp.uncertain)
            mcp._proc.wait(timeout=1)  # Fake exits itself; adapter never kills it.

    def test_timeout_blocks_calls_then_poll_collects_same_response(self):
        with self.make_process("delay") as mcp:
            with self.assertRaises(MCPTimeout):
                mcp.call("Type", {"text": "one"}, timeout=.03)
            self.assertTrue(mcp.uncertain)
            with self.assertRaises(MCPTransportError):
                mcp.call("Click", {})
            result = mcp.poll_pending(timeout=1)
            self.assertEqual(result["content"][0]["text"], "Type")
            self.assertFalse(mcp.uncertain)
        self.assertEqual(self.counter.read_text(encoding="utf-8").splitlines(), ["Type"])


if __name__ == "__main__":
    unittest.main()
