"""Local HTTP and headless UI; anonymous fixtures, no real desktop or accounts."""
from dataclasses import replace
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from playwright.sync_api import sync_playwright
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.domain import Intent, Question
from helpdesk.reference_lookup import LookupConfig, ReferenceLookup
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from tools.lookup_reference import demo_snapshot


ROOT = Path(__file__).resolve().parents[1]


class ReferenceWorkbenchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = DemoHTTPServer(('127.0.0.1', 0), self.root / 'demo.db', processing_mode='ACK_ONLY')
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.request('GET', '/api/state')[1]['csrf_token']
        self.snapshot = demo_snapshot()
        with closing(Store(self.server.db_path)) as store:
            app = Helpdesk(store)
            binding = app.bind('synthetic-English', 'synthetic-student', '匿名学生', verified=True)
            self.outcome = app.ingest(Incoming(binding, 'Synthetic question request', Intent.NEW,
                source='self-authored-fixture', platform_id='synthetic-message',
                verified_question=Question.from_dict(self.snapshot['student_question']),
                raw_material=self.snapshot['original_student_material'], verified_material=self.snapshot['student_material']))
            q = store.one('SELECT * FROM questions WHERE id=?', (self.outcome.question_id,))
            self.payload = {'action': 'lookup', 'question_id': q['id'], 'question_version': q['current_version'],
                'context_revision': q['context_revision'], 'trigger': 'clean_copy', 'candidate_urls': []}
            self.material_id = q['material_id']
        self.config = LookupConfig(enabled=True, cache_root=self.root / 'cache',
            fixture_root=ROOT / 'tests/fixtures/reference_lookup')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, payload=None, headers=None):
        with closing(HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)) as connection:
            options = {'Origin': self.origin, 'X-CSRF-Token': self.token, 'Content-Type': 'application/json'} if payload is not None else {}
            options.update(headers or {})
            connection.request(method, path, body=json.dumps(payload).encode() if payload is not None else None, headers=options)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw.decode()

    def test_default_off_and_local_security_do_not_create_a_lookup(self):
        self.assertFalse(self.request('GET', '/api/reference-lookups')[1]['enabled'])
        self.assertEqual(self.request('POST', '/api/reference-lookups', self.payload)[0], 409)
        for headers in ({'Origin': 'https://other.invalid'}, {'X-CSRF-Token': 'wrong'}, {'Host': 'other.invalid'}):
            self.assertEqual(self.request('POST', '/api/reference-lookups', self.payload, headers=headers)[0], 403)
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event LIKE 'REFERENCE_LOOKUP_%'")[0], 0)
        self.assertFalse(self.config.cache_root.exists())

    def test_ack_only_allows_internal_report_and_never_enables_answer_send(self):
        self.server.reference_lookup = ReferenceLookup(self.config)
        payload = {**self.payload, 'candidate_urls': ['https://www.jyeoo.com/question/synthetic']}
        status, data = self.request('POST', '/api/reference-lookups', payload)
        self.assertEqual(status, 200)
        self.assertEqual(data['result']['retrieval_status'], 'ACCESS_RESTRICTED')
        self.assertEqual(data['result']['errors'][0]['code'], 'NETWORK_DISABLED')
        self.assertEqual(data['result']['next_action'], 'CONTINUE_STUDENT_MATERIAL')
        self.assertEqual(self.request('POST', '/api/action', {'action': 'generate'})[0], 403)
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
            self.assertFalse(store.one("SELECT id FROM outbox WHERE purpose IN ('ANSWER','REQUEST_IMAGE')"))
            self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual(self.request('GET', '/api/reference-lookups')[1]['reports'][0]['retrieval_status'], 'ACCESS_RESTRICTED')

    def test_existing_review_consumes_reference_only_and_stale_request_stops(self):
        self.server.reference_lookup = ReferenceLookup(self.config)
        with closing(Store(self.server.db_path)) as store:
            report = self.server.reference_lookup.run_for_question(store, self.payload['question_id'],
                self.payload['question_version'], self.payload['context_revision'], trigger='clean_copy', fixtures=['demo.html'])
        apply = {'action': 'apply', 'lookup_key': report['lookup_key'], 'reviewer': 'synthetic-reviewer',
                 'reason': '匿名样例：核对原文、题干和四个学生选项一致'}
        self.assertEqual(self.request('POST', '/api/reference-lookups', apply)[0], 400)
        self.server.reference_lookup.config = replace(self.config, shadow=False)
        self.assertEqual(self.request('POST', '/api/reference-lookups', apply)[0], 200)
        self.assertEqual(self.request('POST', '/api/reference-lookups', apply)[0], 200)
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
            Helpdesk(store).correct_material(self.material_id, self.outcome.message_id, 'Synthetic changed material', 'Synthetic changed material')
        self.assertTrue(self.request('GET', '/api/reference-lookups')[1]['reports'][0]['stale'])
        self.assertEqual(self.request('POST', '/api/reference-lookups', self.payload)[0], 400)
        self.assertEqual(self.request('POST', '/api/reference-lookups', apply)[0], 400)

    def test_no_browser_upload_paths_free_commands_or_arbitrary_identifiers(self):
        self.server.reference_lookup = ReferenceLookup(self.config)
        for payload in ({**self.payload, 'fixture': 'C:/private-file'},
                        {**self.payload, 'command': 'anything'},
                        {**self.payload, 'question_id': 'not-a-real-question'},
                        {**self.payload, 'candidate_urls': ['https://example.org'] * 7}):
            self.assertEqual(self.request('POST', '/api/reference-lookups', payload)[0], 400)

    def test_legacy_apply_requires_actual_review_reason_before_confirmation(self):
        review = self.candidate_review()
        self.server.reference_lookup.config = replace(self.config, shadow=False)
        report = self.request('GET', '/api/reference-lookups')[1]['reports'][0]
        payload = {'action': 'apply', 'lookup_key': report['lookup_key'], 'reviewer': '匿名核对人'}
        for change in ({}, {'reason': ''}, {'reason': '   '}, {'reason': 42}, {'reason': 'x' * 2001}):
            self.assertEqual(self.request('POST', '/api/reference-lookups', {**payload, **change})[0], 400)
        with closing(Store(self.server.db_path)) as store:
            candidate = json.loads(store.one('SELECT comparison FROM reference_candidates WHERE id=?',
                (review['candidate_id'],))[0])['candidate']
            self.assertEqual(candidate['state'], 'MATCHED_CANDIDATE')
            self.assertIsNone(candidate['confirmed_by'])
        reason = '人工逐项核对原文、否定条件、四个选项和学生字母映射'
        self.assertEqual(self.request('POST', '/api/reference-lookups', {**payload, 'reason': reason})[0], 200)
        self.assertEqual(self.request('POST', '/api/reference-lookups', {**payload, 'reason': '再次查看同一依据'})[0], 200)
        with closing(Store(self.server.db_path)) as store:
            candidate = json.loads(store.one('SELECT comparison FROM reference_candidates WHERE id=?',
                (review['candidate_id'],))[0])['candidate']
            self.assertEqual(candidate['confirmation_reason'], reason)
            self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_CANDIDATE_CONFIRMED'")[0], 1)
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
            self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_broken_optional_config_does_not_stop_original_workbench(self):
        broken = self.root / 'broken.toml'
        broken.write_text('[lookup]\nenabled = "not a boolean"\n', encoding='utf-8')
        server = DemoHTTPServer(('127.0.0.1', 0), self.root / 'other.db', reference_lookup_config=broken)
        try:
            self.assertFalse(server.reference_lookup.config.enabled)
            self.assertEqual(server.reference_lookup_error, 'CONFIG_UNAVAILABLE_OR_INVALID')
        finally:
            server.server_close()

    def candidate_review(self):
        self.server.reference_lookup = ReferenceLookup(self.config)
        with closing(Store(self.server.db_path)) as store:
            report = self.server.reference_lookup.run_for_question(store, self.payload['question_id'],
                self.payload['question_version'], self.payload['context_revision'], trigger='clean_copy', fixtures=['demo.html'])
        return {'action': 'review_candidate', 'candidate_id': report['matches'][0]['candidate_id'],
            'question_version': self.payload['question_version'], 'context_revision': self.payload['context_revision'],
            'decision': 'confirm', 'reviewer': '匿名核对人', 'reason': '学生原文、题干和四个选项均一致'}

    def test_shadow_can_confirm_without_consumption_then_explicit_use_is_idempotent(self):
        review = self.candidate_review()
        status, output = self.request('POST', '/api/reference-lookups', review)
        self.assertEqual(status, 200)
        self.assertEqual(output['result']['state'], 'CONFIRMED')
        self.assertFalse(output['result']['consumption_enabled'])
        data = self.request('GET', '/api/reference-lookups')[1]
        self.assertEqual(len(data['reference_candidates']), 1)
        self.assertFalse(data['reference_candidates'][0]['can_use'])
        self.server.reference_lookup.config = replace(self.config, shadow=False)
        self.assertTrue(self.request('GET', '/api/reference-lookups')[1]['reference_candidates'][0]['can_use'])
        output = self.request('POST', '/api/reference-lookups', review)[1]
        self.assertTrue(output['result']['consumption_enabled'])
        self.assertEqual(self.request('POST', '/api/reference-lookups', {**review, 'reviewer': '另一核对人'})[0], 200)
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(len(Helpdesk(store).context(self.outcome.turn_id)['references']), 1)
            self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
            self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_CANDIDATE_CONFIRMED'")[0], 1)
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
            self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_review_reject_revoke_and_stale_context_are_checked_on_the_server(self):
        review = self.candidate_review()
        for change in ({'reason': ''}, {'reviewer': ''}, {'decision': 'automatic'},
                       {'context_revision': -10}, {'question_version': 'other-version'}, {'candidate_id': 'unknown'},
                       {'consume': True}, {'context_revision': True}):
            self.assertEqual(self.request('POST', '/api/reference-lookups', {**review, **change})[0], 400)
        self.assertEqual(self.request('POST', '/api/reference-lookups', review)[0], 200)
        self.assertEqual(self.request('POST', '/api/reference-lookups', {**review, 'decision': 'reject', 'reason': '人工复核拒绝此来源'})[0], 200)
        self.assertEqual(self.request('POST', '/api/reference-lookups', review)[0], 400)
        data = self.request('GET', '/api/reference-lookups')[1]['reference_candidates'][0]
        self.assertEqual(data['state'], 'REJECTED')
        self.assertFalse(data['can_confirm'])

    def test_source_permission_removed_after_shadow_review_cannot_enable_use(self):
        review = self.candidate_review()
        self.assertEqual(self.request('POST', '/api/reference-lookups', review)[0], 200)
        with closing(Store(self.server.db_path)) as store:
            row = store.one('SELECT comparison FROM reference_candidates WHERE id=?', (review['candidate_id'],))
            value = json.loads(row[0])
            value['candidate']['source_policy'] = {'business_record_storage_allowed': True, 'domain': 'example.org'}
            value['candidate']['source_url'] = 'https://example.org/question/1'
            store.execute('UPDATE reference_candidates SET comparison=?,source=? WHERE id=?',
                (json.dumps(value), 'example.org', review['candidate_id']))
        self.server.reference_lookup.config = replace(self.config, shadow=False)
        self.assertFalse(self.request('GET', '/api/reference-lookups')[1]['reference_candidates'][0]['can_use'])
        self.assertEqual(self.request('POST', '/api/reference-lookups', review)[0], 400)

    def test_pending_student_correction_disables_confirmation_in_current_workbench(self):
        review = self.candidate_review()
        with closing(Store(self.server.db_path)) as store:
            binding = store.one('SELECT binding_id FROM messages WHERE id=?', (self.outcome.message_id,))[0]
            outcome = Helpdesk(store).ingest(Incoming(binding, '请等我补图', Intent.CORRECTION,
                quote_message_id=self.outcome.message_id))
            self.assertEqual(outcome.status, 'NEEDS_REVIEW')
        candidate = self.request('GET', '/api/reference-lookups')[1]['reference_candidates'][0]
        self.assertTrue(candidate['input_pending_review'])
        self.assertFalse(candidate['can_confirm'])
        review['context_revision'] = candidate['current_context_revision']
        self.assertEqual(self.request('POST', '/api/reference-lookups', review)[0], 400)


class ReferenceWorkbenchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_report_is_plain_text_and_lookup_remains_a_manual_action(self):
        with self.browser.new_context() as context:
            page = context.new_page()
            posts = []
            origin = 'http://127.0.0.1:49151'
            hostile = '<img src=x onerror="window.referenceInjection=true">'
            data = {'enabled': True, 'shadow': True, 'network_enabled': False, 'questions': [
                {'question_id': 'synthetic-question', 'question_version': 'synthetic-version', 'context_revision': 1, 'label': '匿名学生'}],
                'reports': [{'question_id': 'synthetic-question', 'retrieval_status': 'OFFLINE_FIXTURE', 'match_status': 'MATCH_VERIFIED',
                    'explanation': hostile, 'candidates': [], 'matches': []}]}
            def route(request):
                path = request.request.url.removeprefix(origin)
                if path == '/':
                    request.fulfill(status=200, content_type='text/html', body=(ROOT / 'helpdesk/static/index.html').read_text(encoding='utf-8'))
                elif path == '/api/reference-lookups':
                    if request.request.method == 'POST':
                        posts.append(json.loads(request.request.post_data))
                        request.fulfill(status=200, content_type='application/json', body=json.dumps({'result': {'explanation': '内部报告已完成'}}))
                    else:
                        request.fulfill(status=200, content_type='application/json', body=json.dumps(data))
                elif path == '/api/state':
                    request.fulfill(status=200, content_type='application/json', body='{"csrf_token":"synthetic-csrf"}')
                else:
                    request.fulfill(status=404, body='')
            page.route('**/*', route)
            page.goto(origin + '/')
            page.add_script_tag(path=str(ROOT / 'helpdesk/static/reference-lookup.js'))
            page.get_by_text(hostile, exact=True).wait_for()
            self.assertEqual(page.locator('#reference-lookup-panel img').count(), 0)
            self.assertIsNone(page.evaluate('window.referenceInjection'))
            self.assertEqual(posts, [])
            page.locator('#reference-urls').fill('https://example.org/question/1')
            page.get_by_role('button', name='生成内部核对报告', exact=True).click()
            page.get_by_text('内部报告已完成', exact=True).wait_for()
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0]['candidate_urls'], ['https://example.org/question/1'])
            self.assertEqual(posts[0]['question_version'], 'synthetic-version')
            self.assertEqual(page.get_by_role('button', name='确认后添加参考材料').count(), 0)

    def test_shadow_review_needs_explicit_evidence_and_never_displays_internal_confidence_or_logs(self):
        with self.browser.new_context() as context:
            page = context.new_page();page.set_default_timeout(5000)
            posts = [];origin = 'http://127.0.0.1:49151'
            candidate = {'candidate_id': 'candidate', 'question_id': 'question', 'question_version': 'version',
                'current_context_revision': 2, 'state': 'MATCHED_CANDIDATE', 'resolution_status': 'MATCH_CANDIDATE',
                'source': '<img src=x onerror="window.injected=true">', 'source_url': 'https://fixtures.invalid/example',
                'content_hash': 'a' * 64, 'can_confirm': True, 'can_reject': True, 'can_use': False,
                'confidence_level': 'SECRET_CONFIDENCE', 'debug_log': 'NEVER_SHOW_TRACE',
                'student_evidence': {'source_message_id': 'anonymous-message', 'question_source': 'anonymous-OCR',
                    'raw_material': '<img src=x onerror="window.rawInjected=true">', 'raw_stem': 'OCR: NOT unclear',
                    'student_sent_at': '2026-09-30T22:58:00+08:00', 'collected_at': '2026-09-30T23:05:00+08:00',
                    'uncertain_fields': ['stem'], 'raw_options': [], 'images': [
                        {'draft_id': 'a' * 32, 'index': 0}, {'draft_id': 'https://other.invalid', 'index': 0}]},
                'comparison_result': {'relation': ['SAME_CONTENT'], 'evidence': [], 'field_differences': []}}
            data = {'enabled': True, 'shadow': True, 'questions': [{'question_id': 'question', 'question_version': 'version',
                'context_revision': 2, 'label': '匿名学生'}], 'reports': [], 'reference_candidates': [candidate]}
            def route(request):
                path = request.request.url.removeprefix(origin)
                if path == '/':
                    request.fulfill(status=200, content_type='text/html', body=(ROOT / 'helpdesk/static/index.html').read_text(encoding='utf-8'))
                elif path == '/api/reference-lookups':
                    if request.request.method == 'POST':
                        posts.append(json.loads(request.request.post_data))
                        candidate.update(state='CONFIRMED', can_confirm=False, confirmed_by='匿名核对人',
                            confirmed_at='2026-10-01T15:00:00+00:00', confirmation_reason='核对必要原文一致', consumption_enabled=False)
                        request.fulfill(status=200, content_type='application/json', body=json.dumps({'result': candidate}))
                    else:
                        request.fulfill(status=200, content_type='application/json', body=json.dumps(data))
                elif path == '/api/state':
                    request.fulfill(status=200, content_type='application/json', body='{"csrf_token":"synthetic-csrf"}')
                else:
                    request.fulfill(status=404, body='')
            page.route('**/*', route);page.goto(origin + '/')
            page.add_script_tag(path=str(ROOT / 'helpdesk/static/reference-lookup.js'))
            button = page.get_by_role('button', name='确认候选', exact=True);button.wait_for()
            button.click();self.assertEqual(posts, [])
            page.get_by_label('核对人姓名', exact=True).fill('匿名核对人')
            page.get_by_label('候选核对依据', exact=True).fill('核对必要原文一致')
            button.click();page.get_by_text('已人工确认', exact=True).wait_for()
            self.assertEqual(posts, [{'action': 'review_candidate', 'candidate_id': 'candidate', 'question_version': 'version',
                'context_revision': 2, 'decision': 'confirm', 'reviewer': '匿名核对人', 'reason': '核对必要原文一致'}])
            page.get_by_text('查看原始识别文本与消息出处', exact=True).click()
            body = page.locator('#reference-lookup-panel').inner_text()
            self.assertNotIn('SECRET_CONFIDENCE', body);self.assertNotIn('NEVER_SHOW_TRACE', body)
            self.assertEqual(page.locator('#reference-lookup-panel img').count(), 0)
            self.assertIsNone(page.evaluate('window.injected'))
            self.assertIsNone(page.evaluate('window.rawInjected'))
            self.assertIn('OCR: NOT unclear', body)
            self.assertIn('学生原始发送时间：2026-09-30T22:58:00+08:00', body)
            self.assertIn('采集时间：2026-09-30T23:05:00+08:00', body)
            link = page.get_by_role('link', name='查看原图 1', exact=True)
            self.assertEqual(link.count(), 1)
            self.assertEqual(link.get_attribute('href'), '/api/source-question-image?draft_id=' + 'a' * 32 + '&index=0')
            self.assertEqual(page.get_by_role('button', name='用于答疑', exact=True).count(), 0)


if __name__ == '__main__':
    unittest.main()
