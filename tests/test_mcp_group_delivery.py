"""Anonymous native protocol/SQLite fixtures; no real UI, account or message."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.delivery import BoundMessage, NotSubmitted, PreflightFailure
from helpdesk.delivery_tasks import AutomaticDelivery, collector_ack_validator
from helpdesk.mcp_group_delivery import MCPGroupDesktop
from helpdesk.mcp_window_probe import FOREGROUND_COMMAND
from helpdesk.storage import now


def reviewed_config(group='anonymous-group', student='anonymous-student'):
    return {'allow_real_send': True, 'platform': 'wecom', 'display_index': 1,
            'controls_verified': True, 'reviewer': 'Synthetic fixture reviewer',
            'verification_evidence': 'SYNTHETIC UIA recipe; not real verification',
            'groups': [{'group_key': group, 'student_keys': [student], 'header_name': '匿名 English',
                        'header_parent_name': '当前会话标题栏', 'header_parent_role': '组',
                        'editor_name': '消息输入', 'self_sender_name': '匿名老师',
                        'self_sender_role': '组', 'receipt_role': 'text'}]}


class NativeProtocolFixture:
    def __init__(self):
        self.calls = []
        self.draft, self.clipboard = '', ''
        self.messages = ['收到']
        self.native = dict(process='WXWork', handle=42, width=1200, height=1100, left=100, top=-1300)
        self.editor = [900, -300]
        self.header, self.display = '匿名 English', 1
        self.locked = self.ambiguous = self.login = self.spoof = False
        self.bad_copy = self.hide_new = self.timeout_after_enter = False
        self.after_snapshot = None
        self.submissions = 0

    def call(self, tool, args):
        self.calls.append((tool, deepcopy(args)))
        text = ''
        if tool == 'PowerShell':
            value = self.native if args['command'] == FOREGROUND_COMMAND else {
                'schema_version': 1, 'observed_at': now(), 'session_id': 2, 'wts_state': 0,
                'session_flags': 0 if self.locked else 1, 'remote': False,
                'input_desktop': 'DEFAULT', 'input_accessible': True, 'windows': {'wxwork': 1, 'edge': 0}}
            text = 'Response: ' + json.dumps(value) + '\nStatus Code: 0'
        elif tool == 'Snapshot':
            tail = ' [focused]' + (' [value:' + json.dumps(self.draft) + ']' if self.draft else '')
            lines = ['window "企业微信"', '  组 "当前会话标题栏"', '    text ' + json.dumps(self.header),
                     '  (' + ','.join(map(str, self.editor)) + ') 编辑 "消息输入"' + tail]
            if self.ambiguous:
                lines.append('  (800,-300) 编辑 "消息输入"')
            if self.login:
                lines.append('  text "安全验证"')
            lines.append('  组 "匿名老师"')
            lines.extend('    text ' + json.dumps(body) for body in self.messages)
            if self.spoof:
                lines.extend(['  组 "学生"', '    text ' + json.dumps(self.spoof)])
            text = f'Selected Displays: {self.display}\nScreenshot Region: (0,-1440,2560,0)\nUI Tree:\n' + '\n'.join(lines)
            if self.after_snapshot:
                self.after_snapshot(self)
        elif tool == 'Type':
            self.draft = args['text']
        elif tool == 'Clipboard':
            if args['mode'] == 'set':
                self.clipboard = args['text']
            else:
                text = 'Clipboard content:\n' + self.clipboard
        elif tool == 'Shortcut':
            if args['shortcut'] == 'ctrl+c' and not self.bad_copy:
                self.clipboard = self.draft
            if args['shortcut'] == 'enter':
                self.submissions += 1
                if not self.hide_new:
                    self.messages.append(self.draft)
                self.draft = ''
                if self.timeout_after_enter:
                    raise TimeoutError('Synthetic post-submit timeout')
        return {'tool': tool, 'is_error': False, 'content': [{'type': 'text', 'text': text}]}


class GroupDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.native = NativeProtocolFixture()
        self.message = BoundMessage('anonymous-outbox', 'anonymous-binding', 'anonymous-group', 'anonymous-student', '收到')
        self.desktop = MCPGroupDesktop(self.native, reviewed_config(), self.root, guard=lambda _: None)

    def test_exact_multiline_input_is_confirmed_only_by_new_own_message(self):
        self.message = BoundMessage('outbox', 'binding', 'anonymous-group', 'anonymous-student', '第12题：A\n完整原文，不压缩。')
        self.desktop.preflight(self.message)
        receipt = self.desktop.send(self.message)
        self.assertTrue(receipt['confirmed'])
        self.assertFalse(receipt['simulated'])
        self.assertEqual(self.native.messages[-1], self.message.body)
        self.assertEqual(self.native.submissions, 1)
        self.assertEqual([args for tool, args in self.native.calls if tool == 'Type'],
                         [{'loc': [900, -300], 'text': self.message.body, 'press_enter': False}])
        self.assertTrue(all(args['display'] == [1] for tool, args in self.native.calls if tool == 'Snapshot'))
        self.assertNotIn('platform_message_id', receipt)
        self.assertNotIn('student_read', receipt)

    def test_previous_ack_or_student_spoof_does_not_confirm_new_delivery(self):
        self.native.hide_new, self.native.spoof = True, '收到'
        self.desktop.preflight(self.message)
        receipt = self.desktop.send(self.message)
        self.assertFalse(receipt['confirmed'])
        self.assertEqual(self.native.submissions, 1)
        self.assertFalse(self.desktop.reconcile(self.message)['confirmed'])
        self.assertEqual(self.native.submissions, 1)

    def test_missing_copy_is_not_satisfied_by_old_identical_clipboard(self):
        self.native.bad_copy, self.native.clipboard = True, self.message.body
        self.desktop.preflight(self.message)
        with self.assertRaises(NotSubmitted):
            self.desktop.send(self.message)
        self.assertEqual(self.native.submissions, 0)
        with self.assertRaises(PreflightFailure):
            self.desktop.preflight(self.message)

    def test_identity_display_ambiguity_login_and_lock_stop_before_input(self):
        for attribute, value in [('header', '其他群'), ('display', 0), ('ambiguous', True), ('login', True), ('locked', True)]:
            with self.subTest(attribute=attribute):
                native = NativeProtocolFixture()
                setattr(native, attribute, value)
                desktop = MCPGroupDesktop(native, reviewed_config(), self.root, guard=lambda _: None)
                with self.assertRaises(PreflightFailure):
                    desktop.preflight(self.message)
                self.assertFalse(any(tool == 'Type' for tool, _ in native.calls))

    def test_foreground_change_during_snapshot_stops_before_input(self):
        self.native.after_snapshot = lambda frame: frame.native.update(handle=99)
        with self.assertRaisesRegex(PreflightFailure, 'WINDOW_CHANGED'):
            self.desktop.preflight(self.message)
        self.assertEqual(self.native.submissions, 0)

    def test_move_after_typing_prevents_submit(self):
        def move(frame):
            if any(tool == 'Shortcut' and args['shortcut'] == 'right' for tool, args in frame.calls):
                frame.editor = [950, -300]
                frame.native['left'] = 150
        self.native.after_snapshot = move
        self.desktop.preflight(self.message)
        with self.assertRaises(NotSubmitted):
            self.desktop.send(self.message)
        self.assertEqual(self.native.submissions, 0)

    def test_pause_or_version_change_during_final_snapshot_prevents_enter(self):
        active = [True]
        def changed(frame):
            if any(tool == 'Shortcut' and args['shortcut'] == 'right' for tool, args in frame.calls):
                active[0] = False
        def guard(_):
            if not active[0]:
                raise ValueError('Synthetic operator pause / changed question')
        self.native.after_snapshot, self.desktop.guard = changed, guard
        self.desktop.preflight(self.message)
        with self.assertRaises(NotSubmitted):
            self.desktop.send(self.message)
        self.assertEqual(self.native.submissions, 0)

    def test_restart_reconciliation_is_read_only_and_body_binding_is_preserved(self):
        self.native.timeout_after_enter = True
        self.desktop.preflight(self.message)
        with self.assertRaises(TimeoutError):
            self.desktop.send(self.message)
        resumed = MCPGroupDesktop(self.native, reviewed_config(), self.root)
        self.assertTrue(resumed.reconcile(self.message)['confirmed'])
        changed = BoundMessage(self.message.outbox_id, self.message.binding_id, self.message.group_key, self.message.student_key, '不同内容')
        self.assertFalse(resumed.reconcile(changed)['confirmed'])
        self.assertEqual(self.native.submissions, 1)

    def test_real_recipe_cannot_be_changed_by_mutating_original_config(self):
        config = reviewed_config()
        desktop = MCPGroupDesktop(self.native, config, self.root, guard=lambda _: None)
        config['groups'][0]['group_key'] = 'other-group'
        self.assertEqual(desktop.authorize(self.message)['group_key'], 'anonymous-group')
        with self.assertRaises(PreflightFailure):
            desktop.authorize(BoundMessage('id', 'binding', 'other-group', 'anonymous-student', '收到'))

    def test_student_text_with_target_group_name_cannot_impersonate_header(self):
        self.native.header, self.native.spoof = '其他群', '匿名 English'
        with self.assertRaises(PreflightFailure):
            self.desktop.preflight(self.message)
        self.assertFalse(any(tool == 'Type' for tool, _ in self.native.calls))

    def test_changed_recipe_cannot_reuse_receipt_from_previous_configuration(self):
        self.desktop.preflight(self.message)
        self.assertTrue(self.desktop.send(self.message)['confirmed'])
        config = reviewed_config()
        config['verification_evidence'] = 'Different synthetic controls review'
        changed = MCPGroupDesktop(self.native, config, self.root)
        self.assertFalse(changed.reconcile(self.message)['confirmed'])
        self.assertEqual(self.native.submissions, 1)


class SourceDeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_shared_source_delivery_integration import SharedSourceDeliveryIntegrationTests
        self.fx = SharedSourceDeliveryIntegrationTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.fx.generate()
        self.db, self.row = self.fx.db, self.fx.row
        self.fx.flow.set_delivery_policy({'ACK': 'AUTO', 'ANSWER': 'AUTO', 'CORRECTION': 'AUTO'}, 'synthetic-auto')
        self.native = NativeProtocolFixture()
        self.desktop = MCPGroupDesktop(self.native, reviewed_config('fixture-room', 'fixture-student'), self.fx.fx.base)
        self.engine = AutomaticDelivery(self.db, self.desktop,
            ack_source_validator=collector_ack_validator(self.fx.fx.raw))
        # The shared-source fixture also contains an earlier unacknowledged question.
        # It must be delivered first without advancing either answer or its counting.
        self.assertEqual(self.engine.tick()['kind'], 'ACK')
        self.assertEqual(self.native.submissions, 1)
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (self.row['id'],))[0], 'PENDING')

    def test_actual_adapter_receipt_flows_to_original_task_and_existing_counting(self):
        from helpdesk.performance import PerformanceLedger
        from helpdesk.performance_reports import PerformanceReports
        first = self.engine.tick()
        self.assertEqual(first['state'], 'SENT_UI_CONFIRMED')
        unit = self.db.one('SELECT * FROM performance_units WHERE id=?', (self.fx.unit_id,))
        self.assertEqual((unit['completion_outbox_id'], unit['category'], unit['measure_unit'], unit['confirmed_quantity']),
                         (self.row['id'], 'NIGHT', '篇', 1))
        self.assertEqual(unit['question_time'], '2026-09-30T23:10:00+08:00')
        self.assertEqual(self.engine.tick()['state'], 'IDLE')
        reports = PerformanceReports(self.db, PerformanceLedger(self.db))
        self.assertEqual(reports.build('2026-09-30')['summary'], reports.build('2026-09-30')['summary'])
        self.assertEqual(self.native.submissions, 2)

    def test_unknown_send_waits_for_explicit_read_only_check_before_counting(self):
        self.native.timeout_after_enter = True
        self.assertEqual(self.engine.tick()['state'], 'SEND_UNKNOWN')
        self.assertNotEqual(self.db.one('SELECT status FROM performance_units WHERE id=?', (self.fx.unit_id,))[0], 'CONFIRMED')
        self.assertEqual(self.engine.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.engine.flow.inspect_unknown(self.row['id']), 'SENT_UI_CONFIRMED')
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units WHERE id=?', (self.fx.unit_id,))[0], 1)
        self.assertEqual(self.native.submissions, 2)

    def test_fresh_question_correction_after_draft_blocks_old_answer(self):
        def changed(frame):
            if frame.draft:
                self.db.execute('UPDATE questions SET context_revision=context_revision+1')
        self.native.after_snapshot = changed
        self.assertEqual(self.engine.tick()['state'], 'FAILED')
        self.assertEqual(self.native.submissions, 1)

    def test_practice_source_is_not_promoted_to_formal_automatic_delivery(self):
        self.db.execute("UPDATE messages SET source='OPERATOR_TEST' WHERE id=?", (self.row['message_id'],))
        self.assertEqual(self.engine.tick()['state'], 'STALE')
        self.assertEqual(self.native.submissions, 1)
        self.assertNotEqual(self.db.one('SELECT status FROM performance_units WHERE id=?', (self.fx.unit_id,))[0], 'CONFIRMED')


if __name__ == '__main__':
    unittest.main()
