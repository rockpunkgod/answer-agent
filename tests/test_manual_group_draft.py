"""Draft-only delivery preserves paragraphs and never submits or replays a paste."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import time
import unittest

from PIL import Image

from helpdesk.delivery import PreflightFailure
from helpdesk.manual_group_draft import ManualGroupDraft
from helpdesk.mcp_test_delivery import pixel_hash
from tests import test_answer_review_packets_acceptance as acceptance


class ManualDraftTests(unittest.TestCase):
    setUp = acceptance.AnswerPacketAcceptanceTests.setUp
    write_manifest = acceptance.AnswerPacketAcceptanceTests.write_manifest

    def make_draft(self, *, wrong_process=False, occupied=False, timeout_after_paste=False,
                   wrong_readback=False, changed_target=False):
        frame = self.base / 'wecom.png'
        image = Image.new('RGB', (200, 120), 'white')
        image.paste('red', (0, 0, 70, 20)); image.paste('blue', (75, 0, 95, 20))
        image.save(frame)
        from helpdesk.answer_review_packets import list_review_packets
        packet = list_review_packets(self.root)['packets'][0]
        pin = {'platform': 'wecom', 'target_kind': 'group', 'group_observed': packet['group_observed'],
            'student_observed': packet['student_observed'], 'packet_id': packet['packet_id'],
            'body_hash': packet['answer_sha256'], 'native_copy_acquisition_id': packet['native_copy_acquisition_id'],
            'source_evidence': 'executor observed original group and source message',
            'verified_at': time.time()-1, 'expires_at': time.time()+120,
            'window_geometry_handle': [200, 120, 42], 'image_size': [200, 120],
            'header_box': [0, 0, 70, 20], 'avatar_box': [75, 0, 95, 20], 'editor_box': [10, 30, 190, 110],
            'header_hash': pixel_hash(image, [0, 0, 70, 20]), 'avatar_hash': pixel_hash(image, [75, 0, 95, 20]),
            'compose_point': [50, 70]}
        class Transport:
            def __init__(self):
                self.calls = []; self.editor = 'existing user draft' if occupied else ''; self.clipboard = ''
            def call(self, tool, args, **kwargs):
                self.calls.append((tool, deepcopy(args), kwargs))
                result = {'tool': tool, 'is_error': False, 'content': []}
                if tool == 'PowerShell':
                    foreground = {'process': 'WeChat' if wrong_process else 'WXWork', 'handle': 42,
                        'width': 200, 'height': 120, 'left': 0, 'top': 0}
                    result['content'] = [{'type': 'text', 'text': 'Response: '+json.dumps(foreground)+'\nStatus Code: 0'}]
                elif tool == 'Screenshot':
                    current = image.copy()
                    if self.editor: current.paste('black', (25, 40, 75, 50))
                    if changed_target and self.editor: current.paste('green', (0, 0, 70, 20))
                    current.save(frame)
                    result['content'] = [{'type': 'image', 'path': str(frame)}]
                elif tool == 'Clipboard':
                    if args['mode'] == 'set': self.clipboard = args['text']
                    else: result['content'] = [{'type': 'text', 'text': 'Clipboard content:\n'+self.clipboard}]
                elif tool == 'Shortcut':
                    if args['shortcut'] == 'ctrl+v':
                        self.editor = self.clipboard
                        if timeout_after_paste: raise TimeoutError('paste outcome uncertain')
                    elif args['shortcut'] == 'ctrl+c': self.clipboard = 'shortened' if wrong_readback else self.editor
                return result
        transport = Transport()
        return ManualGroupDraft(transport, pin, self.base, self.root), transport, packet

    def assert_never_sent(self, transport):
        self.assertFalse(any(tool == 'Type' for tool, _, _ in transport.calls))
        self.assertTrue(all(args.get('shortcut') in ('ctrl+v', 'ctrl+a', 'ctrl+c', 'right')
                            for tool, args, _ in transport.calls if tool == 'Shortcut'))
        self.assertTrue(all(args['loc'] == [50, 70] for tool, args, _ in transport.calls if tool == 'Click'))
        self.assertTrue(all(kw == {'expected_foreground_process': 'WXWork'}
                            for tool, _, kw in transport.calls if tool in ('Click', 'Shortcut')))

    def test_original_multiline_draft_is_verified_without_send_and_never_replayed(self):
        draft, transport, packet = self.make_draft()
        result = draft.stage(packet['packet_id'])
        self.assertEqual(transport.editor, self.text)
        self.assertEqual(result['status'], 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND')
        self.assertFalse(result['answer_sent']); self.assertFalse(result['formal_performance_eligible'])
        self.assert_never_sent(transport)
        calls = len(transport.calls)
        again = draft.stage(packet['packet_id'])
        self.assertEqual(again['status'], 'PRIOR_DRAFT_ATTEMPT_EXISTS')
        self.assertFalse(again['current_draft_verified']); self.assertEqual(len(transport.calls), calls)
        evidence = json.loads(Path(result['journal']).read_bytes())
        self.assertEqual(evidence['readback_sha256'], sha256(self.text.encode()).hexdigest())

    def test_wrong_application_or_occupied_editor_preserves_existing_input(self):
        for mode in ('wrong_process', 'occupied'):
            with self.subTest(mode=mode):
                draft, transport, packet = self.make_draft(**{mode: True})
                with self.assertRaises((PreflightFailure, ValueError)): draft.stage(packet['packet_id'])
                self.assertFalse(any(t in ('Clipboard', 'Shortcut', 'Click', 'Type') for t, _, _ in transport.calls))
                if mode == 'occupied': self.assertEqual(transport.editor, 'existing user draft')
                # Use a new isolated workspace for the next scenario.
                self.tearDown_fixture()

    def tearDown_fixture(self):
        self.doCleanups(); self.setUp()

    def test_group_binding_mismatch_rejects_before_desktop(self):
        draft, transport, packet = self.make_draft()
        draft.pin['group_observed'] = 'another English group'
        with self.assertRaises(PreflightFailure): draft.stage(packet['packet_id'])
        self.assertEqual(transport.calls, [])

    def test_paste_timeout_is_durable_and_does_not_paste_again(self):
        draft, transport, packet = self.make_draft(timeout_after_paste=True)
        with self.assertRaises(TimeoutError): draft.stage(packet['packet_id'])
        self.assertEqual(transport.editor, self.text)
        calls = len(transport.calls)
        again = draft.stage(packet['packet_id'])
        self.assertEqual(again['previous_status'], 'DRAFT_OUTCOME_UNCONFIRMED')
        self.assertEqual(len(transport.calls), calls)
        self.assert_never_sent(transport)

    def test_truncated_readback_or_switched_group_never_claims_verified_draft(self):
        for mode in ('wrong_readback', 'changed_target'):
            with self.subTest(mode=mode):
                draft, transport, packet = self.make_draft(**{mode: True})
                with self.assertRaises((PreflightFailure, ValueError)): draft.stage(packet['packet_id'])
                journal = json.loads(draft.journal.read_bytes())
                self.assertEqual(journal['status'], 'DRAFT_OUTCOME_UNCONFIRMED')
                self.assertFalse(journal['answer_sent']); self.assertFalse(journal['automatic_retry_allowed'])
                self.assert_never_sent(transport)
                self.tearDown_fixture()

    def test_editor_point_must_be_inside_pinned_editor_and_pin_is_short_lived(self):
        draft, transport, packet = self.make_draft()
        for change in ({'compose_point': [5, 5]}, {'expires_at': time.time()+1000}):
            with self.subTest(change=change):
                original = deepcopy(draft.pin); draft.pin.update(change)
                with self.assertRaises(PreflightFailure): draft.stage(packet['packet_id'])
                self.assertEqual(transport.calls, [])
                draft.pin = original


if __name__ == '__main__':
    unittest.main()
