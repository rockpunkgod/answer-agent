"""Stage a saved, original DeepSeek answer in a verified WeCom group; never send.

The desktop executor supplies a fresh, short-lived group pin. This module cannot
search for a recipient, infer platform identities, approve an answer or record a
delivery. Every uncertain attempt remains durable and is never automatically
replayed. Desktop execution belongs to the configured automation actor.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from .answer_review_packets import list_review_packets
from .delivery import PreflightFailure
from .locking import resource_lock
from .mcp_test_delivery import verify_frame
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground


class ManualGroupDraft:
    def __init__(self, transport, pin, workspace, packet_root):
        self.transport = transport
        self.pin = dict(pin)
        self.workspace = Path(workspace).resolve(strict=True)
        self.packet_root = Path(packet_root).resolve(strict=True)
        self.journal_root = self.workspace / 'data/private/manual-group-drafts'
        self.desktop_lock = self.workspace / 'data/windows-interactive-desktop.lock'
        self.record = None
        self.journal = None
        self._authority_context = None
        self._authority_snapshot = None
        self._readback_mode = False

    def _evidence_file(self, descriptor):
        if not isinstance(descriptor, dict) or set(descriptor) != {'path', 'sha256'}:
            raise PreflightFailure('BOUNDED_LOCAL_EVIDENCE_REQUIRED')
        path = Path(descriptor['path']).resolve(strict=True)
        if (not path.is_relative_to(self.workspace) or not path.is_file()
                or path.stat().st_size > 2_000_000):
            raise PreflightFailure('LOCAL_EVIDENCE_OUTSIDE_WORKSPACE')
        data = path.read_bytes()
        if sha256(data).hexdigest() != descriptor['sha256']:
            raise PreflightFailure('LOCAL_EVIDENCE_HASH_MISMATCH')
        return json.loads(data), str(path)

    def _authority(self, store, outbox_id, *, readback=False):
        from .test_answer_queue import validate_source_answer
        from .workflow import Workflow
        from .delivery import BoundMessage
        # Policy access is read-only; constructing Workflow would create a mock
        # transport database, which this adapter must never do implicitly.
        policy = Workflow.__new__(Workflow)
        policy.db = store
        row = store.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
        allowed = ('PENDING', 'SENDING', 'SEND_UNKNOWN', 'SENT_UI_CONFIRMED', 'STALE') if readback else ('PENDING',)
        if (not row or row['purpose'] not in ('ANSWER', 'CORRECTION') or row['simulated'] != 0
                or row['state'] not in allowed):
            raise PreflightFailure('REAL_ORIGINAL_ANSWER_OUTBOX_REQUIRED')
        if not readback and policy._stopped():
            raise PreflightFailure('STOPPED')
        if not readback and policy._delivery_mode(row) != 'MANUAL':
            raise PreflightFailure('MANUAL_FINAL_SEND_STAGE_REQUIRED')
        if readback:
            binding = store.one('SELECT * FROM bindings WHERE id=?', (row['binding_id'],))
            answer = store.one('SELECT * FROM answers WHERE id=?', (row['answer_id'],))
            evidence = store.one('SELECT * FROM answer_evidence WHERE answer_id=?', (row['answer_id'],))
            if (not binding or not binding['verified'] or not answer or not evidence
                    or answer['state'] not in ('GENERATED', 'STALE') or answer['text'] != row['body']
                    or any(answer[k] != row[k] for k in ('turn_id', 'question_version', 'context_revision'))
                    or not evidence['complete'] or not evidence['uploads_confirmed'] or evidence['simulated'] != 0):
                raise PreflightFailure('FROZEN_DELIVERED_ANSWER_REQUIRED')
            bound = BoundMessage(row['id'], binding['id'], binding['group_key'], binding['student_key'], row['body'])
        else:
            bound, answer = validate_source_answer(store, row, approval=False)
        message = store.one('SELECT * FROM messages WHERE id=?', (row['message_id'],))
        turn = store.one('SELECT * FROM turns WHERE id=?', (row['turn_id'],))
        run = store.one('SELECT * FROM runs WHERE id=?', (row['run_id'],))
        if (not message or message['source'].upper() == 'OPERATOR_TEST' or not turn or not run
                or message['binding_id'] != row['binding_id']
                or message['case_id'] != row['case_id'] or message['question_id'] != answer['question_id']
                or turn['message_id'] != row['message_id'] or turn['case_id'] != row['case_id']
                or turn['question_id'] != answer['question_id'] or run['state'] != 'GENERATED'
                or any(run[k] != row[k] for k in ('turn_id', 'question_version', 'context_revision'))):
            raise PreflightFailure('ORIGINAL_ANSWER_RUN_OR_SOURCE_MISMATCH')
        frozen = json.loads(run['input_json'])
        original_media = json.loads(message['attachments'])
        def media_hashes(items):
            return [{key: item.get(key) for key in ('path', 'sha256', 'provenance')} for item in items]
        case = store.one('SELECT binding_id FROM cases WHERE id=?', (row['case_id'],))
        generated = store.one('SELECT * FROM answer_evidence WHERE answer_id=?', (row['answer_id'],))
        if (frozen.get('simulated') is not False or frozen.get('run_id') != run['id']
                or frozen.get('binding_id') != row['binding_id'] or frozen.get('case_id') != row['case_id']
                or frozen.get('question_id') != answer['question_id']
                or frozen.get('turn_id') != row['turn_id'] or frozen.get('student_words') != message['raw_text']
                or media_hashes(frozen.get('attachments', [])) != media_hashes(original_media)
                or any(frozen.get(k) != row[k] for k in ('question_version', 'context_revision'))
                or not case or case['binding_id'] != row['binding_id']
                or not generated or generated['session_id'] != run['session_id']
                or answer['adapter'] != frozen.get('generation_adapter')):
            raise PreflightFailure('FROZEN_ANSWER_IDENTITY_MISMATCH')
        value = {key: row[key] for key in ('id', 'binding_id', 'run_id', 'answer_id', 'turn_id',
                                          'message_id', 'case_id', 'question_version', 'context_revision')}
        value['outbox_id'] = value.pop('id')
        value.update(group_key=bound.group_key, student_key=bound.student_key, body_hash=bound.body_hash)
        for key, expected in value.items():
            if self.pin.get(key) != expected:
                raise PreflightFailure('AUTHORITATIVE_GROUP_PIN_MISMATCH')
        # These hashes bind immutable authority to the persisted draft attempt;
        # they are internal evidence, not caller-supplied identity fields.
        value['source_sha256'] = sha256(json.dumps({key: message[key] for key in
            ('id', 'binding_id', 'source', 'platform_id', 'raw_text', 'attachments', 'source_sent_at',
             'source_time_evidence')}, sort_keys=True).encode()).hexdigest()
        value['run_sha256'] = sha256(run['input_json'].encode()).hexdigest()
        if (not self.pin.get('identity_evidence') or self.pin.get('identity_verified') is not True
                or bound.group_key.startswith('local-operator-test:') or bound.student_key.startswith('local-fixture:')):
            raise PreflightFailure('PERSISTENT_GROUP_STUDENT_IDENTITY_REQUIRED')
        identity, _ = self._evidence_file(self.pin['identity_evidence'])
        if (identity.get('platform') != 'wecom' or identity.get('verified') is not True
                or any(identity.get(k) != value[k] for k in ('binding_id', 'group_key', 'student_key'))
                or any(identity.get(k) != self.pin.get(k) for k in ('group_observed', 'student_observed'))):
            raise PreflightFailure('PERSISTENT_IDENTITY_EVIDENCE_MISMATCH')
        if identity.get('method') == 'verified_gui_session':
            fields = ('verified_at', 'expires_at', 'window_geometry_handle', 'window_rect',
                      'header_hash', 'avatar_hash', 'screenshot_display', 'display_origin', 'source_message_locator')
            if (identity.get('platform_identity_claimed') is not False
                    or any(identity.get(k) != self.pin.get(k) or k not in identity for k in fields)
                    or identity.get('source_message_id') != row['message_id']
                    or not isinstance(identity.get('source_message_locator'), str)
                    or not identity['source_message_locator'].strip()):
                raise PreflightFailure('SHORT_LIVED_GUI_IDENTITY_UNCONFIRMED')
            source, _ = self._evidence_file(identity.get('source_evidence'))
            if (source.get('kind') != 'GUI_STUDENT_SOURCE' or source.get('platform') != 'wecom'
                    or source.get('message_id') != row['message_id']
                    or source.get('message_locator') != identity['source_message_locator']
                    or source.get('body_hash') != sha256(message['raw_text'].encode()).hexdigest()
                    or any(source.get(k) != identity.get(k) for k in
                           ('group_observed', 'student_observed', 'header_hash', 'avatar_hash',
                            'window_geometry_handle', 'window_rect', 'screenshot_display', 'display_origin'))):
                raise PreflightFailure('GUI_SOURCE_MESSAGE_EVIDENCE_MISMATCH')
            source_frame = Path(source.get('screenshot_path', '')).resolve(strict=True)
            if (not source_frame.is_relative_to(self.workspace) or not source_frame.is_file()
                    or source_frame.stat().st_size > 25_000_000
                    or sha256(source_frame.read_bytes()).hexdigest() != source.get('screenshot_sha256')):
                raise PreflightFailure('GUI_SOURCE_FRAME_EVIDENCE_CHANGED')
            from .performance_rules import timestamp
            source_observed = timestamp(source.get('observed_at')).timestamp()
            # ISO timestamps retain microseconds; time.time pins may retain
            # additional fractional precision, so allow serialization rounding.
            if not identity['verified_at'] - 300 <= source_observed <= identity['verified_at'] + 0.000001:
                raise PreflightFailure('GUI_SOURCE_FRAME_TIME_UNCONFIRMED')
            rect = identity['window_rect']
            verify_frame({'tool': 'Screenshot', 'is_error': False,
                          'content': [{'type': 'image', 'path': str(source_frame)}]}, self.pin,
                         foreground={'process': 'WXWork', 'left': rect[0], 'top': rect[1],
                                     'width': rect[2], 'height': rect[3],
                                     'handle': identity['window_geometry_handle'][2]})
        elif identity.get('method') != 'persistent_platform_ids':
            raise PreflightFailure('VERIFIED_PLATFORM_OR_GUI_IDENTITY_REQUIRED')
        if self._authority_snapshot is not None and self._authority_snapshot != value:
            raise PreflightFailure('ORIGINAL_OUTBOX_CHANGED_DURING_DRAFT')
        if readback:
            journal = self.journal_root / (self.pin.get('packet_id', '') + '.json')
            prior = json.loads(journal.read_bytes())
            if (prior.get('authority') != value or prior.get('status') not in
                    ('DRAFT_VERIFIED_AWAITING_HUMAN_SEND', 'DRAFT_OUTCOME_UNCONFIRMED')):
                raise PreflightFailure('ORIGINAL_OUTBOX_DRAFT_JOURNAL_REQUIRED')
        return value, bound

    def stage_outbox(self, store, outbox_id, *, semantic_decision_id=None):
        """Stage the current real original outbox; no approval or sending occurs."""
        value, _ = self._authority(store, outbox_id)
        packet = self._packet(self.pin.get('packet_id'))
        if packet['answer_sha256'] != value['body_hash']:
            raise PreflightFailure('OUTBOX_PACKET_BODY_MISMATCH')
        if semantic_decision_id is not None and (not isinstance(semantic_decision_id, str)
                or not semantic_decision_id.strip() or self.pin.get('semantic_decision_id') != semantic_decision_id):
            raise PreflightFailure('SEMANTIC_DECISION_ASSOCIATION_MISMATCH')
        self._authority_context = (store, outbox_id, semantic_decision_id)
        self._authority_snapshot = value
        try:
            result = self.stage(packet['packet_id'])
            return {**result, **value, 'semantic_decision_id': semantic_decision_id}
        finally:
            self._authority_context = None
            self._authority_snapshot = None

    def readback_outbox(self, store, outbox_id):
        """Observe an actual teacher bubble; never submit or change business state.

        A trusted observer returns hashed local observation, clipboard_result and
        clipboard_attempt descriptors. Copied bubble text must match the whole
        answer; screenshots, a blank composer and claimed success cannot suffice.
        This interface has no built-in real observer or recipient locator.
        """
        failed = {'confirmed': False, 'simulated': False, 'outbox_id': outbox_id,
                  'reason': 'MANUAL_READBACK_UNCONFIRMED', 'server_receipt': False, 'recipient_read': False}
        try:
            packet = self._packet(self.pin.get('packet_id'))
            value, bound = self._authority(store, outbox_id, readback=True)
            self._authorize(packet)
            observer = getattr(self.transport, 'observe_sent_bubble', None)
            if not callable(observer):
                return {**failed, 'reason': 'RECEIPT_OBSERVER_UNAVAILABLE'}
            self._authority_context = (store, outbox_id, None)
            self._authority_snapshot = value
            self._readback_mode = True
            self.journal_root.mkdir(parents=True, exist_ok=True)
            with resource_lock(self.desktop_lock):
                self.journal = self.journal_root / ('readback-' + uuid4().hex + '.json')
                self.record = {'status': 'READBACK_STARTED', 'authority': value,
                               'started_at': datetime.now(timezone.utc).isoformat(), 'events': [],
                               'answer_sent': False, 'automatic_retry_allowed': False, 'pin': dict(self.pin)}
                self._save()
                self._frame(packet)
                self.record['status'] = 'BUBBLE_OBSERVATION_UNCONFIRMED'
                self._save()
                descriptors = observer(bound, {**self.pin, **value})
                self._frame(packet)
                if not isinstance(descriptors, dict):
                    raise PreflightFailure('READONLY_BUBBLE_EVIDENCE_REQUIRED')
                observation, observation_path = self._evidence_file(descriptors.get('observation'))
                copied, copied_path = self._evidence_file(descriptors.get('clipboard_result'))
                attempt, attempt_path = self._evidence_file(descriptors.get('clipboard_attempt'))
                identity, identity_path = self._evidence_file(self.pin['identity_evidence'])
                if (observation.get('kind') != 'VISIBLE_SENT_BUBBLE' or observation.get('visible') is not True
                        or observation.get('platform') != 'wecom' or observation.get('sender_role') != 'TEACHER'
                        or not self.pin.get('teacher_sender_id')
                        or observation.get('sender_id') != self.pin['teacher_sender_id']
                        or self.pin['teacher_sender_id'] not in identity.get('teacher_sender_ids', [])
                        or any(observation.get(k) != value[k] for k in value)
                        or not isinstance(observation.get('message_locator'), str)
                        or not observation['message_locator'].strip()):
                    raise PreflightFailure('VISIBLE_TEACHER_BUBBLE_TARGET_MISMATCH')
                acquisition = copied.get('attempt_id')
                if (not isinstance(acquisition, str) or len(acquisition) != 32
                        or any(c not in '0123456789abcdef' for c in acquisition)
                        or attempt.get('attempt_id') != acquisition
                        or observation.get('clipboard_attempt_id') != acquisition
                        or copied.get('tool') != 'Clipboard' or copied.get('is_error') is not False
                        or attempt.get('tool') != 'Clipboard' or attempt.get('arguments') != {'mode': 'get'}
                        or attempt.get('status') != 'TOOL_RETURNED'
                        or Path(attempt.get('result_path', '')).resolve() != Path(copied_path)
                        or copied.get('content') != [{'type': 'text', 'text': 'Clipboard content:\n' + bound.body}]):
                    raise PreflightFailure('COMPLETE_SENT_BUBBLE_READBACK_REQUIRED')
                from .performance_rules import timestamp
                sent = timestamp(observation.get('sent_at'))
                acquired = timestamp(attempt.get('started_at'))
                observed = timestamp(observation.get('observed_at'))
                current = datetime.now(timezone.utc)
                row = store.one('SELECT created_at,sent_at FROM outbox WHERE id=?', (outbox_id,))
                if (not timestamp(row['created_at']) <= sent <= acquired <= observed <= current
                        or observed < timestamp(self.record['started_at'])
                        or row['sent_at'] and timestamp(row['sent_at']) != sent):
                    raise PreflightFailure('ACTUAL_BUBBLE_TIME_UNCONFIRMED')
                self._authority(store, outbox_id, readback=True)
                evidence = {**value, 'confirmed': True, 'simulated': False,
                            'confirmed_at': sent.isoformat(), 'sender_role': 'TEACHER',
                            'message_locator': observation['message_locator'],
                            'verification': 'trusted visible teacher bubble + exact native copied text + persistent target pin',
                            'evidence_paths': [observation_path, copied_path, attempt_path, identity_path, str(self.journal)],
                            'evidence_sha256': {key: descriptor['sha256'] for key, descriptor in descriptors.items()
                                                if key in ('observation', 'clipboard_result', 'clipboard_attempt')},
                            'server_receipt': False, 'recipient_read': False}
                self.record.update(status='SENT_BUBBLE_OBSERVED', evidence=evidence)
                self._save()
                return evidence
        except Exception:
            if self._readback_mode and self.record is not None:
                self.record['status'] = 'READBACK_UNCONFIRMED'
                try:
                    self._save()
                except OSError:
                    pass
            return failed
        finally:
            self._authority_context = None
            self._authority_snapshot = None
            self._readback_mode = False

    def _packet(self, packet_id):
        packets = list_review_packets(self.packet_root)['packets']
        matches = [p for p in packets if p['packet_id'] == packet_id]
        if len(matches) != 1:
            raise ValueError('NO_UNIQUE_SAVED_ORIGINAL_ANSWER')
        return matches[0]

    def _authorize(self, packet):
        p = self.pin
        acquired = p.get('verified_at')
        expires = p.get('expires_at')
        current = time.time()
        if (type(acquired) not in (int, float) or type(expires) not in (int, float)
                or not math.isfinite(acquired) or not math.isfinite(expires)
                or not acquired <= current < expires or expires - acquired > 300):
            raise PreflightFailure('GROUP_DRAFT_PIN_EXPIRED_OR_INVALID')
        if (p.get('platform') != 'wecom' or p.get('target_kind') != 'group'
                or p.get('group_observed') != packet['group_observed']
                or p.get('student_observed') != packet['student_observed']
                or p.get('packet_id') != packet['packet_id']
                or p.get('body_hash') != packet['answer_sha256']
                or p.get('native_copy_acquisition_id') != packet['native_copy_acquisition_id']
                or not isinstance(p.get('source_evidence'), str) or not p['source_evidence'].strip()):
            raise PreflightFailure('GROUP_DRAFT_SOURCE_OR_TARGET_MISMATCH')
        origin = p.get('display_origin')
        explicit_display = origin is not None or 'screenshot_display' in p
        if explicit_display:
            rect = p.get('window_rect')
            if (not isinstance(origin, list) or len(origin) != 2 or any(type(v) is not int for v in origin)
                    or type(p.get('screenshot_display')) is not int or p['screenshot_display'] < 0
                    or not isinstance(rect, list) or len(rect) != 4
                    or any(type(v) is not int for v in rect) or rect[2] <= 0 or rect[3] <= 0):
                raise PreflightFailure('EXPLICIT_DISPLAY_AND_WINDOW_RECT_REQUIRED')
        if (not isinstance(p.get('compose_point'), list) or len(p['compose_point']) != 2
                or any(type(v) is not int or (not explicit_display and v < 0) for v in p['compose_point'])):
            raise PreflightFailure('GROUP_DRAFT_EDITOR_POINT_INVALID')
        size = p.get('image_size')
        geometry = p.get('window_geometry_handle')
        if (not isinstance(size, list) or len(size) != 2
                or any(type(v) is not int or v <= 0 for v in size)
                or not isinstance(geometry, list) or len(geometry) != 3
                or any(type(v) is not int or v <= 0 for v in geometry)):
            raise PreflightFailure('GROUP_DRAFT_GEOMETRY_INVALID')
        for key in ('header', 'avatar', 'editor'):
            box = p.get(key + '_box')
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) is not int for v in box)
                    or not 0 <= box[0] < box[2] <= size[0]
                    or not 0 <= box[1] < box[3] <= size[1]):
                raise PreflightFailure('GROUP_DRAFT_REGION_INVALID')
        x, y = p['compose_point']
        if explicit_display:
            x, y = x - origin[0], y - origin[1]
        left, top, right, bottom = p['editor_box']
        if not left < x < right or not top < y < bottom:
            raise PreflightFailure('GROUP_DRAFT_EDITOR_POINT_OUTSIDE_EDITOR')
        if '\x00' in packet['text']:
            raise ValueError('ORIGINAL_ANSWER_CONTAINS_NULL')
        if self._authority_context is not None:
            self._authority(*self._authority_context[:2], readback=self._readback_mode)

    def _save(self):
        self.journal.write_text(json.dumps(self.record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def _call(self, tool, arguments):
        if self._authority_context is not None:
            self._authorize(self._packet(self.pin['packet_id']))
        event = {'tool': tool, 'arguments': arguments, 'status': 'OUTCOME_UNCONFIRMED'}
        self.record['events'].append(event)
        self._save()
        kwargs = {'expected_foreground_process': 'WXWork'} if tool in ('Click', 'Shortcut') else {}
        result = self.transport.call(tool, arguments, **kwargs)
        event.update(status='TOOL_RETURNED', result=result)
        self._save()
        if result.get('is_error') is not False or result.get('error'):
            raise ValueError('GROUP_DRAFT_TOOL_UNCONFIRMED')
        return result

    def _frame(self, packet):
        self._authorize(packet)
        before = parse_foreground(self._call('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10}))
        if 'window_rect' in self.pin and [before[k] for k in ('left', 'top', 'width', 'height')] != self.pin['window_rect']:
            raise PreflightFailure('WECOM_WINDOW_RECT_CHANGED')
        arguments = {'display': [self.pin['screenshot_display']]} if 'screenshot_display' in self.pin else {}
        frame = self._call('Screenshot', arguments)
        after = parse_foreground(self._call('PowerShell', {'command': FOREGROUND_COMMAND, 'timeout': 10}))
        if before != after:
            raise PreflightFailure('FOREGROUND_CHANGED_DURING_DRAFT_CHECK')
        return verify_frame(frame, self.pin, foreground=after)

    def _input(self, packet, tool, arguments):
        # Check the visible group before input; the native guard checks WXWork
        # again immediately before its gesture. Neither check switches windows.
        self._frame(packet)
        return self._call(tool, arguments)

    def stage(self, packet_id):
        packet = self._packet(packet_id)
        self._authorize(packet)
        self.journal_root.mkdir(parents=True, exist_ok=True)
        self.journal = self.journal_root / (packet['packet_id'] + '.json')
        with resource_lock(self.desktop_lock):
            if self.journal.exists():
                # A historical successful draft does not prove the current
                # editor still contains it. Never paste a second time here.
                prior = json.loads(self.journal.read_bytes())
                if self._authority_snapshot is not None and prior.get('authority') != self._authority_snapshot:
                    raise PreflightFailure('PACKET_DRAFT_ALREADY_BOUND_TO_ANOTHER_AUTHORITY')
                return {'status': 'PRIOR_DRAFT_ATTEMPT_EXISTS', 'previous_status': prior['status'],
                        'journal': str(self.journal), 'replayed': False,
                        'current_draft_verified': False, 'answer_sent': False}
            self.record = {'status': 'DRAFT_ATTEMPT_STARTED', 'packet_id': packet['packet_id'],
                'group_observed': packet['group_observed'], 'student_observed': packet['student_observed'],
                'answer_sha256': packet['answer_sha256'], 'native_copy_acquisition_id': packet['native_copy_acquisition_id'],
                'started_at': datetime.now(timezone.utc).isoformat(), 'pin': self.pin, 'events': [],
                'automatic_retry_allowed': False, 'answer_sent': False,
                'formal_performance_eligible': False, 'platform_identity_claimed': False}
            if self._authority_snapshot is not None:
                self.record['authority'] = dict(self._authority_snapshot)
                self.record['semantic_decision_id'] = self._authority_context[2]
            with self.journal.open('x', encoding='utf-8') as stream:
                json.dump(self.record, stream, ensure_ascii=False, indent=2)
            try:
                frame = self._frame(packet)
                if not frame['editor_blank']:
                    raise PreflightFailure('COMPOSE_AREA_NOT_EMPTY')
                self._input(packet, 'Click', {'loc': self.pin['compose_point']})
                self._call('Clipboard', {'mode': 'set', 'text': packet['text']})
                self._input(packet, 'Shortcut', {'shortcut': 'ctrl+v'})
                self.record['status'] = 'DRAFT_PASTED_REQUIRES_READBACK'
                self._save()
                self._input(packet, 'Click', {'loc': self.pin['compose_point']})
                self._call('Clipboard', {'mode': 'set', 'text': 'draft-readback-' + packet['answer_sha256']})
                self._input(packet, 'Shortcut', {'shortcut': 'ctrl+a'})
                self._input(packet, 'Shortcut', {'shortcut': 'ctrl+c'})
                result = self._call('Clipboard', {'mode': 'get'})
                content = result.get('content')
                if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                        or content[0].get('type') != 'text'
                        or content[0].get('text') != 'Clipboard content:\n' + packet['text']):
                    raise ValueError('COMPLETE_ORIGINAL_DRAFT_READBACK_MISMATCH')
                latest = self._packet(packet_id)
                if latest != packet:
                    raise ValueError('SAVED_ORIGINAL_ANSWER_CHANGED_DURING_DRAFT')
                self._input(packet, 'Shortcut', {'shortcut': 'right'})
                final = self._frame(packet)
                if final['editor_blank']:
                    raise ValueError('DRAFT_DISAPPEARED_AFTER_READBACK')
                self.record.update(status='DRAFT_VERIFIED_AWAITING_HUMAN_SEND',
                    verified_at=datetime.now(timezone.utc).isoformat(), original_text_unchanged=True,
                    readback_sha256=sha256(packet['text'].encode('utf-8')).hexdigest(), final_frame=final)
                self._save()
            except BaseException as exc:
                self.record.update(status='DRAFT_OUTCOME_UNCONFIRMED', error_type=type(exc).__name__,
                                   error=str(exc), automatic_retry_allowed=False)
                self._save()
                raise
        return {'status': self.record['status'], 'journal': str(self.journal),
                'current_draft_verified': True, 'answer_sent': False,
                'formal_performance_eligible': False, 'original_text_unchanged': True}
