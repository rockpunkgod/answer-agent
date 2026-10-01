"""Short-lived, operator-pinned WeCom test-answer delivery using Windows-MCP.

This is not a permanent contact-ID resolver. The operator verifies a visible
WeCom target, its header/avatar regions and geometry before making a pin.
"""
from hashlib import sha256
from pathlib import Path
import re
import time
from uuid import uuid4

from .delivery import PreflightFailure, NotSubmitted
from .mcp_page_contract import snapshot_text
from .storage import now
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground


def pixel_hash(image, box):
    return sha256(image.crop(tuple(box)).convert('RGB').tobytes()).hexdigest()


def verify_frame(record, pin, *, foreground=None):
    from PIL import Image
    if foreground is None:
        text = snapshot_text(record)
        if 'Focused Window:' not in text or 'Opened Windows:' not in text:
            raise PreflightFailure('FOREGROUND_METADATA_MISSING')
        focused = text.split('Focused Window:', 1)[-1].split('Opened Windows:', 1)[0]
        if not re.search(r'^\s*企业微信\s+', focused, re.M):
            raise PreflightFailure('WECOM_NOT_FOREGROUND')
        line = next(line for line in focused.splitlines() if re.match(r'^\s*企业微信\s+', line))
        numbers = re.findall(r'\d+', line)
        geometry = list(map(int, numbers[-3:])) if len(numbers) >= 4 else []
    else:
        if record.get('tool') != 'Screenshot' or record.get('is_error') is not False or foreground.get('process') != 'WXWork':
            raise PreflightFailure('FOREGROUND_PROBE_FAILED')
        geometry = [foreground['width'],foreground['height'],foreground['handle']]
    if geometry != pin['window_geometry_handle']:
        raise PreflightFailure('WECOM_WINDOW_CHANGED')
    images = [Path(c['path']) for c in record.get('content', []) if c.get('type') == 'image']
    if len(images) != 1:
        raise PreflightFailure('NO_UNIQUE_FRAME')
    with Image.open(images[0]) as image:
        if list(image.size) != pin['image_size']:
            raise PreflightFailure('DISPLAY_GEOMETRY_CHANGED')
        for key in ('header', 'avatar'):
            if pixel_hash(image, pin[key+'_box']) != pin[key+'_hash']:
                raise PreflightFailure('TEST_CONTACT_VISUAL_PIN_CHANGED')
        editor = image.crop(tuple(pin['editor_box'])).convert('RGB')
        pixels = editor.load()
        blank = sum(min(pixels[x,y]) < 220 for x in range(editor.width) for y in range(editor.height)) < 20
    return {'path': str(images[0].resolve()), 'sha256': sha256(images[0].read_bytes()).hexdigest(),
            'editor_blank': blank}


class MCPTestAnswerDesktop:
    simulated = False
    test_only = True
    test_answer_transport = True

    def __init__(self, transport, pin, workspace):
        from .mcp_transport import MCPProcess
        if isinstance(transport, MCPProcess):
            transport.bound_input_process = 'WXWork'
        self.transport = transport
        self.pin = dict(pin)
        self.lock_path = str(Path(workspace).resolve() / 'data/windows-interactive-desktop.lock')
        self.last_frames = []

    def authorize(self, message):
        p = self.pin
        if (p.get('platform'), p.get('display_name')) != ('wecom', '苇中鹤'):
            raise ValueError('ONLY_VERIFIED_WECOM_TEST_CONTACT_ALLOWED')
        if time.time() > p.get('expires_at', 0):
            raise ValueError('TEST_PIN_EXPIRED')
        if (message.outbox_id, message.binding_id, message.group_key, message.student_key, message.body_hash) != (
                p.get('outbox_id'), p.get('binding_id'), 'wecom', p.get('target_key'), p.get('body_hash')):
            raise ValueError('TEST_ANSWER_PIN_MISMATCH')
        if any(c in message.body for c in '\r\n\t{}'):
            raise ValueError('ANSWER_NEEDS_VERIFIED_SINGLE_LINE_INPUT')

    def _call(self, tool, **arguments):
        result = self.transport.call(tool, arguments)
        if result.get('is_error') is True or result.get('error'):
            raise RuntimeError('MCP tool did not complete')
        return result

    def _frame(self, message):
        self.authorize(message)
        if self.pin.get('observation_mode') == 'fixed_foreground_probe':
            before_record = self._call('PowerShell', command=FOREGROUND_COMMAND, timeout=10)
            before = parse_foreground(before_record)
            record = self._call('Screenshot')
            after_record = self._call('PowerShell', command=FOREGROUND_COMMAND, timeout=10)
            after = parse_foreground(after_record)
            if before != after:
                raise PreflightFailure('FOREGROUND_CHANGED_DURING_CAPTURE')
            frame = verify_frame(record, self.pin, foreground=after)
            frame.update(foreground_before=before_record,foreground_after=after_record)
        else:
            record = self._call('Snapshot', use_dom=False, use_vision=True)
            frame = verify_frame(record, self.pin)
        self.last_record = record
        self.last_frames.append(frame)
        return frame

    def preflight(self, message):
        try:
            frame = self._frame(message)
            if not frame['editor_blank']:
                raise PreflightFailure('COMPOSE_AREA_NOT_EMPTY')
        except ValueError as exc:
            raise PreflightFailure(str(exc)) from exc

    def _clipboard_text(self):
        result = self._call('Clipboard', mode='get')
        values = [c['text'] for c in result.get('content', []) if c.get('type') == 'text']
        if len(values) != 1 or not values[0].startswith('Clipboard content:\n'):
            raise ValueError('CLIPBOARD_READ_UNCONFIRMED')
        return values[0].removeprefix('Clipboard content:\n')

    def send(self, message):
        try:
            self.preflight(message)
            self._call('Type', loc=self.pin['compose_point'], text=message.body, press_enter=False)
            self._frame(message)
            self._call('Click', loc=self.pin['compose_point'])
            self._call('Clipboard', mode='set', text='draft-readback-'+uuid4().hex)
            self._call('Shortcut', shortcut='ctrl+a')
            self._call('Shortcut', shortcut='ctrl+c')
            if self._clipboard_text() != message.body:
                raise NotSubmitted('DRAFT_VERIFICATION_FAILED')
            self._call('Shortcut', shortcut='right')
            self._frame(message)
        except NotSubmitted:
            raise
        except Exception as exc:
            raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED') from exc
        # Workflow has already committed SENDING. No exception below can be
        # reported as a definitely unsubmitted draft, even if the tool times out.
        self._call('Click', loc=self.pin['send_point'])
        return self.reconcile(message)

    def reconcile(self, message):
        """Copy an observed bubble for exact readback; never submit or re-type."""
        try:
            frame = self._frame(message)
            if not frame['editor_blank']:
                raise ValueError('EDITOR_NOT_EMPTY_AFTER_SEND')
            self._call('Clipboard', mode='set', text='receipt-readback-'+uuid4().hex)
            self._call('Click', loc=self.pin['receipt_point'], button='right')
            visual = None
            if self.pin.get('observation_mode') == 'fixed_foreground_probe' and self.pin.get('copy_menu_verified'):
                self._frame(message)
                visual = self.last_record
                choices = []
            else:
                menu = self._call('Snapshot', use_dom=False, use_vision=False)
                text = snapshot_text(menu)
                choices = re.findall(r'\((\d+),(\d+)\) (?:菜单项目|菜单项|MenuItem) "复制(?:\([^\n"]*\))?"', text)
            if len(choices) == 1:
                copy_point = list(map(int, choices[0]))
            else:
                # Some WeCom versions expose no accessibility menu nodes.
                # Require an operator-reviewed, exact first-row visual pin;
                # a coordinate alone cannot authorize a menu operation.
                if choices or not self.pin.get('copy_menu_verified'):
                    self._call('Shortcut', shortcut='escape')
                    raise ValueError('NO_UNIQUE_COPY_MENU')
                if visual is None:
                    visual = self._call('Snapshot', use_dom=False, use_vision=True, use_annotation=False)
                    verify_frame(visual, self.pin)
                from PIL import Image
                image_path = next(c['path'] for c in visual['content'] if c.get('type') == 'image')
                with Image.open(image_path) as image:
                    if pixel_hash(image, self.pin['copy_menu_box']) != self.pin['copy_menu_hash']:
                        self._call('Shortcut', shortcut='escape')
                        raise ValueError('COPY_MENU_VISUAL_PIN_CHANGED')
                copy_point = self.pin['copy_menu_point']
            self._call('Click', loc=copy_point)
            copied = self._clipboard_text()
            after = self._frame(message)
            if copied != message.body or not after['editor_blank']:
                raise ValueError('SENT_BUBBLE_TEXT_MISMATCH')
            return {'confirmed': True, 'simulated': False, 'body_hash': message.body_hash,
                    'confirmed_at': now(), 'target_platform': 'wecom', 'target_name': '苇中鹤',
                    'target_key': self.pin['target_key'], 'source_student_delivered': False,
                    'verification': 'pinned current-session header/avatar + exact copied bubble + empty composer',
                    'server_receipt': False, 'recipient_read': False, 'frames': self.last_frames}
        except Exception:
            return {'confirmed': False, 'simulated': False, 'reason': 'TEST_UI_RESULT_UNCONFIRMED',
                    'source_student_delivered': False, 'frames': self.last_frames}
