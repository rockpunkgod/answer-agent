from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.chat_text_archive import archive_clipboard
from helpdesk.native_intake import index_native_records, list_staged_records
from helpdesk.storage import Store


class NativeIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archives = self.root / 'archives'
        self.archives.mkdir()
        self.store = Store(self.root / 'test.db')
        self.addCleanup(self.store.close)

    def archive(self, acquisition='a'*32, group='27S English答疑2群', text='为什么不选B？\r\n[图片]\r\n'):
        result = self.root / (acquisition + '.json')
        attempt = self.root / ('attempt-' + acquisition + '.json')
        result.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
                                     'is_error': False, 'content': [{'type': 'text', 'text': 'Clipboard content:\n'+text}]}), encoding='utf-8')
        attempt.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
                                      'arguments': {'mode': 'get'}, 'status': 'TOOL_RETURNED',
                                      'result_path': str(result), 'started_at': '2026-09-30T03:00:00+00:00'}), encoding='utf-8')
        return archive_clipboard(result, self.archives, observed_group=group)

    def business_counts(self):
        return {table: self.store.one(f'SELECT COUNT(*) FROM {table}')[0]
                for table in ('messages', 'outbox', 'performance_units', 'bindings', 'cases')}

    def test_native_text_staging_preserves_unknown_facts_and_does_not_create_business_rows(self):
        original = '学生 2026-09-17 22:58:00\r\n为什么不选B？\r\n[图片]\r\n'
        self.archive(text=original)
        before = self.business_counts()
        result = index_native_records(self.store, self.archives)
        row, = list_staged_records(self.store)
        self.assertEqual(result['new_records'], 1)
        self.assertEqual(row['original_text'], original)
        self.assertEqual(row['acquired_at'], '2026-09-30T03:00:00+00:00')
        for key in ('sender', 'original_message_time', 'group_id', 'student_identity', 'questions', 'attachments', 'platform_message_id'):
            self.assertIsNone(row[key])
        self.assertFalse(row['reliable_timestamp'])
        self.assertFalse(row['formal_statistics_eligible'])
        self.assertIn('original_message_time', row['missing_metadata'])
        self.assertEqual(before, self.business_counts())

    def test_repeated_scan_is_idempotent_but_new_acquisition_of_identical_text_is_retained(self):
        self.archive()
        self.assertEqual(index_native_records(self.store, self.archives)['new_records'], 1)
        self.assertEqual(index_native_records(self.store, self.archives)['duplicate_acquisitions'], 1)
        self.archive(acquisition='b'*32)
        self.assertEqual(index_native_records(self.store, self.archives)['new_records'], 1)
        rows = list_staged_records(self.store)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['original_text'], rows[1]['original_text'])

    def test_same_acquisition_archived_twice_is_not_a_second_message(self):
        self.archive()
        self.archive()
        result = index_native_records(self.store, self.archives)
        self.assertEqual(result['new_records'], 1)
        self.assertEqual(result['duplicate_acquisitions'], 1)

    def test_case_insensitive_english_scope_includes_exited_group(self):
        self.archive(group='28Y eNgLiSh【答疑3群】（已退出）')
        self.archive(acquisition='b'*32, group='英语答疑老师')
        result = index_native_records(self.store, self.archives)
        self.assertEqual(result['new_records'], 1)
        self.assertEqual(result['excluded_non_english'], 1)
        self.assertIsNone(list_staged_records(self.store)[0]['membership'])

    def test_tampered_raw_and_rehashed_fake_text_are_rejected_without_rows(self):
        folder = self.archive()
        (folder / '原始文字记录.txt').write_text('伪造记录', encoding='utf-8')
        self.assertEqual(len(index_native_records(self.store, self.archives)['rejected']), 1)
        manifest_path = folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['raw_text_sha256'] = sha256((folder / '原始文字记录.txt').read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertEqual(len(index_native_records(self.store, self.archives)['rejected']), 1)
        self.assertEqual(list_staged_records(self.store), [])

    def test_original_journal_tamper_and_manifest_metadata_claim_are_rejected(self):
        folder = self.archive()
        attempt_path = self.root / ('attempt-' + 'a'*32 + '.json')
        attempt_path.write_text('{}', encoding='utf-8')
        result = index_native_records(self.store, self.archives)
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(list_staged_records(self.store), [])
        self.archive(acquisition='b'*32)
        manifest_path = folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['message_timestamps'] = ['2026-09-17T23:00:00+08:00']
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        result = index_native_records(self.store, self.archives)
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['new_records'], 1)

    def test_malformed_provenance_even_with_updated_hash_is_rejected(self):
        folder = self.archive()
        attempt_path = folder / 'clipboard-attempt.json'
        attempt = json.loads(attempt_path.read_text(encoding='utf-8'))
        attempt['arguments'] = {'mode': 'set'}
        attempt_path.write_text(json.dumps(attempt), encoding='utf-8')
        manifest_path = folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['source_attempt_sha256'] = sha256(attempt_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertEqual(len(index_native_records(self.store, self.archives)['rejected']), 1)
        self.assertEqual(list_staged_records(self.store), [])

    def test_dry_run_and_read_only_list_do_not_create_staging_table(self):
        self.archive()
        self.assertEqual(list_staged_records(self.store), [])
        self.assertEqual(index_native_records(self.store, self.archives, dry_run=True)['new_records'], 1)
        self.assertIsNone(self.store.one("SELECT name FROM sqlite_master WHERE name='native_chat_staging'"))

    def test_original_result_tamper_and_raw_path_escape_are_rejected(self):
        folder = self.archive()
        (self.root / ('a'*32+'.json')).write_text('{}', encoding='utf-8')
        self.assertEqual(len(index_native_records(self.store, self.archives)['rejected']), 1)
        self.assertEqual(list_staged_records(self.store), [])
        escaped = self.root / 'escaped.txt'
        escaped.write_text('为什么不选B？\r\n[图片]\r\n', encoding='utf-8')
        manifest_path = folder / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['raw_text_file'] = str(escaped)
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        result = index_native_records(self.store, self.archives)
        self.assertIn('escapes', result['rejected'][0]['reason'])

    def test_acquisition_group_conflict_fails_batch_before_other_valid_insertions(self):
        self.archive()
        index_native_records(self.store, self.archives)
        self.archive(acquisition='b'*32)
        self.archive(group='Other English群')
        with self.assertRaisesRegex(ValueError, 'Conflicting evidence'):
            index_native_records(self.store, self.archives)
        self.assertEqual(len(list_staged_records(self.store)), 1)


if __name__ == '__main__':
    unittest.main()
