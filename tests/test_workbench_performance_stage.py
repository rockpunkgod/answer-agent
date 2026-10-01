"""Trusted daily-statistics visibility is independent of ACK-only teaching gates."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from helpdesk.demo_server import DemoHTTPServer
from helpdesk.storage import Store


class PerformanceStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, value=None, *, include=True):
        config = self.root / 'collector.toml'
        source = (Path(__file__).resolve().parents[1] / 'config/collector.windows-native.example.toml').read_text(encoding='utf-8')
        source = source.replace('"data/native-demo.db"', json.dumps((self.root / 'business.db').as_posix()))
        source = source.replace('"data/native-messages.db"', json.dumps((self.root / 'raw.db').as_posix()))
        originals = self.root / 'native-records'
        originals.mkdir(exist_ok=True)
        source = source.replace('"data/private/chat-text-records"', json.dumps(originals.as_posix()))
        source = source.replace('performance_enabled = true',
            ('performance_enabled = ' + value) if include else '')
        config.write_text(source, encoding='utf-8'); return config

    def serve(self, value=None, *, include=True, configured=True, performance_enabled=None):
        server = DemoHTTPServer(('127.0.0.1', 0), self.root / 'unused.db',
            collector_config=self.config(value, include=include) if configured else None,
            processing_mode='ACK_ONLY', performance_enabled=performance_enabled)
        worker = Thread(target=server.serve_forever, daemon=True); worker.start()
        def cleanup():
            server.shutdown(); server.server_close(); worker.join(timeout=3)
        self.addCleanup(cleanup); return server

    def request(self, server, path, *, action=None, token=None):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        headers = {'Origin': 'http://127.0.0.1:' + str(server.server_port), 'Content-Type': 'application/json'}
        if token: headers['X-CSRF-Token'] = token
        connection.request('POST' if action else 'GET', path,
                           json.dumps({'action': action}) if action else None, headers)
        response = connection.getresponse(); value = json.loads(response.read()); code = response.status
        connection.close(); return code, value

    def test_enabled_state_without_teaching_or_delivery_permissions(self):
        server = self.serve('true')
        _, state = self.request(server, '/api/state')
        self.assertIs(state['performance_available'], True)
        self.assertFalse(state['collector']['teaching_enabled'])
        self.assertFalse(state['collector']['student_send_enabled'])
        for action in ('approve', 'dispatch', 'generate', 'real_generate', 'dispatch_ack'):
            self.assertEqual(self.request(server, '/api/action', action=action, token=state['csrf_token'])[0], 403)
        _, data = self.request(server, '/api/performance?date=2026-09-30')
        self.assertFalse(data['report']['coverage']['complete'])
        self.assertTrue(all(value is None for value in data['formal_totals'].values()))

    def test_false_and_missing_flag_preserve_hidden_ack_only_statistics(self):
        for value, include in [('false', True), (None, False)]:
            server = self.serve(value, include=include)
            self.assertIs(self.request(server, '/api/state')[1]['performance_available'], False)

    def test_unconfigured_local_workbench_keeps_source_and_send_disconnected(self):
        server = self.serve(configured=False, performance_enabled=True)
        _, state = self.request(server, '/api/state')
        self.assertEqual(state['application'], 'wecom-english-helpdesk')
        self.assertIs(state['simulation'], False)
        self.assertIs(state['dashboard']['simulation'], False)
        self.assertIs(state['performance_available'], True)
        self.assertIs(state['collector']['configured'], False)
        self.assertIs(state['collector']['control']['worker_alive'], False)
        self.assertFalse(state['collector']['student_send_enabled'])
        self.assertFalse(state['collector']['teaching_enabled'])
        for action in ('new_alice', 'approve', 'dispatch_ack', 'real_generate'):
            self.assertEqual(self.request(server, '/api/action', action=action, token=state['csrf_token'])[0], 403)
        _, data = self.request(server, '/api/performance?date=2026-09-30')
        self.assertIs(data['simulation'], False)
        self.assertTrue(all(value is None for value in data['formal_totals'].values()))

    def test_explicit_stage_disable_is_respected_by_launcher_performance_flag(self):
        server = self.serve('false', performance_enabled=True)
        self.assertIs(self.request(server, '/api/state')[1]['performance_available'], False)

    def test_headless_missing_flag_keeps_old_ack_only_section_hidden(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError: self.skipTest('Playwright unavailable')
        server = self.serve(include=False)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page(); page.goto('http://127.0.0.1:' + str(server.server_port))
                expect(page.locator('#collector-panel')).to_be_visible()
                expect(page.locator('[data-performance-entry]')).to_be_hidden()
                expect(page.locator('#performance-refresh')).to_be_hidden()
            finally: browser.close()

    def test_strict_boolean_rejected_before_stage_side_effects(self):
        for value in ('"true"', '1', '[true]'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'performance_enabled must be a boolean'):
                DemoHTTPServer(('127.0.0.1', 0), self.root / 'unused.db',
                               collector_config=self.config(value), processing_mode='ACK_ONLY')
        self.assertFalse((self.root / 'business.db').exists())

    def test_headless_statistics_show_api_numbers_and_partial_coverage(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError: self.skipTest('Playwright unavailable')
        server = self.serve('true')
        totals = {'day_composite_articles': 2, 'grammar_listening_actual_questions': 7, 'night_articles': 1}
        report = {'report_date': '2026-09-30', 'timezone': 'Asia/Shanghai',
                  'summary': {'pending_records': 4, **totals}, 'coverage': {'complete': False},
                  'verified_subtotals': {'totals': totals, 'included_unit_count': 10, 'group_count': 2,
                     'monthly_start_date': '2026-09-01', 'monthly_totals': totals}, 'pending': []}
        with patch('helpdesk.performance_reports.PerformanceReports.build', return_value=report):
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                try:
                    page = browser.new_page(); page.goto('http://127.0.0.1:' + str(server.server_port))
                    expect(page.locator('#collector-panel')).to_be_visible()
                    expect(page.locator('[data-performance-entry]')).to_be_visible()
                    self.assertTrue(page.locator('[data-teaching-entry]').evaluate_all('(nodes)=>nodes.every(node=>node.hidden)'))
                    page.locator('#report-date').fill('2026-09-30')
                    page.get_by_role('button', name='查看统计草稿').click()
                    expect(page.locator('.performance-column').nth(0)).to_contain_text('2 篇')
                    expect(page.locator('.performance-column').nth(1)).to_contain_text('7 题')
                    expect(page.locator('.performance-column').nth(2)).to_contain_text('1 篇')
                    expect(page.locator('#performance-result')).to_contain_text('全量正式总数未知')
                finally: browser.close()

    def test_headless_download_retains_all_details_without_creating_ledger_or_report_versions(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError: self.skipTest('Playwright unavailable')
        server = self.serve(configured=False, performance_enabled=True)
        # Initialize the existing report schema, then capture all business counts before browser use.
        self.request(server, '/api/performance?date=2026-09-30')
        tables = ('messages', 'outbox', 'performance_units', 'performance_report_versions')
        def counts():
            store = Store(server.db_path)
            try: return {table: store.one('SELECT COUNT(*) FROM ' + table)[0] for table in tables}
            finally: store.close()
        before = counts()
        totals = {'day_composite_articles': 2, 'grammar_listening_actual_questions': 7, 'night_articles': 1}
        report = {'report_date': '2026-09-30', 'timezone': 'Asia/Shanghai',
                  'summary': {'pending_records': 25, **totals}, 'coverage': {'complete': False},
                  'details': [{'unit_id': 'unit-1', 'source_message_id': 'message-1',
                               'student_question_time': '2026-09-30T23:10:00+08:00', 'quantity': 1}],
                  'pending': [{'student': '匿名学生', 'reason': '待核对-' + str(i)} for i in range(25)]}
        with patch('helpdesk.performance_reports.PerformanceReports.build', return_value=report):
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto('http://127.0.0.1:' + str(server.server_port))
                    expect(page.locator('#mode-label')).to_contain_text('采集未配置')
                    expect(page.locator('button[data-action="collector_start"]')).to_be_disabled()
                    expect(page.locator('#worker-control-panel')).to_be_hidden()
                    page.locator('#report-date').fill('2026-09-30')
                    page.locator('#performance-refresh').click()
                    expect(page.locator('#performance-result li')).to_have_count(20)
                    downloads = []
                    for index in range(2):
                        with page.expect_download() as event:
                            page.locator('#performance-export').click()
                        download = event.value
                        self.assertEqual(download.suggested_filename, '2026-09-30-答疑日报.json')
                        target = self.root / ('download-' + str(index) + '.json')
                        download.save_as(target)
                        downloads.append(json.loads(target.read_text(encoding='utf-8')))
                    self.assertEqual(downloads[0], downloads[1])
                    self.assertEqual(downloads[0]['report'], report)
                    self.assertEqual(len(downloads[0]['report']['pending']), 25)
                    self.assertTrue(all(value is None for value in downloads[0]['formal_totals'].values()))
                    page.locator('#report-date').fill('')
                    page.locator('#performance-export').click()
                    expect(page.locator('#performance-export-status')).to_have_text('请填写统计日期')
                finally: browser.close()
        self.assertEqual(counts(), before)


if __name__ == '__main__': unittest.main()
