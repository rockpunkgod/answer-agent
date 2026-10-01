"""Outbox-linked manual drafts/readback; all native evidence and actors are fake."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.delivery import PreflightFailure
from tests import test_manual_group_draft as draft_fixture
from tests import test_answer_review_packets_acceptance as packet_fixture
from tests import test_live_generation as generation_fixture


class ManualDeliveryReconciliationTests(unittest.TestCase):
    write_manifest = packet_fixture.AnswerPacketAcceptanceTests.write_manifest
    make_draft = draft_fixture.ManualDraftTests.make_draft
    assert_never_sent = draft_fixture.ManualDraftTests.assert_never_sent

    def setUp(self):
        packet_fixture.AnswerPacketAcceptanceTests.setUp(self)
        self.db = Store(self.base / 'isolated-business.db')
        self.addCleanup(self.db.close)
        app = self.app = Helpdesk(self.db)
        self.binding = app.bind('fixture-platform-group-id', 'fixture-platform-student-id', '学生甲', verified=True)
        self.original = app.ingest(Incoming(self.binding, 'fixture original question', Intent.NEW,
            source='isolated-wecom-fixture', verified_question=demo_question(),
            raw_material='fixture passage', verified_material='fixture passage'))
        helper = generation_fixture.LiveGenerationBoundaryTests()
        helper.db, helper.app, helper.turn = self.db, app, self.original.turn_id
        helper.base = self.base
        helper.skill = self.base / 'fixture-course.md'
        helper.skill.write_text('# approved course fixture\n', encoding='utf-8')
        helper.manifest = self.base / 'fixture-course-manifest.json'
        helper.manifest.write_text('{}', encoding='utf-8')
        adapter = generation_fixture.StubAdapter()
        flow, rid = helper.start(adapter)
        self.flow = flow
        flow.set_answer_review_required(False)
        flow.set_manual_send(True)
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (rid,))[0])
        generated = adapter.generate(snapshot)
        generated['text'] = self.text
        self.oid = flow.finish(rid, generated)['outbox_id']
        self.draft, self.transport, _ = self.make_draft()
        row = self.db.one('SELECT * FROM outbox WHERE id=?', (self.oid,))
        self.draft.pin.update({key: row[key] for key in ('binding_id', 'run_id', 'answer_id', 'turn_id',
            'message_id', 'case_id', 'question_version', 'context_revision')})
        self.draft.pin.update(outbox_id=self.oid, group_key='fixture-platform-group-id',
                              student_key='fixture-platform-student-id', identity_verified=True,
                              teacher_sender_id='fixture-platform-teacher-id')
        self.identity = {'platform': 'wecom', 'method': 'persistent_platform_ids', 'verified': True,
                         **{key: self.draft.pin[key] for key in ('binding_id', 'group_key', 'student_key',
                                                               'group_observed', 'student_observed')},
                         'teacher_sender_ids': ['fixture-platform-teacher-id']}
        self.draft.pin['identity_evidence'] = self.write_evidence('fixture-identity.json', self.identity)

    def write_evidence(self, name, record):
        path = self.base / name
        path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        return {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()}

    def persisted(self):
        return list(self.db.connection.iterdump())

    def stage(self):
        return self.draft.stage_outbox(self.db, self.oid)

    def negative_display(self):
        self.draft.pin.update(screenshot_display=1, display_origin=[0, -200],
                              compose_point=[50, -130], window_rect=[0, -200, 200, 120])
        call = self.transport.call
        self.foreground_top = -200
        def observed(tool, args, **kwargs):
            result = call(tool, args, **kwargs)
            if tool == 'PowerShell':
                foreground = {'process': 'WXWork', 'handle': 42, 'width': 200, 'height': 120,
                              'left': 0, 'top': self.foreground_top}
                result['content'] = [{'type': 'text', 'text': 'Response: ' + json.dumps(foreground) + '\nStatus Code: 0'}]
            return result
        self.transport.call = observed

    def gui_identity(self):
        self.negative_display()
        self.draft.pin['source_message_locator'] = 'isolated-fixture:original-source-locator'
        fields = ('verified_at', 'expires_at', 'window_geometry_handle', 'window_rect',
                  'header_hash', 'avatar_hash', 'screenshot_display', 'display_origin', 'source_message_locator')
        self.identity.update(method='verified_gui_session', platform_identity_claimed=False,
                             source_message_id=self.original.message_id,
                             **{key: self.draft.pin[key] for key in fields})
        frame = self.base / 'fixture-original-source-frame.png'
        frame.write_bytes((self.base / 'wecom.png').read_bytes())
        source = {'kind': 'GUI_STUDENT_SOURCE', 'platform': 'wecom', 'message_id': self.original.message_id,
                  'message_locator': self.draft.pin['source_message_locator'],
                  'body_hash': sha256(self.db.one('SELECT raw_text FROM messages WHERE id=?',
                                                (self.original.message_id,))[0].encode()).hexdigest(),
                  'observed_at': datetime.fromtimestamp(self.draft.pin['verified_at'], timezone.utc).isoformat(),
                  'screenshot_path': str(frame), 'screenshot_sha256': sha256(frame.read_bytes()).hexdigest(),
                  **{key: self.identity[key] for key in ('group_observed', 'student_observed', 'header_hash',
                      'avatar_hash', 'window_geometry_handle', 'window_rect', 'screenshot_display', 'display_origin')}}
        self.identity['source_evidence'] = self.write_evidence('fixture-gui-source.json', source)
        self.draft.pin['identity_evidence'] = self.write_evidence('fixture-gui-identity.json', self.identity)

    def observer(self, *, wrong_body=False, wrong_target=False, role='TEACHER', corrupt_hash=False):
        self.sent = datetime.now(timezone.utc).isoformat()
        def observe(message, pin):
            # This fixture represents native copying from an observed sent bubble;
            # no desktop, clipboard, submission or real platform is touched.
            acquired = datetime.now(timezone.utc).isoformat()
            acquisition = 'b' * 32
            copied = {'attempt_id': acquisition, 'tool': 'Clipboard', 'is_error': False,
                      'content': [{'type': 'text', 'text': 'Clipboard content:\n' +
                                  ('shortened fixture' if wrong_body else message.body)}]}
            result = self.write_evidence('fixture-bubble-result.json', copied)
            attempt = {'attempt_id': acquisition, 'tool': 'Clipboard', 'arguments': {'mode': 'get'},
                       'status': 'TOOL_RETURNED', 'result_path': result['path'], 'started_at': acquired}
            value, _ = self.draft._authority(self.db, self.oid, readback=True)
            observation = {**value, 'kind': 'VISIBLE_SENT_BUBBLE', 'visible': True, 'platform': 'wecom',
                           'sender_role': role, 'sender_id': 'fixture-platform-teacher-id',
                           'message_locator': 'isolated-fixture:bubble-id', 'clipboard_attempt_id': acquisition,
                           'sent_at': self.sent, 'observed_at': datetime.now(timezone.utc).isoformat()}
            if wrong_target: observation['group_key'] = 'wrong-group'
            archive = self.write_evidence('fixture-bubble-observation.json', observation)
            if corrupt_hash: archive['sha256'] = '0' * 64
            return {'observation': archive, 'clipboard_result': result,
                    'clipboard_attempt': self.write_evidence('fixture-bubble-attempt.json', attempt)}
        self.transport.observe_sent_bubble = observe

    def test_authoritative_stage_keeps_original_outbox_and_zero_delivery_performance(self):
        before = self.persisted()
        result = self.stage()
        self.assertEqual(result['status'], 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND')
        self.assertEqual(result['outbox_id'], self.oid)
        self.assertEqual(self.transport.editor, self.text)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assert_never_sent(self.transport)
        journal = json.loads(Path(result['journal']).read_bytes())
        self.assertEqual(journal['authority']['outbox_id'], self.oid)
        calls = len(self.transport.calls)
        self.assertEqual(self.stage()['status'], 'PRIOR_DRAFT_ATTEMPT_EXISTS')
        self.assertEqual(len(self.transport.calls), calls)

    def test_wrong_authority_fields_stop_before_any_input(self):
        for field in ('binding_id', 'group_key', 'student_key', 'outbox_id', 'run_id', 'answer_id',
                      'turn_id', 'question_version', 'context_revision', 'body_hash'):
            with self.subTest(field=field):
                original = deepcopy(self.draft.pin)
                self.draft.pin[field] = 'wrong-fixture-field'
                with self.assertRaises((ValueError, PreflightFailure)):
                    self.stage()
                self.assertEqual(self.transport.calls, [])
                self.draft.pin = original
        self.flow.set_stop(True)
        with self.assertRaises(PreflightFailure): self.stage()
        self.assertEqual(self.transport.calls, [])

    def test_explicit_negative_display_uses_local_editor_and_pinned_window_rect(self):
        self.negative_display()
        self.assertEqual(self.stage()['status'], 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND')
        self.assertTrue(all(args == {'display': [1]} for tool, args, _ in self.transport.calls if tool == 'Screenshot'))
        self.assertTrue(all(args['loc'] == [50, -130] for tool, args, _ in self.transport.calls if tool == 'Click'))
        self.assertFalse(any(tool == 'Type' or args.get('shortcut') == 'enter' for tool, args, _ in self.transport.calls))

    def test_window_relocation_rejects_explicit_display_pin_before_paste(self):
        self.negative_display()
        self.foreground_top = -190
        with self.assertRaises(PreflightFailure): self.stage()
        self.assertFalse(any(tool in ('Click', 'Shortcut', 'Clipboard') for tool, _, _ in self.transport.calls))

    def test_verified_gui_identity_with_bounded_source_evidence_can_stage(self):
        self.gui_identity()
        self.assertEqual(self.stage()['status'], 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND')
        journal = json.loads(self.draft.journal.read_bytes())
        self.assertFalse(journal['platform_identity_claimed'])

    def test_gui_identity_missing_source_expired_or_wrong_locator_rejects(self):
        self.gui_identity()
        initial = deepcopy(self.draft.pin)
        for changes in ({'source_message_locator': 'wrong-locator'}, {'expires_at': 0}):
            with self.subTest(changes=changes):
                self.draft.pin = {**deepcopy(initial), **changes}
                with self.assertRaises((ValueError, PreflightFailure)): self.stage()
                self.assertEqual(self.transport.calls, [])
        self.draft.pin = initial
        self.identity.pop('source_evidence')
        self.draft.pin['identity_evidence'] = self.write_evidence('fixture-gui-identity.json', self.identity)
        with self.assertRaises((ValueError, PreflightFailure)): self.stage()
        self.assertEqual(self.transport.calls, [])

    def test_auto_stage_and_changed_version_rejected_before_input(self):
        self.flow.set_manual_send(False)
        with self.assertRaises(PreflightFailure): self.stage()
        self.flow.set_manual_send(True)
        self.app.ingest(Incoming(self.binding, 'fixture correction', Intent.CORRECTION,
                                 quote_message_id=self.original.message_id,
                                 verified_question=demo_question(stem='changed fixture stem')))
        with self.assertRaises((ValueError, PreflightFailure)): self.stage()
        self.assertEqual(self.transport.calls, [])

    def test_mid_paste_correction_is_uncertain_and_never_replayed(self):
        call = self.transport.call
        def mutate(tool, args, **kwargs):
            result = call(tool, args, **kwargs)
            if tool == 'Shortcut' and args['shortcut'] == 'ctrl+v':
                self.db.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?',
                                (self.original.question_id,))
            return result
        self.transport.call = mutate
        with self.assertRaises((ValueError, PreflightFailure)): self.stage()
        self.assertEqual(json.loads(self.draft.journal.read_bytes())['status'], 'DRAFT_OUTCOME_UNCONFIRMED')
        pastes = sum(t == 'Shortcut' and a.get('shortcut') == 'ctrl+v' for t, a, _ in self.transport.calls)
        with self.assertRaises(ValueError): self.stage()
        self.assertEqual(sum(t == 'Shortcut' and a.get('shortcut') == 'ctrl+v' for t, a, _ in self.transport.calls), pastes)
        self.assert_never_sent(self.transport)

    def test_uncertain_authoritative_paste_is_not_replayed_on_restart(self):
        pin = deepcopy(self.draft.pin)
        draft, transport, _ = self.make_draft(timeout_after_paste=True)
        draft.pin = pin
        with self.assertRaises(TimeoutError): draft.stage_outbox(self.db, self.oid)
        calls = len(transport.calls)
        restarted = type(draft)(transport, pin, self.base, self.root)
        self.assertEqual(restarted.stage_outbox(self.db, self.oid)['previous_status'], 'DRAFT_OUTCOME_UNCONFIRMED')
        self.assertEqual(len(transport.calls), calls)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assert_never_sent(transport)

    def test_changed_body_source_or_context_rejected_before_input(self):
        for sql, params in (
            ('UPDATE outbox SET body=? WHERE id=?', ('changed body', self.oid)),
            ('UPDATE messages SET raw_text=? WHERE id=?', ('changed source', self.original.message_id)),
            ('UPDATE questions SET context_revision=context_revision+1 WHERE id=?', (self.original.question_id,)),
        ):
            with self.subTest(sql=sql):
                self.db.execute('SAVEPOINT fixture_change')
                try:
                    self.db.execute(sql, params)
                    with self.assertRaises((ValueError, PreflightFailure)): self.stage()
                    self.assertEqual(self.transport.calls, [])
                finally:
                    self.db.execute('ROLLBACK TO fixture_change')
                    self.db.execute('RELEASE fixture_change')

    def test_missing_observer_blank_editor_or_claimed_success_never_confirms(self):
        self.stage()
        before = self.persisted()
        self.transport.editor = ''
        self.assertEqual(self.draft.readback_outbox(self.db, self.oid)['reason'], 'RECEIPT_OBSERVER_UNAVAILABLE')
        self.transport.observe_sent_bubble = lambda *_: {'confirmed': True, 'editor_blank': True}
        self.assertFalse(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        self.assertEqual(self.persisted(), before)
        self.assert_never_sent(self.transport)

    def test_readback_requires_prior_authoritative_journal_and_current_pin(self):
        self.observer()
        self.assertFalse(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        self.assertEqual(self.transport.calls, [])
        self.stage()
        self.draft.pin['expires_at'] = 0
        calls = len(self.transport.calls)
        self.assertFalse(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        self.assertEqual(len(self.transport.calls), calls)

    def test_actual_fixture_bubble_readback_returns_evidence_without_business_writes(self):
        self.stage()
        before = self.persisted()
        self.observer()
        evidence = self.draft.readback_outbox(self.db, self.oid)
        self.assertTrue(evidence['confirmed'], evidence)
        self.assertEqual(evidence['confirmed_at'], self.sent)
        self.assertEqual(evidence['body_hash'], sha256(self.text.encode()).hexdigest())
        self.assertEqual(evidence['outbox_id'], self.oid)
        self.assertTrue(all(Path(path).is_file() for path in evidence['evidence_paths']))
        self.assertEqual(self.persisted(), before)
        self.assert_never_sent(self.transport)

    def test_stop_and_disabled_stage_allow_only_historical_readback(self):
        self.stage()
        self.flow.set_stop(True)
        self.flow.set_delivery_policy({'ACK': 'DISABLED', 'ANSWER': 'DISABLED', 'CORRECTION': 'DISABLED'}, 'fixture disabled')
        self.observer()
        self.assertTrue(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        with self.assertRaises(PreflightFailure): self.stage()
        self.assert_never_sent(self.transport)

    def test_old_version_sent_bubble_remains_observable_but_not_stageable(self):
        self.stage()
        self.app.ingest(Incoming(self.binding, 'fixture correction', Intent.CORRECTION,
                                 quote_message_id=self.original.message_id,
                                 verified_question=demo_question(stem='changed fixture stem')))
        self.observer()
        self.assertTrue(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        with self.assertRaises((ValueError, PreflightFailure)): self.stage()
        self.assert_never_sent(self.transport)

    def test_wrong_receipt_target_role_hash_and_truncated_text_never_confirm(self):
        self.stage()
        for options in ({'wrong_body': True}, {'wrong_target': True}, {'role': 'STUDENT'}, {'corrupt_hash': True}):
            with self.subTest(options=options):
                self.observer(**options)
                self.assertFalse(self.draft.readback_outbox(self.db, self.oid)['confirmed'])
        self.assert_never_sent(self.transport)


if __name__ == '__main__':
    unittest.main()
