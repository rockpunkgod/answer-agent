"""Native test-copy delivery over the existing Outbox, never a production source.

Requires a separately verified live account/chat guard. A visual pin alone is
not an account ID or room ID. No chat search, activation, collector promotion,
new database, direct answer-file sending or automatic UNKNOWN retry is exposed.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .delivery import PreflightFailure, NotSubmitted
from .mcp_test_delivery import MCPTestAnswerDesktop, pixel_hash, verify_frame
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground_process
from .personal_wechat_test_transport import NativePersonalWeChatTestTransport
from .storage import now
from .test_answer_queue import validate_test_copy, test_copy_reconcile_bound
from .test_routing import PERSONAL_TEST_PLATFORM, PersonalWeChatTestRecipient, test_recipient_from_mapping


class PersonalWeChatTestDesktop(MCPTestAnswerDesktop):
    receipt_platform = PERSONAL_TEST_PLATFORM
    receipt_name = '测试'

    def __init__(self, store, pin, workspace, *, account_chat_validator, native_factory=None):
        NativePersonalWeChatTestTransport.validate_pin(pin)
        if not callable(account_chat_validator):
            raise ValueError('LIVE_PERSONAL_ACCOUNT_AND_CHAT_VALIDATOR_REQUIRED')
        if (pin.get('observation_mode') != 'fixed_foreground_probe'
                or pin.get('copy_menu_verified') is not True):
            raise ValueError('REVIEWED_PERSONAL_TEST_CONTROLS_REQUIRED')
        self.store = store
        self.account_chat_validator = account_chat_validator
        self._active_message = None
        self._reconciling = False
        self._baseline = None
        self.workspace = Path(workspace).resolve()
        self.journal_root = self.workspace / 'data/private/personal-wechat-test-delivery'
        self._validate_geometry(pin)
        native = (native_factory or NativePersonalWeChatTestTransport)(
            deepcopy(pin), self.workspace, guard=self._native_guard)
        super().__init__(native, deepcopy(pin), self.workspace)

    @staticmethod
    def _validate_geometry(pin):
        size, bounds, geometry = (pin.get(k) for k in ('image_size', 'display_bounds', 'window_geometry_handle'))
        if (not isinstance(size, list) or len(size) != 2 or any(type(x) is not int or x <= 0 for x in size)
                or not isinstance(bounds, list) or len(bounds) != 4 or any(type(x) is not int for x in bounds)
                or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]
                or not isinstance(geometry, list) or len(geometry) != 3
                or any(type(x) is not int or x <= 0 for x in geometry)
                or type(pin.get('display_index')) is not int or pin['display_index'] < 0):
            raise ValueError('PERSONAL_TEST_GEOMETRY_REQUIRED')
        for field in ('header_box', 'avatar_box', 'editor_box', 'send_button_box', 'copy_menu_box', 'outgoing_viewport'):
            box = pin.get(field)
            if (not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box)
                    or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]):
                raise ValueError('PERSONAL_TEST_CONTROL_REGION_REQUIRED')
        for name in ('header_hash', 'avatar_hash', 'send_button_hash', 'copy_menu_hash'):
            digest = pin.get(name)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('PERSONAL_TEST_CONTROL_HASH_REQUIRED')
        points = []
        for field in ('compose_point', 'send_point', 'receipt_point', 'copy_menu_point'):
            point = pin.get(field)
            if (not isinstance(point, list) or len(point) != 2 or any(type(x) is not int for x in point)
                    or not bounds[0] <= point[0] < bounds[2] or not bounds[1] <= point[1] < bounds[3]):
                raise ValueError('PERSONAL_TEST_CONTROL_POINT_REQUIRED')
            points.append(tuple(point))
            region = pin[{'compose_point': 'editor_box', 'send_point': 'send_button_box',
                          'receipt_point': 'outgoing_viewport', 'copy_menu_point': 'copy_menu_box'}[field]]
            x = (point[0]-bounds[0])*size[0]/(bounds[2]-bounds[0])
            y = (point[1]-bounds[1])*size[1]/(bounds[3]-bounds[1])
            if not region[0] <= x < region[2] or not region[1] <= y < region[3]:
                raise ValueError('PERSONAL_TEST_POINT_OUTSIDE_CONTROL')
        if len(set(points)) != len(points):
            raise ValueError('PERSONAL_TEST_CONTROLS_MUST_BE_DISTINCT')
        view, color = pin['outgoing_viewport'], pin.get('outgoing_color')
        if (type(pin.get('outgoing_right')) is not int or not view[0] < pin['outgoing_right'] <= view[2]
                or not isinstance(color, list) or len(color) != 3
                or any(type(x) is not int or not 0 <= x <= 255 for x in color)):
            raise ValueError('PERSONAL_TEST_OUTGOING_RECIPE_REQUIRED')

    def authorize(self, message):
        NativePersonalWeChatTestTransport.validate_pin(self.pin)
        if (message.outbox_id != self.pin.get('outbox_id') or message.binding_id != self.pin.get('binding_id')
                or message.body_hash != self.pin.get('body_hash') or not message.body.strip()
                or len(message.body) > 30000):
            raise PreflightFailure('PERSONAL_TEST_OUTBOX_PIN_MISMATCH')
        row = self.store.one('SELECT * FROM outbox WHERE id=?', (message.outbox_id,))
        if not row or row['purpose'] != 'TEST_ANSWER' or row['simulated'] != 0:
            raise PreflightFailure('PERSONAL_TEST_COPY_REQUIRED')
        allowed = ('SENDING', 'SEND_UNKNOWN', 'SENT_UI_CONFIRMED') if self._reconciling else ('PENDING', 'SENDING')
        if row['state'] not in allowed:
            raise PreflightFailure('PERSONAL_TEST_SEND_STATE_CHANGED')
        stop = self.store.one("SELECT value FROM settings WHERE key='stop_requested'")
        if not stop or stop[0] != 'false':
            raise PreflightFailure('PERSONAL_TEST_DELIVERY_PAUSED')
        mapping = self.store.one('SELECT * FROM test_answer_copies WHERE test_outbox_id=?', (row['id'],))
        if not mapping:
            raise PreflightFailure('PERSONAL_TEST_COPY_REQUIRED')
        recipient = test_recipient_from_mapping(mapping)
        if (not isinstance(recipient, PersonalWeChatTestRecipient)
                or recipient.room_id != self.pin['target_key']
                or recipient.account_id != self.pin.get('account_id')):
            raise PreflightFailure('PERSONAL_TEST_ACCOUNT_OR_ROOM_MISMATCH')
        expected = test_copy_reconcile_bound(self.store, row) if self._reconciling else validate_test_copy(self.store, row)
        if expected != message:
            raise PreflightFailure('PERSONAL_TEST_BINDING_CHANGED')
        if self.account_chat_validator(recipient) is not True:
            raise PreflightFailure('PERSONAL_TEST_LIVE_IDENTITY_UNCONFIRMED')
        self._active_message = message

    def _native_guard(self):
        if self._active_message is None:
            raise PreflightFailure('PERSONAL_TEST_ACTIVE_TASK_REQUIRED')
        self.authorize(self._active_message)

    def _call(self, tool, **arguments):
        self._native_guard()
        if self._reconciling:
            allowed = tool in ('PowerShell', 'Screenshot')
            allowed |= tool == 'Clipboard' and (arguments == {'mode': 'get'} or
                arguments.get('mode') == 'set' and set(arguments) == {'mode', 'text'}
                and isinstance(arguments['text'], str) and arguments['text'].startswith('receipt-readback-'))
            allowed |= tool == 'Click' and arguments in (
                {'loc': self.pin['receipt_point'], 'button': 'right'}, {'loc': self.pin['copy_menu_point']})
            allowed |= tool == 'Shortcut' and arguments == {'shortcut': 'escape'}
            if not allowed:
                raise PreflightFailure('PERSONAL_TEST_READBACK_ONLY')
        return super()._call(tool, **arguments)

    def _frame(self, message):
        from PIL import Image, ImageOps
        from .wecom_visible import incoming_bubbles
        self.authorize(message)
        probe = {'command': FOREGROUND_COMMAND, 'timeout': 10}
        before = parse_foreground_process(self._call('PowerShell', **probe))
        record = self._call('Screenshot')
        after = parse_foreground_process(self._call('PowerShell', **probe))
        if before != after:
            raise PreflightFailure('PERSONAL_TEST_WINDOW_CHANGED')
        frame = verify_frame(record, self.pin, foreground=after, expected_process='Weixin')
        with Image.open(frame['path']) as image:
            width, view = image.width, self.pin['outgoing_viewport']
            mirrored = incoming_bubbles(ImageOps.mirror(image), {
                'viewport': [width-view[2], view[1], width-view[0], view[3]],
                'incoming_left': width-self.pin['outgoing_right'], 'bubble_color': self.pin['outgoing_color']})
            boxes = sorted(([width-b[2], b[1], width-b[0], b[3]] for b in mirrored), key=lambda b: b[1])
            outgoing = [sha256(json.dumps([b[2]-b[0], b[3]-b[1]]).encode() +
                               image.crop(b).convert('RGB').tobytes()).hexdigest() for b in boxes]
            send_enabled = pixel_hash(image, self.pin['send_button_box']) == self.pin['send_button_hash']
        frame.update(native=after, outgoing=outgoing, boxes=boxes, record=record, send_enabled=send_enabled)
        self.last_record = record
        self.last_frames.append(frame)
        return frame

    def _path(self, message):
        return self.journal_root / (sha256(message.outbox_id.encode()).hexdigest() + '.json')

    def _recipe_hash(self):
        return sha256(json.dumps(self.pin, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def preflight(self, message):
        self.authorize(message)
        if self._path(message).exists():
            raise PreflightFailure('PERSONAL_TEST_EXISTING_ATTEMPT_REQUIRES_READBACK')
        super().preflight(message)
        self._baseline = deepcopy(self.last_frames[-1])

    def _before_submit(self, message):
        self.authorize(message)
        if self._baseline is None:
            raise PreflightFailure('PERSONAL_TEST_BASELINE_REQUIRED')
        if not self.last_frames[-1]['send_enabled']:
            raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED')
        path = self._path(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {key: getattr(message, key) for key in ('outbox_id', 'binding_id', 'group_key', 'student_key', 'body_hash')}
        record.update(state='SUBMISSION_UNCONFIRMED', started_at=now(),
                      recipe_sha256=self._recipe_hash(), before=self._baseline)
        # Exists before the one submit gesture. Restart can only reconcile it.
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False)

    def reconcile(self, message):
        self._reconciling = True
        try:
            self.authorize(message)
            path = self._path(message)
            record = json.loads(path.read_text(encoding='utf-8'))
            if (record.get('state') not in ('SUBMISSION_UNCONFIRMED', 'UI_CONFIRMED')
                    or record.get('recipe_sha256') != self._recipe_hash()
                    or any(record.get(k) != getattr(message, k) for k in
                           ('outbox_id', 'binding_id', 'group_key', 'student_key', 'body_hash'))):
                raise ValueError('PERSONAL_TEST_ATTEMPT_MISMATCH')
            current = self._frame(message)
            before = record['before']['outgoing']
            if not current['editor_blank'] or len(current['outgoing']) != len(before) + 1 or current['outgoing'][:-1] != before:
                raise ValueError('PERSONAL_TEST_NEW_OUTGOING_NOT_UNIQUE')
            box = current['boxes'][-1]
            bounds, size = self.pin['display_bounds'], self.pin['image_size']
            point = self.pin['receipt_point']
            x = (point[0]-bounds[0])*size[0]/(bounds[2]-bounds[0])
            y = (point[1]-bounds[1])*size[1]/(bounds[3]-bounds[1])
            if not box[0] <= x < box[2] or not box[1] <= y < box[3]:
                raise ValueError('PERSONAL_TEST_RECEIPT_IS_NOT_NEW_BUBBLE')
            evidence = super().reconcile(message)
            if evidence.get('confirmed') is not True or self.last_frames[-1]['outgoing'] != current['outgoing']:
                raise ValueError('PERSONAL_TEST_COPY_READBACK_UNCONFIRMED')
            record.update(state='UI_CONFIRMED', confirmed_at=record.get('confirmed_at') or evidence['confirmed_at'])
            path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            evidence.update(confirmed_at=record['confirmed_at'], target_key=message.student_key,
                            transport='PERSONAL_WECHAT_TEST_NATIVE',
                            evidence=str(path), formal_performance_eligible=False,
                            verification='new outgoing UI bubble + exact copied body + frozen test target + blank editor')
            return evidence
        except Exception:
            return {'confirmed': False, 'simulated': False, 'source_student_delivered': False,
                    'formal_performance_eligible': False, 'reason': 'PERSONAL_TEST_RECEIPT_UNCONFIRMED'}
        finally:
            self._reconciling = False
