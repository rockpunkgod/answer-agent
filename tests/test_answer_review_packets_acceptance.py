"""Independent read-only packet acceptance; no desktop or outbound actions."""
from hashlib import sha256
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from helpdesk.answer_review_packets import list_review_packets
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.storage import Store


class AnswerPacketAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'packets'; self.root.mkdir()
        self.packet = self.root / 'question34'; self.packet.mkdir()
        self.text = '同学，我们来分析一下。\r\n\r\n第一段。\n<script>window.bad=true</script>\n'
        self.acquisition = 'a' * 32
        source = self.base / 'windows-mcp'; source.mkdir()
        journal_patch = patch('helpdesk.answer_review_packets.WINDOWS_MCP_JOURNAL_ROOT', source)
        journal_patch.start(); self.addCleanup(journal_patch.stop)
        self.result = source / 'result.json'
        self.attempt = source / ('attempt-' + self.acquisition + '.json')
        copied = {'attempt_id': self.acquisition, 'tool': 'Clipboard', 'is_error': False,
                  'content': [{'type': 'text', 'text': 'Clipboard content:\n' + self.text}]}
        attempt = {'attempt_id': self.acquisition, 'tool': 'Clipboard', 'arguments': {'mode': 'get'},
                   'status': 'TOOL_RETURNED', 'result_path': str(self.result),
                   'started_at': '2026-10-01T04:00:00+00:00'}
        self.result.write_text(json.dumps(copied, ensure_ascii=False), encoding='utf-8')
        self.attempt.write_text(json.dumps(attempt, ensure_ascii=False), encoding='utf-8')
        (self.packet / 'answer.txt').write_bytes(self.text.encode('utf-8'))
        (self.packet / 'request.json').write_text(json.dumps({'group_observed': 'English答疑群',
            'student_observed': '学生甲', 'question_number': '34', 'student_words': '为什么不选B？'}), encoding='utf-8')
        (self.packet / 'clipboard-result.json').write_bytes(self.result.read_bytes())
        (self.packet / 'clipboard-attempt.json').write_bytes(self.attempt.read_bytes())
        self.manifest = {'version': 1, 'packet_id': self.packet.name,
                         'session_url': 'https://chat.deepseek.com/a/chat/s/review-packet-34'}
        for key, name in [('request', 'request.json'), ('answer', 'answer.txt'),
                          ('clipboard_result', 'clipboard-result.json'), ('clipboard_attempt', 'clipboard-attempt.json')]:
            self.manifest[key] = {'file': name, 'sha256': sha256((self.packet / name).read_bytes()).hexdigest()}
        self.write_manifest()

    def write_manifest(self):
        (self.packet / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def server(self, configured=True):
        self.db = self.base / 'business.db'
        server = DemoHTTPServer(('127.0.0.1', 0), self.db, processing_mode='ACK_ONLY',
                               answer_review_root=self.root if configured else None)
        worker = Thread(target=server.serve_forever, daemon=True); worker.start()
        def cleanup():
            server.shutdown(); server.server_close(); worker.join(timeout=3)
        self.addCleanup(cleanup)
        return server

    def request(self, server, path, method='GET', payload=None, host=None, token=None):
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        headers = {'Origin': 'http://127.0.0.1:' + str(server.server_port), 'Content-Type': 'application/json'}
        if host: headers['Host'] = host
        if token: headers['X-CSRF-Token'] = token
        client.request(method, path, None if payload is None else json.dumps(payload), headers)
        response = client.getresponse(); raw = response.read(); code = response.status
        client.close(); return code, json.loads(raw)

    def counts(self):
        store = Store(self.db)
        try:
            return {name: store.one('SELECT COUNT(*) FROM ' + name)[0] for name in
                    ('messages', 'outbox', 'answers', 'runs', 'cases', 'performance_units', 'audit')}
        finally: store.close()

    def test_http_packet_exact_text_and_readonly_repeated_get(self):
        server = self.server(); before = self.counts()
        for _ in range(2):
            code, data = self.request(server, '/api/answer-review-packets')
            self.assertEqual(code, 200)
            row, = data['packets']; self.assertEqual(row['text'], self.text)
            self.assertEqual(row['answer_sha256'], sha256(self.text.encode()).hexdigest())
            self.assertEqual(row['question_number'], '34')
            self.assertEqual(row['status'], 'AWAITING_HUMAN_SEND')
            self.assertIs(row['answer_review_required'], False)
            self.assertIs(row['actual_delivery_confirmed'], False)
            self.assertIs(row['formal_performance_eligible'], False)
        self.assertEqual(before, self.counts())
        _, state = self.request(server, '/api/state')
        for action in ('approve', 'dispatch', 'real_generate', 'dispatch_ack'):
            code, _ = self.request(server, '/api/action', 'POST', {'action': action}, token=state['csrf_token'])
            self.assertEqual(code, 403)
        self.assertEqual(before, self.counts())

    def test_unconfigured_and_host_and_client_directory_are_bounded(self):
        server = self.server(configured=False)
        code, data = self.request(server, '/api/answer-review-packets?root=' + self.root.as_posix())
        self.assertEqual(code, 200); self.assertFalse(data['configured']); self.assertEqual(data['packets'], [])
        self.assertEqual(self.request(server, '/api/answer-review-packets', host='other.example')[0], 403)

    def test_changed_answer_hash_and_local_escape_rejected(self):
        answer = self.packet / 'answer.txt'; original = answer.read_bytes(); answer.write_bytes(original + b'changed')
        with self.assertRaises(ValueError): list_review_packets(self.root)
        answer.write_bytes(original)
        self.manifest['answer']['file'] = '../answer.txt'; self.write_manifest()
        with self.assertRaises(ValueError): list_review_packets(self.root)

    def test_headless_actual_dom_exact_text_safe_copy_and_tamper_block(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest('Playwright unavailable')
        server = self.server(); before = self.counts(); errors = []
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:' + str(server.server_port))
                page.locator('#answer-review-packets details').wait_for()
                page.locator('#answer-review-packets summary').click()
                self.assertEqual(page.locator('#answer-review-packets pre').text_content(), self.text)
                self.assertFalse(page.evaluate('Boolean(window.bad)'))
                page.evaluate("Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text=>{window.testCopied=text;window.testCopyCount=(window.testCopyCount||0)+1;}}})")
                answer = self.packet / 'answer.txt'; original = answer.read_bytes()
                answer.write_bytes(original + b'changed')
                page.get_by_role('button', name='复制完整原文').click()
                expect(page.locator('#answer-review-status')).to_contain_text('发生变化')
                self.assertEqual(page.evaluate('window.testCopyCount||0'), 0)
                answer.write_bytes(original)
                page.get_by_role('button', name='复制完整原文').click()
                expect(page.locator('#answer-review-status')).to_contain_text('完整原文已复制')
                self.assertEqual(page.evaluate('window.testCopied'), self.text)
                self.assertIn('当前未发送、未计绩效', page.locator('#answer-review-status').text_content())
                self.assertFalse(errors)
            finally: browser.close()
        self.assertEqual(before, self.counts())

    def test_original_journal_changed_and_missing_identity_rejected(self):
        self.result.write_bytes(self.result.read_bytes() + b'changed original journal')
        with self.assertRaises(ValueError): list_review_packets(self.root)


if __name__ == '__main__': unittest.main()
