"""Small synchronous, no-retry bridge to ``tools/windows_mcp_session.py``."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any, Callable, TextIO


ROOT = Path(__file__).resolve().parents[1]
_EOF = object()
_MAX_LINE = 1024 * 1024
GUARDED_INPUTS = frozenset({'Click', 'Type', 'Shortcut', 'Scroll', 'Move'})
_INPUT_PROCESSES = frozenset({'WXWork', 'msedge'})
RUNTIME_HOME_ENV = 'HELPDESK_WINDOWS_MCP_HOME'
TREE_LIMIT_ENV = 'HELPDESK_WINDOWS_MCP_MAX_TREE_ELEMENTS'


def tree_capture_limit(*, environ=None):
    """Bound the installed MCP's tree budget without changing upstream files."""
    raw = (os.environ if environ is None else environ).get(TREE_LIMIT_ENV, '4000')
    if not isinstance(raw, str) or len(raw) > 5 or not raw.isascii() or not raw.isdecimal():
        raise MCPTransportError('MCP_TREE_LIMIT_INVALID')
    value = int(raw)
    if not 500 <= value <= 10000:
        raise MCPTransportError('MCP_TREE_LIMIT_INVALID')
    return value


def runtime_home(root=ROOT, *, environ=None):
    """One locally configured installation; no downloads, shell or GUI actions."""
    value = (os.environ if environ is None else environ).get(RUNTIME_HOME_ENV)
    if value is None:
        return Path(root).resolve() / '.venv-windows-mcp'
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 2048
            or any(ord(char) < 32 for char in value) or value.startswith(('\\\\','//'))
            or not Path(value).is_absolute()):
        raise MCPTransportError('MCP_RUNTIME_HOME_INVALID')
    return Path(value).resolve()


class MCPTransportError(RuntimeError):
    pass


class MCPCallError(MCPTransportError):
    """The session returned an error for a request (which was not replayed)."""


class MCPTimeout(MCPTransportError):
    """Request outcome is unknown; poll_pending may collect its eventual reply."""


class MCPProcess:
    """Own one persistent stdio MCP session. Calls are serialized and never retried."""

    def __init__(self, python: str | os.PathLike[str] | None = None, *, root: str | os.PathLike[str] = ROOT,
                 timeout: float = 60.0, stderr_path: str | os.PathLike[str] | None = None,
                 process_factory: Callable[..., Any] = subprocess.Popen,
                 response_queue: queue.Queue | None = None,
                 bound_input_process: str | None = None):
        self.root = Path(root)
        self.runtime_home = runtime_home(self.root)
        self.python = str(python) if python is not None else str(self.runtime_home / 'Scripts/python.exe')
        self.timeout = timeout
        self.stderr_path = Path(stderr_path) if stderr_path else self.root / "data/private/windows-mcp/adapter-stderr.log"
        self._factory = process_factory
        self._responses = response_queue or queue.Queue()
        self._proc = None
        self._stderr: TextIO | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending = False
        self._pending_tool: str | None = None
        self._closed = False
        self.pid: int | None = None
        self.ignored_stdout_lines = 0
        self.last_response = None
        self.bound_input_process = bound_input_process

    @property
    def bound_input_process(self) -> str | None:
        return self._bound_input_process

    @bound_input_process.setter
    def bound_input_process(self, value: str | None) -> None:
        if value is not None and (not isinstance(value, str) or value not in _INPUT_PROCESSES):
            raise MCPTransportError('MCP_INPUT_PROCESS_INVALID')
        self._bound_input_process = value

    @property
    def uncertain(self) -> bool:
        return self._pending

    def __enter__(self) -> "MCPProcess":
        if self._proc is not None:
            raise MCPTransportError("MCPProcess can only be entered once")
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = self.stderr_path.open("a", encoding="utf-8")
        kwargs: dict[str, Any] = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self._stderr, cwd=str(self.root), text=True,
                                      encoding="utf-8", errors="replace", bufsize=1,
                                      env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                                           RUNTIME_HOME_ENV: str(self.runtime_home)})
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._proc = self._factory([self.python, "-u", str(self.root / "tools/windows_mcp_session.py")], **kwargs)
            self.pid = getattr(self._proc, "pid", None)
            self._reader = threading.Thread(target=self._read_stdout, name="mcp-stdout", daemon=True)
            self._reader.start()
            ready = self._next(self.timeout)
            if not isinstance(ready, dict) or ready.get("ready") is not True:
                raise MCPTransportError('MCP_SESSION_NOT_READY')
            return self
        except BaseException:
            # Startup can fail after the child exists (for example, ready timed
            # out). Ask it to stop reading, but never terminate a process here.
            if self._proc is not None:
                self._send_quit()
            self._close_stderr()
            raise

    def _read_stdout(self) -> None:
        stream = self._proc.stdout
        try:
            while True:
                line = stream.readline(_MAX_LINE + 1)
                if not line:
                    break
                if len(line) > _MAX_LINE:
                    while line and not line.endswith("\n"):
                        line = stream.readline(_MAX_LINE + 1)
                    self.ignored_stdout_lines = min(self.ignored_stdout_lines + 1, 1_000_000)
                    continue
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    self.ignored_stdout_lines = min(self.ignored_stdout_lines + 1, 1_000_000)
                    continue
                if isinstance(msg, dict) and any(k in msg for k in ("ready", "content", "tools", "error", "is_error")):
                    self._responses.put(msg)
                else:
                    self.ignored_stdout_lines = min(self.ignored_stdout_lines + 1, 1_000_000)
        finally:
            self._responses.put(_EOF)
            try:
                stream.close()
            except OSError:
                pass

    def _next(self, timeout: float) -> dict[str, Any]:
        try:
            item = self._responses.get(timeout=max(0, timeout))
        except queue.Empty as exc:
            raise MCPTimeout("Timed out waiting for MCP response; outcome may be unknown") from exc
        if item is _EOF:
            raise MCPTransportError("MCP stdout closed before a response arrived")
        return item

    def call(self, tool: str, args: dict[str, Any], *, timeout: float | None = None,
             expected_foreground_process: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure_available()
            if expected_foreground_process is not None and (not isinstance(expected_foreground_process, str)
                                                          or expected_foreground_process not in _INPUT_PROCESSES):
                raise MCPTransportError('MCP_INPUT_PROCESS_INVALID')
            self._pending = True
            self._pending_tool = tool
            # Even a write/flush exception can follow a partial transmission;
            # preserve uncertainty and never let call() replay it implicitly.
            request = {"tool": tool, "arguments": args}
            if expected_foreground_process is None and tool in GUARDED_INPUTS:
                expected_foreground_process = self.bound_input_process
            if expected_foreground_process is not None:
                request["expected_foreground_process"] = expected_foreground_process
            self._write(request)
            return self._receive_pending(self.timeout if timeout is None else timeout)

    def poll_pending(self, timeout: float | None = None) -> dict[str, Any]:
        """Read the single timed-out call's eventual response; never sends input."""
        with self._lock:
            if not self._pending:
                raise MCPTransportError("There is no pending uncertain response")
            return self._receive_pending(self.timeout if timeout is None else timeout)

    def _receive_pending(self, timeout: float) -> dict[str, Any]:
        response = self._next(timeout)
        self.last_response = response
        expected = self._pending_tool
        if response.get("tool") != expected:
            raise MCPTransportError('MCP_RESPONSE_TOOL_MISMATCH')
        self._pending = False
        self._pending_tool = None
        if response.get("error") or response.get("is_error") is True:
            raise MCPCallError('MCP_TOOL_REPORTED_ERROR')
        return response

    def _ensure_available(self) -> None:
        if self._proc is None or self._closed:
            raise MCPTransportError("MCPProcess is not active")
        if self._pending:
            raise MCPTransportError("Previous request outcome is uncertain; use poll_pending first")

    def _write(self, obj: dict[str, Any]) -> None:
        try:
            self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise MCPTransportError("Could not write MCP request") from exc

    def __exit__(self, *_: Any) -> None:
        if self._proc is None or self._closed:
            self._close_stderr()
            return
        self._closed = True
        # If a call timed out, quit is queued after it. Keep the child alive so an
        # in-flight desktop action is never killed or silently declared cancelled.
        self._send_quit()
        if not self._pending:
            try:
                self._proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, AttributeError):
                pass
        self._close_stderr()

    def _send_quit(self) -> None:
        try:
            self._proc.stdin.write("quit\n")
            self._proc.stdin.flush()
        except (OSError, AttributeError):
            pass
        try:
            self._proc.stdin.close()
        except (OSError, AttributeError):
            pass

    def _close_stderr(self) -> None:
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
