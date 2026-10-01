"""Native-answer handoff preserves raw text and never replaces a prior packet."""
from hashlib import sha256
import json
import unittest

from helpdesk.answer_review_packets import create_review_packet, list_review_packets, ReviewPacketError
from tests import test_answer_review_packets_acceptance as acceptance


class ReviewImportTests(unittest.TestCase):
    setUp = acceptance.AnswerPacketAcceptanceTests.setUp
    write_manifest = acceptance.AnswerPacketAcceptanceTests.write_manifest

    def create(self, name='copied34', result=None):
        return create_review_packet(self.base / 'imported', name, self.packet / 'request.json',
                                    result or self.result, self.attempt,
                                    self.manifest['session_url'])

    def test_import_preserves_original_and_duplicate_cannot_overwrite(self):
        original = sha256(self.result.read_bytes()).hexdigest()
        first = self.create()
        self.assertEqual(first['text'], self.text)
        self.assertFalse(first['actual_delivery_confirmed'])
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(FileExistsError):
            self.create('renamed34')
        records = list_review_packets(self.base / 'imported')['packets']
        self.assertEqual(records, [first])
        self.assertEqual(sha256(self.result.read_bytes()).hexdigest(), original)

    def test_forged_copy_not_published_and_valid_packet_remains(self):
        first = self.create()
        forged = json.loads(self.result.read_bytes())
        forged['content'][0]['text'] += '改写内容'
        source = self.base / 'forged.json'
        source.write_text(json.dumps(forged, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(ReviewPacketError):
            self.create('forged34', source)
        self.assertFalse((self.base / 'imported/forged34').exists())
        self.assertEqual(list_review_packets(self.base / 'imported')['packets'], [first])


if __name__ == '__main__':
    unittest.main()
