import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from helpdesk.chat_text_archive import archive_clipboard
from helpdesk.native_export import export_native_records


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'archives'
        self.root.mkdir()
        self.output = self.base / 'export'

    def archive(self, acquisition='a' * 32, group='eNgLiSh答疑群（已退出）'):
        text = '为什么不选B？\r\n[图片]\r\n'
        result = self.base / (acquisition + '.json')
        result.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
                                     'is_error': False, 'content': [{'type': 'text',
                                     'text': 'Clipboard content:\n' + text}]}), encoding='utf-8')
        attempt = self.base / ('attempt-' + acquisition + '.json')
        attempt.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
                                      'arguments': {'mode': 'get'}, 'status': 'TOOL_RETURNED',
                                      'started_at': '2026-09-30T03:00:00+00:00',
                                      'result_path': str(result)}), encoding='utf-8')
        return archive_clipboard(result, self.root, observed_group=group)

    def test_preserves_repeated_acquisitions_native_bytes_unknown_metadata_and_order(self):
        folders = [self.archive(), self.archive(), self.archive('b' * 32, '其他群')]
        result = export_native_records(self.root, self.output)
        self.assertEqual(result['collection_count'], 2)
        self.assertIsNone(result['question_count'])
        manifest = json.loads((self.output / '导出清单.json').read_bytes())
        self.assertEqual(manifest['coverage'], 'partial')
        self.assertFalse(manifest['all_history_exported'])
        self.assertFalse(manifest['scope']['date_filter_applied'])
        self.assertEqual([r['collection_id'] for r in manifest['records']],
                         sorted(f.name for f in folders[:2]))
        with ZipFile(self.output / '原文与采集凭据.zip') as archive:
            for record in manifest['records']:
                self.assertIsNone(record['original_sender'])
                self.assertIsNone(record['original_message_time'])
                self.assertIsNone(record['question_count'])
                raw_name = next(n for n in record['package_files'] if n.endswith('/原始文字记录.txt'))
                self.assertEqual(archive.read(raw_name), '为什么不选B？\r\n[图片]\r\n'.encode('utf-8'))
                self.assertEqual(len(record['package_files']), 6)
        first = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertTrue(export_native_records(self.root, self.output)['reused_identical_export'])
        other = self.base / 'another_export'
        export_native_records(self.root, other)
        self.assertEqual(first, {p.name: p.read_bytes() for p in other.iterdir()})

    def test_tampered_hash_or_original_journal_fails_before_output(self):
        folder = self.archive()
        raw = folder / '原始文字记录.txt'
        original = raw.read_bytes()
        raw.write_bytes(b'fake')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            export_native_records(self.root, self.output)
        self.assertFalse(self.output.exists())
        raw.write_bytes(original)
        (self.base / ('a' * 32 + '.json')).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'journal'):
            export_native_records(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_unqualified_source_and_existing_output_are_rejected(self):
        folder = self.archive()
        manifest_path = folder / 'manifest.json'
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        manifest['source_kind'] = 'SCREENSHOT_OCR'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unreviewed native'):
            export_native_records(self.root, self.output)
        self.assertFalse(self.output.exists())
        manifest_path.write_bytes(original)
        self.output.mkdir()
        sentinel = self.output / '已有文件.txt'
        sentinel.write_bytes(b'keep me')
        with self.assertRaises(FileExistsError):
            export_native_records(self.root, self.output)
        self.assertEqual(sentinel.read_bytes(), b'keep me')

    def test_changed_sources_never_overwrite_prior_export(self):
        self.archive()
        export_native_records(self.root, self.output)
        prior = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.archive('b' * 32)
        with self.assertRaises(FileExistsError):
            export_native_records(self.root, self.output)
        self.assertEqual(prior, {p.name: p.read_bytes() for p in self.output.iterdir()})
        with self.assertRaisesRegex(ValueError, 'separate'):
            export_native_records(self.root, self.root / 'exports')


if __name__ == '__main__':
    unittest.main()
