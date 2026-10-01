"""Mocked HTTP/DNS fixtures. These checks make no Internet requests."""
from unittest.mock import MagicMock, patch
from pathlib import Path
import socket
import tempfile
from threading import Thread
import time
import unittest

from helpdesk.reference_fetch import (Budget, HTTPResult, LookupFailure, PinnedHTTP,
    ReferenceFetcher, SourceAdmission, canonical_url, restriction_page)


class Clock:
    def __init__(self): self.value = 10000
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


class Wire:
    def __init__(self, values): self.values, self.calls = list(values), []
    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.values.pop(0)
        if isinstance(value, BaseException): raise value
        status, headers, body = value
        return HTTPResult(url, status, headers, body)


ROBOTS = (200, {'content-type': 'text/plain'}, b'User-agent: *\nAllow: /\n')
PAGE = (200, {'content-type': 'text/html'}, b'<title>Question</title><p>Self-authored text.</p>')


class ReferenceFetchTests(unittest.TestCase):
    def setUp(self):
        ReferenceFetcher._last_request = {}
        ReferenceFetcher._blocked_until = {}
        self.clock = Clock()
        self.source = SourceAdmission('example.org', aliases=('www.example.org',), automatic_enabled=True,
            terms_status='APPROVED', terms_url='https://example.org/terms', robots_status='REVIEWED',
            checked_at='2026-09-30T00:00:00+00:00', allowed_paths=('/question/',), retention_seconds=60)

    def fetch(self, values, source=None, seconds=120):
        wire = Wire(values)
        fetcher = ReferenceFetcher(transport=wire, clock=self.clock, sleeper=self.clock.sleep)
        return wire, fetcher, source or self.source, Budget(seconds, clock=self.clock)

    def test_public_url_and_local_metadata_credentials_encodings_rejected(self):
        self.assertEqual(canonical_url('https://example.org/question/1#fragment'), 'https://example.org/question/1')
        for url in ('file:///C:/secret', 'http://127.0.0.1/', 'http://10.0.0.1/', 'http://169.254.169.254/',
                    'http://[::1]/', 'http://localhost/', 'https://user:secret@example.org/',
                    'https://example.org:8443/', 'https://example.org/question/%252e%252e/private',
                    'https://example.org/question/1?token=secret', 'https://example.org\\@127.0.0.1/'):
            with self.subTest(url=url), self.assertRaises(LookupFailure): canonical_url(url)

    def test_terms_and_robots_separate_no_network_before_admission(self):
        from dataclasses import replace
        for source in (replace(self.source, automatic_enabled=False), replace(self.source, terms_status='UNVERIFIED'),
                       replace(self.source, robots_status='UNVERIFIED'), replace(self.source, allowed_paths=('/other/',))):
            wire, fetcher, source, budget = self.fetch([], source)
            with self.assertRaises(LookupFailure): fetcher.page('https://example.org/question/1', source, budget)
            self.assertFalse(wire.calls)

    def test_robots_denial_blocks_page_and_allowed_read_has_evidence(self):
        wire, fetcher, source, budget = self.fetch([(200, {'content-type': 'text/plain'}, b'User-agent: *\nDisallow: /\n')])
        with self.assertRaisesRegex(LookupFailure, 'ROBOTS_DENIED'):
            fetcher.page('https://example.org/question/1', source, budget)
        self.assertEqual(len(wire.calls), 1)
        wire, fetcher, source, budget = self.fetch([ROBOTS, PAGE])
        result, text = fetcher.page('https://example.org/question/1', source, budget)
        self.assertTrue(result.headers['x-reference-robots-sha256'])
        self.assertGreaterEqual(self.clock.value, 10010)

    def test_login_captcha_paywall_and_200_verification_stop(self):
        for title in ('Login required', '安全验证', 'Captcha', '会员专享', 'Error 500'):
            wire, fetcher, source, budget = self.fetch([ROBOTS, (200, {'content-type': 'text/html'}, ('<title>'+title+'</title>').encode())])
            with self.subTest(title=title), self.assertRaisesRegex(LookupFailure, 'LOGIN_CAPTCHA_OR_PAYWALL'):
                fetcher.page('https://example.org/question/1', source, budget)
            self.assertEqual(len(wire.calls), 2)
        self.assertFalse(restriction_page('<title>Reading question</title><p>The article explains login screens.</p>'))

    def test_403_and_429_never_escalate_or_retry(self):
        for status, expected in ((403, 'ACCESS_RESTRICTED'), (429, 'RATE_LIMITED')):
            wire, fetcher, source, budget = self.fetch([ROBOTS, (status, {'retry-after': '30'}, b'')])
            with self.subTest(status=status), self.assertRaises(LookupFailure) as caught:
                fetcher.page('https://example.org/question/1', source, budget)
            self.assertEqual(caught.exception.status, expected)
            self.assertEqual(len(wire.calls), 2)
            if status == 429:
                self.assertGreaterEqual(fetcher._blocked_until['example.org'], self.clock.value + 30)

    def test_redirect_path_and_nonpublic_targets_checked_before_follow(self):
        for target in ('http://127.0.0.1/', 'https://other.example.net/question/1', 'https://example.org/private'):
            wire, fetcher, source, budget = self.fetch([ROBOTS, (302, {'location': target}, b'')])
            with self.subTest(target=target), self.assertRaises(LookupFailure):
                fetcher.page('https://example.org/question/1', source, budget)
            self.assertEqual(len(wire.calls), 2)
        wire, fetcher, source, budget = self.fetch([ROBOTS, (302, {'location': '/question/2'}, b''), ROBOTS, PAGE])
        result, text = fetcher.page('https://example.org/question/1', source, budget)
        self.assertEqual(result.url, 'https://example.org/question/2')

    def test_timeout_retry_once_and_budget_or_cancel_stops(self):
        wire, fetcher, source, budget = self.fetch([ROBOTS, LookupFailure('TIMEOUT', 'HTTP_TIMEOUT'), PAGE])
        result, text = fetcher.page('https://example.org/question/1', source, budget)
        self.assertEqual(len(wire.calls), 3)
        ReferenceFetcher._last_request = {}
        wire, fetcher, source, budget = self.fetch([ROBOTS, PAGE], seconds=5)
        with self.assertRaisesRegex(LookupFailure, 'RATE_WAIT_EXCEEDS_BUDGET'):
            fetcher.page('https://example.org/question/1', source, budget)
        self.assertEqual(len(wire.calls), 1)
        cancelled = Budget(cancelled=lambda: True)
        with self.assertRaisesRegex(LookupFailure, 'CANCELLED'): cancelled.remaining()

    def test_pdf_requires_existing_reader_no_summary_substitution(self):
        wire, fetcher, source, budget = self.fetch([ROBOTS, (200, {'content-type': 'application/pdf'}, b'%PDF-fixture')])
        with self.assertRaises(LookupFailure) as caught:
            fetcher.page('https://example.org/question/1', source, budget)
        self.assertEqual(caught.exception.status, 'UNSUPPORTED_DOCUMENT')

    def test_search_endpoint_no_secret_redirects(self):
        wire, fetcher, source, budget = self.fetch([(302, {'location': 'https://other.example.net/'}, b'')])
        with self.assertRaises(LookupFailure):
            fetcher.search_request('https://api.search.brave.com/res/v1/web/search?q=fixture', budget, headers={'X-Subscription-Token': 'synthetic-key'})
        self.assertEqual(len(wire.calls), 1)
        with self.assertRaises(LookupFailure):
            fetcher.search_request('https://other.example.net/?q=fixture', budget, headers={'X-Subscription-Token': 'synthetic-key'})

    def test_actual_transport_pins_public_ip_and_rejects_dns_rebinding(self):
        resolver = lambda host, port, timeout: ['93.184.216.34']
        connection, response, sock = MagicMock(), MagicMock(), MagicMock()
        response.getheaders.return_value = [('Content-Type', 'text/html')]
        response.status = 200
        response.read1.side_effect = [b'fixture', b'']
        connection.getresponse.return_value = response
        with patch('helpdesk.reference_fetch.http.client.HTTPConnection', return_value=connection), patch('helpdesk.reference_fetch.socket.create_connection', return_value=sock) as connect:
            PinnedHTTP(resolver=resolver).request('http://example.org/question/1', timeout=15)
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', connection.port))
        with patch('helpdesk.reference_fetch.socket.create_connection') as connect:
            with self.assertRaisesRegex(LookupFailure, 'DNS_NONPUBLIC'):
                PinnedHTTP(resolver=lambda *args: ['93.184.216.34', '127.0.0.1']).request('https://example.org/question/1', timeout=15)
            connect.assert_not_called()

    def test_connection_close_and_slow_body_or_headers_respect_absolute_deadline(self):
        # Real local sockets, synthetic HTTP only; checked public IP is mocked.
        for scenario in ('connection_close', 'slow_body', 'slow_headers'):
            with self.subTest(scenario=scenario):
                client, peer = socket.socketpair()
                def serve():
                    try:
                        peer.recv(8192)
                        if scenario == 'connection_close':
                            peer.sendall(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\nfixture')
                            return
                        if scenario == 'slow_body':
                            peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 10000\r\nContent-Type: text/plain\r\n\r\n')
                            for i in range(40):
                                peer.sendall(b'x')
                                time.sleep(.03)
                        else:
                            for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n':
                                peer.sendall(bytes([byte]))
                                time.sleep(.03)
                    except OSError:
                        pass
                    finally:
                        peer.close()
                thread = Thread(target=serve, daemon=True)
                thread.start()
                started = time.monotonic()
                try:
                    with patch('helpdesk.reference_fetch.socket.create_connection', return_value=client):
                        transport = PinnedHTTP(resolver=lambda *args: ['93.184.216.34'])
                        if scenario == 'connection_close':
                            self.assertEqual(transport.request('http://example.org/question/1', timeout=1).body, b'fixture')
                        else:
                            with self.assertRaises(LookupFailure) as caught:
                                transport.request('http://example.org/question/1', timeout=.2)
                            self.assertEqual(caught.exception.status, 'TIMEOUT')
                    self.assertLess(time.monotonic() - started, 1.5)
                finally:
                    client.close()
                    peer.close()
                    thread.join(timeout=2)

    def test_shared_host_interval_and_retry_after_survive_another_fetcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wire = Wire([PAGE])
            kwargs = {'clock': self.clock, 'wall_clock': self.clock, 'sleeper': self.clock.sleep, 'coordination_root': root}
            first = ReferenceFetcher(transport=wire, **kwargs)
            first._request('https://example.org/question/1', Budget(clock=self.clock))
            ReferenceFetcher._last_request, ReferenceFetcher._blocked_until = {}, {}
            second = ReferenceFetcher(transport=Wire([(429, {'retry-after': '30'}, b'')]), **kwargs)
            with self.assertRaisesRegex(LookupFailure, 'HTTP_429_RETRY_AFTER'):
                second._request('https://example.org/question/2', Budget(clock=self.clock))
            self.assertGreaterEqual(self.clock.value, 10010)
            ReferenceFetcher._last_request, ReferenceFetcher._blocked_until = {}, {}
            third = ReferenceFetcher(transport=Wire([PAGE]), **kwargs)
            third._request('https://example.org/question/3', Budget(clock=self.clock))
            self.assertGreaterEqual(self.clock.value, 10040)


if __name__ == '__main__':
    unittest.main()
