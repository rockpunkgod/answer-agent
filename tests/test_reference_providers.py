"""Local files and mocked HTTP; provider capture cannot confirm or send anything."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpdesk.reference_fetch import Budget, HTTPResult, LookupFailure, SourceAdmission
from helpdesk.reference_lookup import LookupConfig
from helpdesk.reference_providers import HttpProvider, LocalProvider
from tools.crawl_reference import capture_candidate, main


class ReferenceProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.approved = self.root / 'approved';self.approved.mkdir()
        self.file = self.approved / 'question.html';self.file.write_text('<p>Anonymous candidate text.</p>', encoding='utf-8')
        self.source = SourceAdmission('example.org', automatic_enabled=True, terms_status='APPROVED',
            terms_url='https://example.org/terms', robots_status='REVIEWED', checked_at='2026-10-01T00:00:00+00:00',
            allowed_paths=('/question',), retention_seconds=3600, business_record_storage_allowed=True)
        self.config = LookupConfig(enabled=True, network_enabled=True, fixture_root=self.approved,
            cache_root=self.root / 'cache', admissions=(self.source,))

    def test_local_capture_has_normalized_provenance_and_content_hash(self):
        output = LocalProvider(self.approved).capture('question.html', Budget())
        self.assertEqual(set(output), {'source', 'url', 'content', 'retrieved_time', 'hash'})
        self.assertEqual(output['hash'], sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(output['content'], self.file.read_text(encoding='utf-8'))
        self.assertTrue(output['retrieved_time'])
        self.assertNotIn('confirmed', output)

    def test_local_capture_rejects_path_escape_unknown_encoding_and_oversized_file(self):
        outside = self.root / 'outside.html';outside.write_text('Private text.', encoding='utf-8')
        for locator in ('../outside.html', str(outside)):
            with self.assertRaises(ValueError):
                LocalProvider(self.approved).capture(locator, Budget())
        self.file.write_bytes(b'\xff\xfe')
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            LocalProvider(self.approved).capture('question.html', Budget())
        self.file.write_bytes(b'a' * 1_000_001)
        with self.assertRaisesRegex(ValueError, 'bounded file'):
            LocalProvider(self.approved).capture('question.html', Budget())

    def test_http_capture_reuses_fetcher_and_never_compares_or_confirms(self):
        body = b'<p>Anonymous HTTP fixture.</p>'
        result = HTTPResult('https://example.org/question/1', 200,
            {'x-reference-robots-sha256': 'a' * 64, 'x-reference-robots-checked-at': 'synthetic-time'}, body)
        fetcher = Mock();fetcher.page.return_value = (result, body.decode())
        output = HttpProvider(self.config, fetcher).capture(result.url, Budget())
        self.assertEqual(output['hash'], sha256(body).hexdigest())
        self.assertEqual(output['url'], result.url)
        self.assertEqual(output['robots_sha256'], 'a' * 64)
        self.assertEqual(fetcher.page.call_count, 1)
        self.assertNotIn('comparison', output)
        self.assertNotIn('state', output)

    def test_network_and_admission_fail_before_any_fetch(self):
        for config in (replace(self.config, network_enabled=False), replace(self.config, enabled=False),
                       replace(self.config, admissions=()),
                       replace(self.config, admissions=(replace(self.source, automatic_enabled=False),)),
                       replace(self.config, admissions=(replace(self.source, retention_seconds=0),))):
            with self.subTest(config=config), self.assertRaises(LookupFailure):
                fetcher = Mock()
                HttpProvider(config, fetcher).capture('https://example.org/question/1', Budget())
            fetcher.page.assert_not_called()

    def test_expired_budget_prevents_local_reads(self):
        with self.assertRaises(LookupFailure), patch.object(LocalProvider, 'read') as read:
            LocalProvider(self.approved).capture('question.html', Budget(-1))
        read.assert_not_called()

    def test_capture_tool_uses_approved_file_and_default_off_is_honest(self):
        output = capture_candidate(self.config, local_file='question.html')
        self.assertEqual(output['hash'], sha256(self.file.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'disabled'):
            capture_candidate(replace(self.config, enabled=False), local_file='question.html')

    def test_capture_tool_self_test_does_not_start_a_browser_or_network(self):
        with patch('tools.crawl_reference.resolve_public') as dns, patch('tools.crawl_reference.ReferenceFetcher') as fetcher:
            self.assertEqual(main(['--self-test']), 0)
        dns.assert_not_called();fetcher.assert_not_called()

    def test_capture_tool_does_not_export_external_body_or_report_confirmation(self):
        from io import StringIO
        import json
        stream = StringIO()
        with patch('tools.crawl_reference.LookupConfig.load', return_value=self.config), patch('sys.stdout', stream):
            self.assertEqual(main(['--local-file', 'question.html']), 0)
        output = json.loads(stream.getvalue())
        self.assertFalse(output['confirmed'])
        self.assertFalse(output['content_saved'])
        self.assertEqual(output['state'], 'DISCOVERED')
        self.assertNotIn('Anonymous candidate text', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
