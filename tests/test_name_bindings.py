"""Local name grouping persists without promoting identity or producing sends."""
from pathlib import Path
import tempfile
import unittest

from helpdesk.service import Helpdesk
from helpdesk.storage import Store
from helpdesk.wecom_tools import classify_names


class NameBindingTests(unittest.TestCase):
    def test_restart_reuses_binding_and_separates_groups_and_renames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'anonymous.db'
            store = Store(path)
            service = Helpdesk(store)
            first = service.bind_names('English A', 'Alice')
            self.assertNotEqual(first, service.bind_names('English B', 'Alice'))
            self.assertNotEqual(first, service.bind_names('English A', 'Alice renamed'))
            expected = classify_names('English A', 'Alice')
            row = store.one('SELECT * FROM bindings WHERE id=?', (first,))
            self.assertEqual((row['group_key'], row['student_key']),
                             (expected['group_key'], expected['student_key']))
            self.assertEqual(row['verified'], 0)
            store.close()
            store = Store(path)
            try:
                self.assertEqual(first, Helpdesk(store).bind_names(' English A ', ' Alice ', verified=True))
                self.assertEqual(store.one('SELECT verified FROM bindings WHERE id=?', (first,))[0], 0)
                self.assertEqual(store.one('SELECT COUNT(*) FROM outbox')[0], 0)
            finally:
                store.close()

    def test_known_ambiguity_or_missing_name_creates_no_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'anonymous.db')
            try:
                service = Helpdesk(store)
                with self.assertRaisesRegex(ValueError, 'NAME_REQUIRES_REVIEW'):
                    service.bind_names('English A', 'Alice', ambiguous_names=['Alice'])
                with self.assertRaises(ValueError):
                    service.bind_names('English A', '')
                self.assertEqual(store.one('SELECT COUNT(*) FROM bindings')[0], 0)
            finally:
                store.close()
