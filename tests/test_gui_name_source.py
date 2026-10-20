"""Anonymous provider contracts, not a live native source verification."""
import unittest
import tempfile
from pathlib import Path

from helpdesk.gui_message_source import GUIMessageSource
from helpdesk.message_sources import SyncMode
from helpdesk.wecom_tools import classify_names
from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.service import Helpdesk
from helpdesk.storage import Store


class GUINameSourceTests(unittest.TestCase):
    def fixture(self, *, ambiguous=()):
        group = classify_names('English A', 'Alice')['group_key']
        observation = dict(provenance='official_windows_mcp', room_id=group, room_name='English A',
            foreground=True, desktop_unlocked=True, position_continuity=True, last_position='next',
            messages=[dict(position='1', sender_display_name='Alice', text='Why B?',
                           sender_identity_verified=True, consecutive_header_verified=True)])
        source = GUIMessageSource(lambda **kw: observation, expected_room_id=group,
                                 group_display_name='English A', ambiguous_names=ambiguous)
        return source, observation

    def test_name_keys_match_business_binding_without_platform_id_or_time_guess(self):
        source, observation = self.fixture()
        message = source.fetch_page(None, SyncMode.LIVE, 10).messages[0]
        self.assertEqual(message.sender_id, classify_names('English A', 'Alice')['student_key'])
        self.assertEqual(message.source_confidence, 'high')
        self.assertEqual(message.time_confidence, 'low')
        self.assertIsNone(message.sent_at_local)
        self.assertEqual(message.parse_status, 'pending_time_verification')
        self.assertFalse(message.raw_payload['item']['name_classification']['platform_identity_verified'])
        self.assertNotIn('name_classification', observation['messages'][0])

    def test_ambiguous_name_cannot_be_trusted(self):
        source, _ = self.fixture(ambiguous=['Alice'])
        message = source.fetch_page(None, SyncMode.LIVE, 10).messages[0]
        self.assertEqual(message.source_confidence, 'low')
        self.assertFalse(message.raw_payload['identity_verified'])

    def test_equal_text_from_two_students_remains_distinct(self):
        source, observation = self.fixture()
        observation['messages'].append(dict(observation['messages'][0], position='2', sender_display_name='Bob'))
        first, second = source.fetch_page(None, SyncMode.LIVE, 10).messages
        self.assertNotEqual(first.sender_id, second.sender_id)
        self.assertNotEqual(first.message_id, second.message_id)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_name_alone_does_not_replace_provider_identity_evidence(self):
        source, observation = self.fixture()
        observation['messages'][0]['sender_identity_verified'] = False
        self.assertEqual(source.fetch_page(None, SyncMode.LIVE, 10).messages[0].source_confidence, 'low')
        observation['room_name'] = 'English B'
        with self.assertRaises(ValueError):
            source.fetch_page(None, SyncMode.LIVE, 10)

    def test_wrong_configuration_rejected(self):
        with self.assertRaises(ValueError):
            GUIMessageSource(None, expected_room_id='wrong', group_display_name='English A')

    def test_named_source_replay_queues_one_ack_without_completion_or_performance(self):
        source, observation = self.fixture()
        observation['messages'][0].update(text='老师为什么选B？',
            sent_at='2026-10-20T22:58:00+08:00', full_time_verified=True)
        with tempfile.TemporaryDirectory() as directory:
            raw = CollectorStore(Path(directory) / 'source.db')
            business = Store(Path(directory) / 'business.db')
            try:
                binding = Helpdesk(business).bind_names('English A', 'Alice', verified=True)
                batch = source.fetch_page(None, SyncMode.LIVE, 10)
                raw.persist_batch(source.source_name, SyncMode.LIVE, batch, None)
                dispatch = CollectorDispatcher(raw, business, ack_room_names=['English A'])
                dispatch.drain()
                observation['evidence'] = {'capture': 'second anonymous capture'}
                # A separate adapter instance represents a restarted collector.
                restarted = GUIMessageSource(lambda **kw: observation, expected_room_id=source.room,
                                             group_display_name='English A')
                replay = restarted.fetch_page(None, SyncMode.LIVE, 10)
                raw.persist_batch(source.source_name, SyncMode.LIVE, replay, 'next')
                dispatch.drain()
                rows = business.all('SELECT * FROM outbox')
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['binding_id'], rows[0]['purpose'], rows[0]['body']),
                                 (binding, 'ACK', '收到'))
                self.assertEqual(rows[0]['state'], 'PENDING')
                self.assertEqual(business.one('SELECT COUNT(*) FROM collector_answer_tasks')[0], 1)
                self.assertEqual(business.one('SELECT COUNT(*) FROM performance_units')[0], 0)
                self.assertEqual(business.one('SELECT source_sent_at FROM messages')[0],
                                 '2026-10-20T22:58:00+08:00')
            finally:
                business.close()

    def test_name_binding_does_not_allow_missing_time_or_teacher_ack(self):
        for teacher in (False, True):
            with self.subTest(teacher=teacher), tempfile.TemporaryDirectory() as directory:
                source, observation = self.fixture()
                item = observation['messages'][0]
                item['text'] = '老师为什么选B？'
                if teacher:
                    item.update(sender_role='teacher', sent_at='2026-10-20T23:10:00+08:00', full_time_verified=True)
                raw = CollectorStore(Path(directory) / 'source.db')
                business = Store(Path(directory) / 'business.db')
                try:
                    Helpdesk(business).bind_names('English A', 'Alice', verified=True)
                    raw.persist_batch(source.source_name, SyncMode.LIVE,
                                      source.fetch_page(None, SyncMode.LIVE, 10), None)
                    CollectorDispatcher(raw, business).drain()
                    self.assertEqual(business.one('SELECT COUNT(*) FROM outbox')[0], 0)
                    task = business.one('SELECT * FROM collector_answer_tasks')
                    self.assertEqual(task['state'], 'ACK_HELD')
                    self.assertEqual(task['reason'], 'TEACHER_MESSAGE_ACK_FORBIDDEN' if teacher
                                     else 'ORIGINAL_TIME_REQUIRES_REVIEW_ACK_HELD')
                finally:
                    business.close()
