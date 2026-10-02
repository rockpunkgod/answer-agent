"""Bound group text delivery through the existing Windows-MCP connection.

Recipes are locally reviewed accessibility names, never LLM-selected recipients.
No navigation, arbitrary script, image matching or inferred platform receipt.
"""
from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
import re
from uuid import uuid4

from .delivery import PreflightFailure, RetryablePreflightFailure, NotSubmitted
from .mcp_page_contract import snapshot_text
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground
from .storage import now


class MCPGroupDesktop:
    simulated = False
    test_only = False

    def __init__(self, transport, config, workspace, *, guard=None):
        self.validate_config(config)
        self.transport = transport
        from .mcp_transport import MCPProcess
        if isinstance(transport, MCPProcess):
            transport.bound_input_process = 'WXWork'
        self.config = deepcopy(config)
        self.guard = guard
        self.workspace = Path(workspace).resolve()
        self.lock_path = str(self.workspace / 'data/windows-interactive-desktop.lock')
        self.journal_root = self.workspace / 'data/private/group-delivery'

    @staticmethod
    def validate_config(config):
        if (not isinstance(config, dict) or config.get('allow_real_send') is not True
                or config.get('platform') != 'wecom' or type(config.get('display_index')) is not int
                or config['display_index'] < 0 or config.get('controls_verified') is not True
                or not isinstance(config.get('reviewer'), str) or not config['reviewer'].strip()
                or not isinstance(config.get('verification_evidence'), str) or not config['verification_evidence'].strip()
                or not isinstance(config.get('groups'), list) or not config['groups']):
            raise ValueError('REVIEWED_GROUP_DELIVERY_CONFIG_REQUIRED')
        keys, headers = set(), set()
        for group in config['groups']:
            if (not isinstance(group, dict) or not all(isinstance(group.get(k), str) and group[k].strip()
                    and not any(c in group[k] for c in '\r\n')
                    for k in ('group_key', 'header_name', 'header_parent_name', 'editor_name', 'self_sender_name'))
                    or group.get('header_parent_role') not in ('组', '列表项')
                    or group.get('self_sender_role') not in ('组', '列表项')
                    or group.get('receipt_role') not in ('text', '文本')
                    or not isinstance(group.get('student_keys'), list) or not group['student_keys']
                    or any(not isinstance(x, str) or not x.strip() for x in group['student_keys'])
                    or group['group_key'] in keys or group['header_name'] in headers):
                raise ValueError('UNIQUE_REVIEWED_GROUP_RECIPE_REQUIRED')
            keys.add(group['group_key']); headers.add(group['header_name'])

    def authorize(self, message):
        groups = [x for x in self.config['groups'] if x['group_key'] == message.group_key
                  and message.student_key in x['student_keys']]
        if len(groups) != 1 or not message.body.strip() or len(message.body) > 30000:
            raise PreflightFailure('GROUP_OR_STUDENT_OUTSIDE_ALLOWLIST')
        return groups[0]

    def _call(self, tool, arguments):
        result = self.transport.call(tool, arguments)
        if result.get('is_error') is not False:
            raise RuntimeError('GROUP_NATIVE_CALL_FAILED')
        return result

    def _inspect(self, message):
        recipe = self.authorize(message)
        def foreground():
            try:
                return parse_foreground(self._call('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10}))
            except ValueError as exc:
                if str(exc) == 'WECOM_NOT_FOREGROUND':
                    raise RetryablePreflightFailure('WINDOW_UNAVAILABLE') from exc
                raise PreflightFailure('GROUP_FOREGROUND_UNCONFIRMED') from exc
        before = foreground()
        observed = self._call('Snapshot', {'display': [self.config['display_index']],
                                           'use_dom': False, 'use_vision': False})
        native = foreground()
        if before != native:
            raise PreflightFailure('GROUP_WINDOW_CHANGED_DURING_CAPTURE')
        text = snapshot_text(observed)
        summary, separator, tree = text.partition('UI Tree:')
        selected = re.findall(r'^Selected Displays: (.*)$', summary, re.M)
        regions = re.findall(r'^Screenshot Region: \((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)\s*$', summary, re.M)
        if selected != [str(self.config['display_index'])] or len(regions) != 1:
            raise PreflightFailure('GROUP_DISPLAY_UNCONFIRMED')
        region = tuple(map(int, regions[0]))
        if region[0] >= region[2] or region[1] >= region[3]:
            raise PreflightFailure('GROUP_DISPLAY_UNCONFIRMED')
        first = re.search(r'window "([^"\n]+)"', tree) if separator else None
        if not first or first[1] != '企业微信':
            raise RetryablePreflightFailure('WINDOW_UNAVAILABLE')
        nodes, stack = [], []
        for line in tree.splitlines():
            match = re.match(r'([ \t│├└─]*)(?:\((-?\d+),(-?\d+)\) )?'
                             r'(window|组|列表项|按钮|编辑|text|文本) ("(?:\\.|[^"\\])*?")(.*)$', line)
            if not match:
                continue
            depth = len(match[1].expandtabs(4))
            while stack and stack[-1]['depth'] >= depth:
                stack.pop()
            name = json.loads(match[5])
            node = {'depth': depth, 'role': match[4], 'name': name, 'tail': match[6],
                    'loc': [int(match[2]), int(match[3])] if match[2] else None,
                    'parents': [(x['role'], x['name']) for x in stack]}
            nodes.append(node); stack.append(node)
        windows = [x for x in nodes if x['role'] == 'window']
        if len(windows) != 1 or windows[0]['name'] != '企业微信':
            raise PreflightFailure('GROUP_WINDOW_AMBIGUOUS')
        if any(x['name'] in ('安全验证', '扫码登录', '验证码', '通过手机企业微信扫码进行安全验证') for x in nodes):
            raise PreflightFailure('WECOM_LOGIN_REQUIRES_OPERATOR')
        header_parent = (recipe['header_parent_role'], recipe['header_parent_name'])
        headers = [x for x in nodes if x['name'] == recipe['header_name'] and x['role'] in ('text', '文本', '按钮')
                   and header_parent in x['parents']]
        editors = [x for x in nodes if (x['role'], x['name']) == ('编辑', recipe['editor_name'])]
        if len(headers) != 1 or len(editors) != 1 or editors[0]['loc'] is None:
            raise PreflightFailure('GROUP_IDENTITY_OR_EDITOR_AMBIGUOUS')
        x, y = editors[0]['loc']
        if not (region[0] <= x < region[2] and region[1] <= y < region[3]):
            raise PreflightFailure('GROUP_EDITOR_OUTSIDE_DISPLAY')
        if not (native['left'] <= x < native['left'] + native['width']
                and native['top'] <= y < native['top'] + native['height']):
            raise PreflightFailure('GROUP_EDITOR_OUTSIDE_CURRENT_WINDOW')
        author = (recipe['self_sender_role'], recipe['self_sender_name'])
        messages = [x['name'] for x in nodes if x['role'] == recipe['receipt_role'] and author in x['parents']]
        return {'observation': observed, 'editor': editors[0], 'messages': messages, 'region': region, 'native': native}

    def _path(self, message):
        return self.journal_root / (sha256(message.outbox_id.encode()).hexdigest() + '.json')

    def _save(self, path, record, *, exclusive=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)

    def _guard(self, message):
        if self.guard is None:
            raise PreflightFailure('GROUP_SEND_GUARD_REQUIRED')
        self.guard(message)

    def preflight(self, message):
        self._guard(message)
        if self._path(message).exists():
            raise PreflightFailure('EXISTING_GROUP_ATTEMPT_REQUIRES_REVIEW')
        from .windows_worker_probe import DESKTOP_STATUS_COMMAND, parse_desktop_status
        started = now()
        result = self._call('PowerShell', {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10})
        content = result.get('content', [])
        try:
            if len(content) != 1 or content[0].get('type') != 'text':
                raise ValueError('Desktop status missing')
            status = parse_desktop_status(content[0]['text'], observed_after=started)
        except ValueError as exc:
            raise PreflightFailure('GROUP_DESKTOP_UNCONFIRMED') from exc
        if not status.unlocked or status.remote is not False:
            raise PreflightFailure('GROUP_DESKTOP_UNAVAILABLE')
        frame = self._inspect(message)
        if '[value:' in frame['editor']['tail']:
            raise PreflightFailure('GROUP_EDITOR_OCCUPIED')

    def _clipboard(self):
        result = self._call('Clipboard', {'mode': 'get'})
        parts = [x['text'] for x in result.get('content', []) if x.get('type') == 'text']
        if len(parts) != 1 or not parts[0].startswith('Clipboard content:\n'):
            raise NotSubmitted('DRAFT_VERIFICATION_FAILED')
        return parts[0].removeprefix('Clipboard content:\n')

    def send(self, message):
        self._guard(message)
        frame = self._inspect(message)
        if '[value:' in frame['editor']['tail']:
            raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED')
        path = self._path(message)
        record = {'outbox_id': message.outbox_id, 'binding_id': message.binding_id,
                  'group_key': message.group_key, 'student_key': message.student_key,
                  'body_hash': message.body_hash, 'state': 'BEFORE_INPUT',
                  'before': frame, 'created_at': now(),
                  'recipe_sha256': sha256(json.dumps(self.config, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        self._save(path, record, exclusive=True)
        try:
            self._guard(message)
            self._call('Type', {'loc': frame['editor']['loc'], 'text': message.body, 'press_enter': False})
            self._guard(message)
            self._call('Clipboard', {'mode': 'set', 'text': 'group-draft-check-' + uuid4().hex})
            for shortcut in ('ctrl+a', 'ctrl+c'):
                self._guard(message)
                current = self._inspect(message)
                if '[focused]' not in current['editor']['tail']:
                    raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED')
                self._call('Shortcut', {'shortcut': shortcut})
            if self._clipboard() != message.body:
                raise NotSubmitted('DRAFT_VERIFICATION_FAILED')
            self._guard(message)
            self._call('Shortcut', {'shortcut': 'right'})
            self._guard(message)
            current = self._inspect(message)
            if (current['region'] != frame['region'] or current['native'] != frame['native']
                    or current['editor']['loc'] != frame['editor']['loc']):
                raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED')
            if '[focused]' not in current['editor']['tail']:
                raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED')
            self._guard(message)
        except Exception as exc:
            record.update(state='DRAFT_REQUIRES_REVIEW', submission_attempted=False)
            self._save(path, record)
            if isinstance(exc, NotSubmitted):
                raise
            raise NotSubmitted('PRE_SUBMISSION_CHECK_FAILED') from exc
        record.update(state='SUBMISSION_UNCONFIRMED', submission_started_at=now())
        self._save(path, record)
        self._call('Shortcut', {'shortcut': 'enter'})  # Exactly one submit; never retry.
        return self.reconcile(message)

    def reconcile(self, message):
        self.authorize(message)
        path = self._path(message)
        if not path.exists():
            return {'confirmed': False, 'simulated': False, 'reason': 'GROUP_BASELINE_MISSING'}
        record = json.loads(path.read_text(encoding='utf-8'))
        if (any(record.get(k) != getattr(message, k) for k in ('outbox_id', 'binding_id', 'group_key', 'student_key', 'body_hash'))
                or record.get('recipe_sha256') != sha256(json.dumps(self.config, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                or record.get('state') not in ('SUBMISSION_UNCONFIRMED', 'UI_CONFIRMED')):
            return {'confirmed': False, 'simulated': False, 'reason': 'GROUP_ATTEMPT_UNCONFIRMED'}
        observed = self._inspect(message)
        before, after = record['before']['messages'], observed['messages']
        confirmed = (after[:len(before)] == before and len(after) == len(before) + 1
                     and after[-1] == message.body and '[value:' not in observed['editor']['tail'])
        record.update(state='UI_CONFIRMED' if confirmed else 'SUBMISSION_UNCONFIRMED', after=observed)
        if confirmed:
            record['confirmed_at'] = record.get('confirmed_at') or now()
        self._save(path, record)
        return {'confirmed': confirmed, 'simulated': False, 'body_hash': message.body_hash,
                'confirmed_at': record.get('confirmed_at'), 'evidence': str(path),
                'verification': 'fresh group + exact clipboard + appended own-sender UI message',
                'reason': None if confirmed else 'NEW_OWN_MESSAGE_NOT_UNAMBIGUOUS'}
