"""Interactive stdio client for the installed official Windows-MCP server.

Run with .venv-windows-mcp/Scripts/python.exe -u. Each input line is one
JSON request {tool, arguments}; 'list' lists schemas. No implicit UI actions.
Screenshots and server logs stay in the private local evidence directory.
"""
import asyncio
import base64
from hashlib import sha256
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from helpdesk.mcp_window_probe import (FOREGROUND_COMMAND, parse_foreground_process,
                                       screen2_caption_command, parse_screen2_caption,
                                       WECOM_SCREEN2_CAPTION_COMMAND, WECOM_SCREEN2_WINDOW_COMMAND,
                                       parse_wecom_window_location, verify_wecom_switch_snapshot)
from helpdesk.windows_worker_probe import DESKTOP_STATUS_COMMAND, parse_desktop_status

ALLOWED = {"Screenshot", "Snapshot", "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "DisplayInventory", "App", "Clipboard", "PowerShell"}
SCREEN2_ACTIVATE = 'ActivateEdgeOnScreen2'  # Local entry; native operation is Click.
SCREEN2_ACTIVATIONS = {SCREEN2_ACTIVATE: 'msedge', 'ActivateWeComOnScreen2': 'WXWork'}
SCREEN2_APP_ACTIVATION = 'ActivateWeComOnScreen2ByApp'


GUARDED_INPUTS = {"Click", "Type", "Shortcut", "Scroll", "Move"}
POINTER_INPUTS = {"Click", "Scroll", "Move", "Type"}  # Type first clicks its location.


def fixed_readonly_probe(arguments):
    """Only reviewed literal probes; no arguments can become executable code."""
    return (isinstance(arguments, dict) and set(arguments) == {'command', 'timeout'}
            and type(arguments['timeout']) is int and arguments['timeout'] == 10
            and arguments['command'] in (FOREGROUND_COMMAND, DESKTOP_STATUS_COMMAND,
                                          WECOM_SCREEN2_CAPTION_COMMAND, WECOM_SCREEN2_WINDOW_COMMAND))


def audit_arguments(tool, arguments):
    """Intent journals contain integrity metadata, never raw input contents."""
    if tool == 'Clipboard' and arguments == {'mode': 'get'}:
        return {'mode': 'get'}  # Existing original-message importer checks this.
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    value = {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
    if tool == 'PowerShell' and fixed_readonly_probe(arguments):
        value['probe'] = {FOREGROUND_COMMAND: 'FOREGROUND', DESKTOP_STATUS_COMMAND: 'DESKTOP_STATUS',
                         WECOM_SCREEN2_CAPTION_COMMAND: 'WECOM_SCREEN2_CAPTION',
                         WECOM_SCREEN2_WINDOW_COMMAND: 'WECOM_SCREEN2_WINDOW'}[arguments['command']]
    return value


def error_code(exc):
    # Native/library error messages can contain input or authentication data.
    if isinstance(exc, ValueError) and str(exc) in (
            'SCREEN2_ACTIVATION_REJECTED', 'SCREEN2_ACTIVATION_UNCONFIRMED',
            'INVALID_SHORTCUT_ARGUMENTS'):
        return str(exc)
    return 'FOREGROUND_GUARD_REJECTED' if isinstance(exc, ValueError) and str(exc) == 'FOREGROUND_GUARD_REJECTED' else 'REQUEST_OR_NATIVE_RESULT_UNCONFIRMED'


def expected_foreground(request):
    if 'expected_foreground_process' not in request:
        if request.get('tool') in GUARDED_INPUTS:
            raise ValueError('EXPECTED_BUSINESS_PROCESS_REQUIRED')
        return None
    expected = request['expected_foreground_process']
    if (type(expected) is not str or not expected.strip() or expected != expected.strip()
            or any(c in expected for c in ('\r', '\n', '\x00'))):
        raise ValueError('expected_foreground_process must be a nonempty process name')
    if expected.casefold() not in {'wxwork', 'msedge'}:
        raise ValueError('BUSINESS_PROCESS_OUTSIDE_SCOPE')
    if request.get('tool') not in GUARDED_INPUTS:
        raise ValueError('Foreground guard is only supported for input tools')
    return expected


async def check_foreground(client, expected, attempt, attempt_path, *, pointer_loc=None):
    """Fixed official probe, recorded before input; no retry on unknown outcome."""
    preflight = {'expected_process': expected, 'status': 'PROBE_UNCONFIRMED'}
    attempt['foreground_preflight'] = preflight
    def save():
        attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    try:
        result = await client.call_tool('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10},
                                        timeout=15, raise_on_error=False)
        record = {'tool': 'PowerShell', 'is_error': result.is_error,
                  'content': [{'type': item.type, **({'text': item.text} if item.type == 'text' else {})}
                              for item in result.content]}
        preflight['probe_record'] = record
        observed = parse_foreground_process(record)
        preflight['observed_foreground'] = observed
        if observed['process'].casefold() != expected.casefold():
            raise ValueError('FOREGROUND_PROCESS_MISMATCH')
        if pointer_loc is not None:
            x, y = pointer_loc
            inside = (observed['left'] <= x < observed['left'] + observed['width']
                      and observed['top'] <= y < observed['top'] + observed['height'])
            preflight['point_in_current_window'] = inside
            if not inside:
                raise ValueError('POINTER_OUTSIDE_CURRENT_WECOM_WINDOW')
    except Exception as exc:
        preflight.update(status='INPUT_BLOCKED', error_type=type(exc).__name__)
        attempt['status'] = 'INPUT_BLOCKED_BY_FOREGROUND_GUARD'
        save()
        raise ValueError('FOREGROUND_GUARD_REJECTED') from exc
    preflight['status'] = 'PROCESS_MATCHED'
    save()


async def check_screen2_activation(client, loc, attempt, attempt_path, *, target_process='msedge', by_app=False):
    preflight = {'status': 'PROBE_UNCONFIRMED'}
    attempt['screen2_activation_preflight'] = preflight
    def save():
        attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    try:
        desktop = await client.call_tool('PowerShell', {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10},
                                        timeout=15, raise_on_error=False)
        if (desktop.is_error or len(desktop.content) != 1
                or desktop.content[0].type != 'text'):
            raise ValueError('DESKTOP_UNCONFIRMED')
        status = parse_desktop_status(desktop.content[0].text, observed_after=attempt['started_at'])
        if not status.unlocked or status.remote is not False:
            raise ValueError('DESKTOP_NOT_AVAILABLE')
        preflight['desktop_available'] = True
        # App uses the cached exact window, so inspect only display 2 first.
        # It does not click a title bar; a covered caption is irrelevant here.
        if by_app:
            snapshot = await client.call_tool('Snapshot', {'use_vision': False, 'use_dom': False,
                'use_annotation': False, 'use_ui_tree': True, 'display': [1]}, timeout=45, raise_on_error=False)
            snapshot_record = {'tool': 'Snapshot', 'is_error': snapshot.is_error,
                'content': [{'type': item.type, **({'text': item.text} if item.type == 'text' else {})}
                            for item in snapshot.content]}
            preflight['snapshot_sha256'] = sha256(json.dumps(snapshot_record, ensure_ascii=False,
                sort_keys=True).encode('utf-8')).hexdigest()
        command = WECOM_SCREEN2_WINDOW_COMMAND if by_app else screen2_caption_command(loc, target_process=target_process)
        result = await client.call_tool('PowerShell', {'command': command, 'timeout': 10},
                                       timeout=15, raise_on_error=False)
        record = {'tool': 'PowerShell', 'is_error': result.is_error,
                  'content': [{'type': item.type, **({'text': item.text} if item.type == 'text' else {})}
                              for item in result.content]}
        preflight['probe_record'] = record
        target = parse_wecom_window_location(record) if by_app else parse_screen2_caption(record, loc, target_process=target_process)
        if by_app:
            verify_wecom_switch_snapshot(snapshot_record, target)
        preflight.update(status='SCREEN2_WECOM_WINDOW_VERIFIED' if by_app else 'SCREEN2_EDGE_CAPTION_VERIFIED' if target_process == 'msedge'
                         else 'SCREEN2_WECOM_CAPTION_VERIFIED', target=target)
        save()
        return target
    except Exception as exc:
        preflight.update(status='INPUT_BLOCKED', error_type=type(exc).__name__)
        attempt['status'] = 'INPUT_BLOCKED_BY_SCREEN2_GUARD'
        save()
        raise ValueError('SCREEN2_ACTIVATION_REJECTED') from exc


async def main():
    private = ROOT / "data/private/windows-mcp"
    private.mkdir(parents=True, exist_ok=True)
    transport = StdioTransport(
        command=str(ROOT / ".venv-windows-mcp/Scripts/windows-mcp.exe"),
        args=["serve", "--transport", "stdio", "--tools", ",".join(sorted(ALLOWED))],
        cwd=str(ROOT), env={"ANONYMIZED_TELEMETRY": "false", "POSTHOG_API_KEY": "",
                            "WINDOWS_MCP_SCREENSHOT_BACKEND": "pillow"},
        log_file=private / "server.log")
    async with Client(transport) as client:
        print(json.dumps({"ready": True, "transport": "stdio", "server": "Windows-MCP"}), flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line or line.strip() == "quit":
                break
            attempt_path = None
            attempt = None
            name = None
            try:
                request = json.loads(line)
                name = request["tool"]
                if name == 'Shortcut':
                    shortcut_args = request.get('arguments')
                    if (not isinstance(shortcut_args, dict) or set(shortcut_args) != {'shortcut'}
                            or not isinstance(shortcut_args['shortcut'], str)
                            or not shortcut_args['shortcut'].strip()
                            or len(shortcut_args['shortcut']) > 128
                            or any(c in shortcut_args['shortcut'] for c in ('\r', '\n', '\x00'))):
                        raise ValueError('INVALID_SHORTCUT_ARGUMENTS')
                expected = expected_foreground(request)
                pointer_loc = None
                if name in POINTER_INPUTS and expected is not None and expected.casefold() == 'wxwork':
                    pointer_loc = request.get('arguments', {}).get('loc')
                    if (not isinstance(pointer_loc, list) or len(pointer_loc) != 2
                            or any(type(value) is not int for value in pointer_loc)):
                        raise ValueError('EXPLICIT_WECOM_POINTER_LOCATION_REQUIRED')
                activation_loc = None
                by_app = name == SCREEN2_APP_ACTIVATION
                if by_app and (set(request) != {'tool', 'arguments'} or request['arguments'] != {}):
                    raise ValueError('INVALID_SCREEN2_ACTIVATION')
                if name in SCREEN2_ACTIVATIONS:
                    if (set(request) != {'tool', 'arguments'} or not isinstance(request['arguments'], dict)
                            or set(request['arguments']) != {'loc'}):
                        raise ValueError('INVALID_SCREEN2_ACTIVATION')
                    activation_loc = request['arguments']['loc']
                    screen2_caption_command(activation_loc, target_process=SCREEN2_ACTIVATIONS[name])
                if name == "list":
                    tools = await client.list_tools()
                    print(json.dumps({"tools": [{"name": t.name, "schema": t.inputSchema} for t in tools]}), flush=True)
                    continue
                if name not in ALLOWED and name not in SCREEN2_ACTIVATIONS and not by_app:
                    raise ValueError("Tool outside explicit desktop allowlist")
                if name == "PowerShell" and not fixed_readonly_probe(request.get("arguments", {})):
                    raise ValueError("Only reviewed fixed read-only probes are allowed")
                if name == "Type" and any(c in request.get("arguments", {}).get("text", "") for c in ("\r", "\n")):
                    raise ValueError("Multiline Type is unsafe in chat: newlines can submit partial messages. Stage a single line and verify before Enter.")
                # Persist intent before GUI input: a timeout or process crash must
                # never be mistaken for evidence that the action did not happen.
                attempt_id = uuid4().hex
                attempt_path = private / f"attempt-{attempt_id}.json"
                attempt = {"attempt_id": attempt_id, "tool": name,
                           "arguments": audit_arguments(name, request.get("arguments", {})),
                           "arguments_storage": "INTEGRITY_METADATA_ONLY",
                           "started_at": datetime.now(timezone.utc).isoformat(),
                           "status": "OUTCOME_UNCONFIRMED", "automatic_retry_allowed": False}
                attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding="utf-8")
                if expected is not None:
                    # Serial requests: this probe is the final MCP call before input.
                    await check_foreground(client, expected, attempt, attempt_path, pointer_loc=pointer_loc)
                target = None
                if activation_loc is not None or by_app:
                    target = await check_screen2_activation(client, activation_loc, attempt, attempt_path,
                                                           target_process='WXWork' if by_app else SCREEN2_ACTIVATIONS[name], by_app=by_app)
                native_name = 'App' if by_app else 'Click' if activation_loc is not None else name
                native_arguments = ({'mode': 'switch', 'name': '企业微信'} if by_app else
                    {'loc': activation_loc, 'button': 'left', 'clicks': 1} if activation_loc is not None else request.get('arguments', {}))
                result = await client.call_tool(native_name, native_arguments, timeout=45, raise_on_error=False)
                if target is not None and not result.is_error:
                    attempt['native_app_returned' if by_app else 'native_click_returned'] = True
                    postflight = {'status': 'PROBE_UNCONFIRMED'}
                    attempt['screen2_activation_postflight'] = postflight
                    attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding='utf-8')
                    try:
                        after = await client.call_tool('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10},
                                                       timeout=15, raise_on_error=False)
                        postflight['probe_record'] = {'tool': 'PowerShell', 'is_error': after.is_error,
                            'content': [{'type': item.type, **({'text': item.text} if item.type == 'text' else {})}
                                        for item in after.content]}
                        observed = parse_foreground_process(postflight['probe_record'])
                        postflight['observed'] = observed
                        if observed['process'] != target['process'] or observed['handle'] != target['handle']:
                            raise ValueError('ACTIVATION_TARGET_CHANGED')
                        if by_app and (observed['left'] != target['window_left'] or observed['top'] != target['window_top']
                                or observed['width'] != target['window_right'] - target['window_left']
                                or observed['height'] != target['window_bottom'] - target['window_top']):
                            raise ValueError('ACTIVATION_WINDOW_MOVED')
                        postflight['status'] = 'TARGET_FOREGROUND_VERIFIED'
                    except Exception as exc:
                        attempt['status'] = 'ACTIVATION_RESULT_UNCONFIRMED'
                        postflight.update(status='UNCONFIRMED', error_type=type(exc).__name__)
                        attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding='utf-8')
                        raise ValueError('SCREEN2_ACTIVATION_UNCONFIRMED') from exc
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                output = []
                # Input acknowledgements may echo the supplied text. They are
                # not source observations and cannot serve as business proof.
                input_result = name in GUARDED_INPUTS or name in SCREEN2_ACTIVATIONS or by_app or name == 'Clipboard' and request.get('arguments', {}).get('mode') != 'get'
                for i, item in enumerate(result.content if not result.is_error and not input_result else []):
                    if item.type == "image":
                        ext = ".png" if item.mimeType == "image/png" else ".jpg"
                        path = private / f"{stamp}-{i}{ext}"
                        path.write_bytes(base64.b64decode(item.data))
                        output.append({"type": "image", "path": str(path)})
                    elif item.type == "text":
                        output.append({"type": "text", "text": item.text})
                record = {"attempt_id": attempt_id, "tool": name, "is_error": result.is_error, "content": output}
                result_path = private / f"{stamp}.json"
                result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                # A tool return is not a business delivery receipt.
                attempt.update(status="TOOL_ERROR_UNCONFIRMED" if result.is_error else "TOOL_RETURNED",
                               result_path=str(result_path))
                attempt_path.write_text(json.dumps(attempt, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(record), flush=True)
            except Exception as exc:
                print(json.dumps({"tool": name if isinstance(name, str) and (name in ALLOWED or name == 'list' or name in SCREEN2_ACTIVATIONS or name == SCREEN2_APP_ACTIVATION) else None, "error": type(exc).__name__, "detail": error_code(exc),
                                  "attempt_path": str(attempt_path) if attempt_path else None,
                                  "automatic_retry_allowed": False}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
