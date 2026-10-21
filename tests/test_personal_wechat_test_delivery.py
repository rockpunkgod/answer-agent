"""Scripted GUI + disposable SQLite; no real client, browser, message or fee."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

from PIL import Image

from helpdesk.delivery import PreflightFailure
from helpdesk.mcp_test_delivery import pixel_hash, verify_frame
from helpdesk.personal_wechat_test_delivery import PersonalWeChatTestDesktop
from helpdesk.test_answer_queue import queue_test_answer, validate_test_copy, test_copy_reconcile_bound as reconcile_bound
from helpdesk.test_routing import PersonalWeChatTestRecipient, TestRecipient
from helpdesk.workflow import Workflow
from tests import test_live_generation


class ScriptedNative:
    def __init__(self, pin, root, *, guard):
        self.pin, self.guard, self.root = pin, guard, Path(root)
        self.calls = []
        self.editor = self.clipboard = ''
        self.body = ''
        self.sent = self.menu = False
        self.submits = 0
        self.submit_error = self.copy_wrong = self.no_append = False
        self.process = 'Weixin'
        self.button_changed = False
        self.after_submit = lambda: None

    def frame_image(self):
        image = Image.new('RGB', (400, 300), 'white')
        image.paste('red', (0, 0, 10, 10))
        image.paste('blue', (10, 0, 20, 10))
        if self.editor:
            image.paste('black', (30, 220, 80, 235))
        if self.sent and not self.no_append:
            image.paste((149, 236, 105), (310, 100, 380, 150))
        if self.menu:
            image.paste('black', (20, 130, 100, 170))
        if self.button_changed:
            image.paste('red', (330, 240, 380, 270))
        return image

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool in ('Click', 'Clipboard', 'Shortcut', 'Type'):
            self.guard()
        parts = []
        if tool == 'PowerShell':
            value = dict(process=self.process, handle=1, width=400, height=300, left=0, top=0)
            parts = [{'type': 'text', 'text': 'Response: ' + json.dumps(value) + '\nStatus Code: 0'}]
        elif tool == 'Screenshot':
            path = self.root / ('anonymous-frame-' + str(len(self.calls)) + '.png')
            self.frame_image().save(path)
            parts = [{'type': 'image', 'path': str(path)}]
        elif tool == 'Type':
            self.editor = args['text']
        elif tool == 'Clipboard':
            if args['mode'] == 'set':
                self.clipboard = args['text']
            else:
                parts = [{'type': 'text', 'text': 'Clipboard content:\n' + self.clipboard}]
        elif tool == 'Shortcut':
            if args['shortcut'] == 'ctrl+v':
                self.editor = self.clipboard
            elif args['shortcut'] == 'ctrl+c':
                self.clipboard = self.editor
        elif tool == 'Click':
            if args['loc'] == self.pin['send_point']:
                self.submits += 1
                if self.submit_error:
                    raise TimeoutError('synthetic unknown submit')
                self.body, self.editor, self.sent = self.editor, '', True
                self.after_submit()
            elif args['loc'] == self.pin['receipt_point']:
                self.menu = True
            elif args['loc'] == self.pin['copy_menu_point']:
                self.clipboard = 'wrong copied body' if self.copy_wrong else self.body
                self.menu = False
        return {'tool': tool, 'backend': 'PERSONAL_WECHAT_TEST_NATIVE', 'is_error': False, 'content': parts}


class PersonalTestDeliveryTests(unittest.TestCase):
    def setUp(self):
        fixture = test_live_generation.LiveGenerationBoundaryTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        self.store, self.root = fixture.db, fixture.base
        flow, run = fixture.start(test_live_generation.StubAdapter())
        snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        self.source_id = flow.finish(run, test_live_generation.StubAdapter().generate(snapshot))['outbox_id']
        flow.approve(self.source_id)
        self.recipient = PersonalWeChatTestRecipient('anonymous-account', 'anonymous-room@chatroom',
            'synthetic account + unique room evidence', datetime.now(timezone.utc))
        self.oid = queue_test_answer(self.store, self.source_id, self.recipient)
        self.bound = validate_test_copy(self.store, self.store.one('SELECT * FROM outbox WHERE id=?', (self.oid,)))
        image = Image.new('RGB', (400, 300), 'white')
        image.paste('red', (0, 0, 10, 10)); image.paste('blue', (10, 0, 20, 10))
        menu = image.copy(); menu.paste('black', (20, 130, 100, 170))
        self.pin = dict(platform='personal_wechat_test', display_name='测试', reviewed=True,
            target_key=self.recipient.room_id, account_id=self.recipient.account_id,
            verification_evidence='synthetic reviewed controls', expires_at=time.time()+300,
            outbox_id=self.oid, binding_id=self.bound.binding_id, body_hash=self.bound.body_hash,
            observation_mode='fixed_foreground_probe', copy_menu_verified=True,
            window_geometry_handle=[400, 300, 1], image_size=[400, 300], display_bounds=[0, 0, 400, 300], display_index=0,
            header_box=[0, 0, 10, 10], avatar_box=[10, 0, 20, 10], editor_box=[20, 210, 300, 260],
            header_hash=pixel_hash(image, [0, 0, 10, 10]), avatar_hash=pixel_hash(image, [10, 0, 20, 10]),
            send_button_box=[330, 240, 380, 270], send_button_hash=pixel_hash(image, [330, 240, 380, 270]),
            copy_menu_box=[20, 130, 100, 170], copy_menu_hash=pixel_hash(menu, [20, 130, 100, 170]),
            compose_point=[50, 230], send_point=[350, 250], receipt_point=[350, 120], copy_menu_point=[40, 145],
            outgoing_viewport=[50, 40, 390, 190], outgoing_right=380, outgoing_color=[149, 236, 105])
        self.identity = True
        self.desktop = self.make_desktop()
        self.native = self.desktop.transport

    def make_desktop(self, pin=None, validator=None):
        return PersonalWeChatTestDesktop(self.store, pin or self.pin, self.root,
            account_chat_validator=validator or (lambda recipient: self.identity), native_factory=ScriptedNative)

    def test_account_room_scoping_and_existing_outbox_idempotence(self):
        self.assertEqual(queue_test_answer(self.store, self.source_id, self.recipient), self.oid)
        self.assertEqual(self.bound.group_key, 'personal_wechat_test')
        self.assertEqual(self.bound.student_key, self.recipient.stable_key)
        other_account = replace(self.recipient, account_id='other-account')
        self.assertNotEqual(other_account.stable_key, self.recipient.stable_key)
        with self.assertRaisesRegex(ValueError, 'TARGET_ALREADY_BOUND'):
            queue_test_answer(self.store, self.source_id, other_account)
        for invalid in ({'display_name': '答疑1群'}, {'room_id': '测试'}, {'platform': 'wecom'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(self.recipient, **invalid)
        with self.assertRaises(ValueError):
            TestRecipient('personal_wechat_test', self.recipient.stable_key, '测试', 'evidence', datetime.now(timezone.utc))

    def test_frozen_target_evidence_tamper_is_rejected(self):
        mapping = self.store.one('SELECT * FROM test_answer_copies WHERE test_outbox_id=?', (self.oid,))
        proof = json.loads(mapping['verification_evidence']); proof['account_id'] = 'other-account'
        self.store.execute('UPDATE test_answer_copies SET verification_evidence=? WHERE test_outbox_id=?', (json.dumps(proof), self.oid))
        row = self.store.one('SELECT * FROM outbox WHERE id=?', (self.oid,))
        for fn in (validate_test_copy, reconcile_bound):
            with self.assertRaisesRegex(ValueError, 'TEST_TARGET_INVALID'):
                fn(self.store, row)
        with self.assertRaises(ValueError):
            self.desktop.preflight(self.bound)
        self.assertEqual(self.native.calls, [])

    def test_workflow_delivery_updates_only_test_copy_never_source_or_performance(self):
        flow = Workflow(self.store, desktop=self.desktop)
        self.assertEqual(flow.dispatch(self.oid), 'SENT_UI_CONFIRMED')
        self.assertEqual(self.native.submits, 1)
        self.assertEqual(flow.dispatch(self.oid), 'SENT_UI_CONFIRMED')
        self.assertEqual(queue_test_answer(self.store, self.source_id, self.recipient), self.oid)
        self.assertEqual(self.native.submits, 1)
        self.assertEqual(self.store.one('SELECT state FROM outbox WHERE id=?', (self.source_id,))[0], 'PENDING')
        self.assertEqual(self.fixture.app.context(self.fixture.turn)['sent_history'], [])
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        check = self.store.one('SELECT evidence FROM delivery_checks WHERE outbox_id=? ORDER BY rowid DESC', (self.oid,))
        proof = json.loads(check[0])
        self.assertEqual(proof['target_platform'], 'personal_wechat_test')
        self.assertEqual(proof['target_key'], self.recipient.stable_key)
        self.assertNotEqual(proof['target_key'], self.recipient.room_id)
        self.assertFalse(proof['source_student_delivered'])
        self.assertFalse(proof['server_receipt'])
        self.assertFalse(proof['formal_performance_eligible'])

    def test_unknown_submit_is_durable_and_reconcile_does_not_replay(self):
        self.native.submit_error = True
        flow = Workflow(self.store, desktop=self.desktop)
        self.assertEqual(flow.dispatch(self.oid), 'SEND_UNKNOWN')
        path = self.desktop._path(self.bound)
        self.assertEqual(json.loads(path.read_text())['state'], 'SUBMISSION_UNCONFIRMED')
        self.assertEqual(flow.dispatch(self.oid), 'SEND_UNKNOWN')
        self.assertEqual(flow.inspect_unknown(self.oid), 'SEND_UNKNOWN')
        self.assertEqual(self.native.submits, 1)
        restarted = self.make_desktop()
        self.assertEqual(Workflow(self.store, desktop=restarted).inspect_unknown(self.oid), 'SEND_UNKNOWN')
        self.assertEqual(restarted.transport.submits, 0)

    def test_wrong_copy_or_no_new_bubble_cannot_claim_delivery(self):
        self.native.copy_wrong = True
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'SEND_UNKNOWN')
        self.assertEqual(self.native.submits, 1)
        self.native.copy_wrong = False
        self.native.no_append = True
        count = len(self.native.calls)
        self.assertFalse(self.desktop.reconcile(self.bound)['confirmed'])
        self.assertFalse(any(tool == 'Click' for tool, args in self.native.calls[count:]))

    def test_live_identity_pause_and_wecom_frame_stop_before_typing(self):
        self.identity = False
        with self.assertRaisesRegex(PreflightFailure, 'LIVE_IDENTITY_UNCONFIRMED'):
            self.desktop.preflight(self.bound)
        self.assertEqual(self.native.calls, [])
        self.identity = True
        Workflow(self.store).set_stop(True)
        with self.assertRaisesRegex(PreflightFailure, 'PAUSED'):
            self.desktop.preflight(self.bound)
        Workflow(self.store).set_stop(False)
        self.native.process = 'WXWork'
        with self.assertRaises(PreflightFailure):
            self.desktop.preflight(self.bound)
        self.assertFalse(any(tool in ('Click', 'Type', 'Shortcut') for tool, args in self.native.calls))

    def test_source_stale_before_send_stops_but_readback_after_send_retains_fact(self):
        self.native.after_submit = lambda: self.store.execute(
            "UPDATE questions SET context_revision=context_revision+1 WHERE id=(SELECT question_id FROM turns WHERE id=?)",
            (self.fixture.turn,))
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'SENT_UI_CONFIRMED')
        with self.assertRaisesRegex(ValueError, 'Stale turn'):
            validate_test_copy(self.store, self.store.one('SELECT * FROM outbox WHERE id=?', (self.oid,)))
        self.assertTrue(self.desktop.reconcile(self.bound)['confirmed'])
        self.assertEqual(self.native.submits, 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_missing_journal_is_not_receipt_and_readback_cannot_paste_or_send(self):
        self.store.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (self.oid,))
        self.assertFalse(self.desktop.reconcile(self.bound)['confirmed'])
        self.desktop._reconciling = True
        self.desktop.authorize(self.bound)
        for tool, args in (('Shortcut', {'shortcut': 'ctrl+v'}), ('Click', {'loc': self.pin['send_point']}),
                           ('Clipboard', {'mode': 'set', 'text': self.bound.body})):
            with self.subTest(tool=tool), self.assertRaisesRegex(PreflightFailure, 'READBACK_ONLY'):
                self.desktop._call(tool, **args)
        self.assertEqual(self.native.submits, 0)

    def test_old_identical_bubble_is_not_a_new_receipt(self):
        self.native.sent = True
        self.native.body = self.bound.body
        self.native.after_submit = lambda: None
        # Keep the same sole bubble before/after: exact text alone is insufficient.
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'SEND_UNKNOWN')
        self.assertEqual(self.native.submits, 1)

    def test_personal_image_cannot_be_used_as_wecom_frame(self):
        frame = self.desktop._frame(self.bound)
        with self.assertRaises(PreflightFailure):
            verify_frame(frame['record'], self.pin, foreground=frame['native'])
        with self.assertRaises(PreflightFailure):
            verify_frame(frame['record'], self.pin, expected_process='Weixin')

    def test_changed_send_control_leaves_unsubmitted_draft_without_click(self):
        self.native.button_changed = True
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'FAILED')
        self.assertEqual(self.native.submits, 0)
        self.assertFalse(self.desktop._path(self.bound).exists())
        self.assertEqual(json.loads(self.store.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?',
                                                   (self.oid,))[0])['submission_attempted'], False)

    def test_version_change_before_dispatch_never_prepares_or_sends(self):
        self.store.execute('UPDATE questions SET context_revision=context_revision+1')
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'STALE')
        self.assertEqual(self.native.calls, [])

    def test_manual_account_guard_cannot_override_frozen_account_or_room(self):
        for change in ({'account_id': 'other-account'}, {'target_key': 'other-room@chatroom'}):
            desktop = self.make_desktop(dict(self.pin, **change))
            with self.subTest(change=change), self.assertRaisesRegex(PreflightFailure, 'ACCOUNT_OR_ROOM_MISMATCH'):
                desktop.preflight(self.bound)
            self.assertEqual(desktop.transport.calls, [])

    def test_points_must_be_in_their_reviewed_controls(self):
        for change in ({'send_point': [200, 100]}, {'copy_menu_point': [200, 200]},
                       {'compose_point': [350, 250]}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'POINT_OUTSIDE_CONTROL'):
                self.make_desktop(dict(self.pin, **change))

    def test_fresh_process_unknown_recovery_cannot_resubmit_or_count(self):
        self.native.submit_error = True
        self.assertEqual(Workflow(self.store, desktop=self.desktop).dispatch(self.oid), 'SEND_UNKNOWN')
        pin_path = self.root / 'anonymous-pin.json'
        pin_path.write_text(json.dumps(self.pin), encoding='utf-8')
        script = '''
from contextlib import closing
import json,sys
from pathlib import Path
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from helpdesk.personal_wechat_test_delivery import PersonalWeChatTestDesktop
from tests.test_personal_wechat_test_delivery import ScriptedNative
with closing(Store(sys.argv[1])) as store:
    pin=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    desktop=PersonalWeChatTestDesktop(store,pin,Path(sys.argv[2]).parent,
        account_chat_validator=lambda recipient:True,native_factory=ScriptedNative)
    flow=Workflow(store,desktop=desktop)
    state=flow.inspect_unknown(pin['outbox_id'])
    state2=flow.dispatch(pin['outbox_id'])
    print(json.dumps({'state':state,'second_dispatch':state2,'submits':desktop.transport.submits,
        'performance':store.one('SELECT COUNT(*) FROM performance_units')[0]}))
'''
        child = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script,
                                self.store.path, str(pin_path)], capture_output=True, text=True,
                               timeout=20, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout), {'state': 'SEND_UNKNOWN', 'second_dispatch': 'SEND_UNKNOWN',
                                                  'submits': 0, 'performance': 0})


if __name__ == '__main__':
    unittest.main()
