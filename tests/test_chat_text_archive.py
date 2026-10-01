from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.chat_text_archive import archive_clipboard


class ChatTextArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.result = self.root / 'result.json'
        self.attempt = self.root / ('attempt-' + 'a' * 32 + '.json')
        self.text = '学生 2026-09-17 22:58:00\r\n为什么不选B？\r\n[图片]\r\n'
        self.record = {'attempt_id': 'a' * 32, 'tool': 'Clipboard', 'is_error': False,
                       'content': [{'type': 'text', 'text': 'Clipboard content:\n' + self.text}]}
        self.provenance = {'attempt_id': 'a' * 32, 'tool': 'Clipboard', 'arguments': {'mode': 'get'},
                           'status': 'TOOL_RETURNED', 'result_path': str(self.result),
                           'started_at': '2026-09-30T03:00:00+00:00'}
        self.save()

    def save(self):
        self.result.write_text(json.dumps(self.record), encoding='utf-8')
        self.attempt.write_text(json.dumps(self.provenance), encoding='utf-8')

    def archive(self):
        return archive_clipboard(self.result, self.root / 'output', observed_group='测试群')

    def test_verbatim_text_and_provenance_without_time_or_count_inference(self):
        first = self.archive()
        second = self.archive()
        self.assertNotEqual(first, second)
        for folder in (first, second):
            self.assertEqual((folder / '原始文字记录.txt').read_bytes(), self.text.encode('utf-8'))
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['raw_text_sha256'], sha256(self.text.encode('utf-8')).hexdigest())
            self.assertIsNone(manifest['message_timestamps'])
            self.assertFalse(manifest['formal_statistics_eligible'])
            self.assertFalse(manifest['coverage_complete'])

    def test_screenshot_or_snapshot_text_is_not_a_native_export(self):
        for tool in ('Snapshot', 'Screenshot', 'OCR'):
            self.record['tool'] = tool
            self.save()
            with self.assertRaises(ValueError):
                self.archive()
        self.assertFalse((self.root / 'output').exists())

    def test_wrong_attempt_or_failed_result_cannot_be_archived(self):
        self.provenance['arguments'] = {'mode': 'set', 'text': self.text}
        self.save()
        with self.assertRaises(ValueError):
            self.archive()
        self.provenance['arguments'] = {'mode': 'get'}
        self.record['is_error'] = True
        self.save()
        with self.assertRaises(ValueError):
            self.archive()
        self.assertFalse((self.root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
