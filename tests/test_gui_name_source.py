"""Anonymous provider contracts, not a live native source verification."""
import unittest

from helpdesk.gui_message_source import GUIMessageSource
from helpdesk.message_sources import SyncMode
from helpdesk.wecom_tools import classify_names


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
