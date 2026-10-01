"""Self-authored fixtures and mocked HTTP; never real websites or accounts."""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpdesk.domain import Intent, Question
from helpdesk.reference_fetch import HTTPResult, LookupFailure, SourceAdmission
from helpdesk.reference_lookup import BraveSearch, LookupConfig, ReferenceLookup, extract_document, query_phrases
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store, encode
from tools.lookup_reference import demo_snapshot


FIXTURE = Path(__file__).parent / 'fixtures/reference_lookup/demo.html'


class Provider:
    identity = 'MOCK_URL_DISCOVERY'
    def __init__(self, results=(), *, error=None, callback=None):
        self.results, self.error, self.callback, self.calls = results, error, callback, []
    def search(self, query, budget):
        budget.remaining()
        self.calls.append(query)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return list(self.results)


class Fetcher:
    def __init__(self, body=None, *, error=None):
        self.body = body or FIXTURE.read_bytes()
        self.error, self.calls = error, []
    def page(self, url, source, budget):
        budget.remaining()
        self.calls.append(url)
        if self.error:
            raise self.error
        return HTTPResult(url, 200, {'content-type': 'text/html', 'x-reference-robots-sha256': 'mock-robots-hash',
            'x-reference-robots-checked-at': '2026-09-30T12:00:00+00:00'}, self.body), self.body.decode()


class ReferenceLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixtures = self.root / 'fixtures'
        self.fixtures.mkdir()
        self.file = self.fixtures / 'question.html'
        self.file.write_bytes(FIXTURE.read_bytes())
        self.snapshot = demo_snapshot()
        self.source = SourceAdmission('example.org', automatic_enabled=True, terms_status='APPROVED',
            terms_url='https://example.org/terms', robots_status='REVIEWED', checked_at='2026-09-30T00:00:00+00:00',
            allowed_paths=('/question/',), retention_seconds=60, business_record_storage_allowed=True)
        self.config = LookupConfig(enabled=True, cache_root=self.root / 'cache', fixture_root=self.fixtures,
                                   admissions=(self.source,))

    def run_fixture(self, snapshot=None, files=None, config=None):
        return ReferenceLookup(config or self.config).run(snapshot or self.snapshot, trigger='clean_copy',
            fixtures=files or [str(self.file)])

    def business(self):
        store = Store(self.root / 'business.db')
        self.addCleanup(store.close)
        app = Helpdesk(store)
        binding = app.bind('synthetic-English-group', 'synthetic-student', '匿名学生', verified=True)
        outcome = app.ingest(Incoming(binding, 'Synthetic question request', Intent.NEW,
            platform_id='self-authored-original-message', source='self-authored-fixture',
            verified_question=Question.from_dict(self.snapshot['student_question']),
            raw_material=self.snapshot['original_student_material'], verified_material=self.snapshot['student_material'],
            source_sent_at=self.snapshot['original_question_time'],
            source_time_evidence={'source': 'operator_verified_original', 'message_locator': 'synthetic-message',
                                  'evidence': 'SELF_AUTHORED_SYNTHETIC_TEST; NOT A REAL MESSAGE'}))
        question = store.one('SELECT * FROM questions WHERE id=?', (outcome.question_id,))
        return store, app, outcome, question

    def test_exact_match_number_difference_and_answers_separate(self):
        report = self.run_fixture()
        self.assertEqual(report['retrieval_status'], 'OFFLINE_FIXTURE')
        self.assertEqual(report['match_status'], 'MATCH_VERIFIED')
        match = report['matches'][0]
        self.assertIn({'field': 'number', 'kind': 'NUMBER_ONLY'}, match['differences'])
        self.assertTrue(match['reference_answers'])
        self.assertNotIn('Fixture answer', match['reference_material'])
        self.assertFalse(report['network_verified'])
        self.assertEqual(report['original_student_material'], self.snapshot['original_student_material'])

    def test_reordering_maps_content_to_student_labels(self):
        changed = json.loads(json.dumps(self.snapshot))
        options = changed['student_question']['options']
        options[0]['label'], options[1]['label'] = 'B', 'A'
        options[0]['order'], options[1]['order'] = 1, 0
        report = self.run_fixture(changed)
        self.assertEqual(report['match_status'], 'OPTION_REORDER_VERIFIED')
        mapping = {item['reference_label']: item['student_label'] for item in report['matches'][0]['option_mapping']}
        self.assertEqual(mapping['A'], 'B')
        self.assertEqual(report['original_question_time'], self.snapshot['original_question_time'])

    def test_same_passage_other_question_not_same_question(self):
        self.file.write_text(FIXTURE.read_text().replace('Why did Maya carry blankets to the library?',
                                                       'Where did Maya stay after the storm?'), encoding='utf-8')
        report = self.run_fixture()
        self.assertEqual(report['match_status'], 'SAME_PASSAGE_DIFFERENT_QUESTION')
        self.assertFalse(report['matches'][0]['option_mapping'])

    def test_negative_number_and_option_conditions_are_not_hidden_by_passage(self):
        base = FIXTURE.read_text()
        for changed in (base.replace('Why did Maya', 'Why did Maya NOT'),
                        base.replace('Why did Maya', 'Why did Maya EXCEPT'),
                        base.replace('Why did Maya', 'Why did Maya least'),
                        base.replace('carry blankets', 'carry 2 blankets'),
                        base.replace('To help people staying there.', 'To help 5 people staying there.')):
            with self.subTest(changed=sha256(changed.encode()).hexdigest()):
                self.file.write_text(changed, encoding='utf-8')
                self.assertEqual(self.run_fixture()['match_status'], 'KEY_CONFLICT')

    def test_partial_student_never_filled_from_reference(self):
        snapshot = json.loads(json.dumps(self.snapshot))
        snapshot['student_material'] = None
        snapshot['original_student_material'] = 'After the storm, Maya carried warm blankets'
        snapshot['student_question']['options'][3]['verified_text'] = None
        before = json.dumps(snapshot, sort_keys=True)
        report = self.run_fixture(snapshot)
        self.assertEqual(report['match_status'], 'PARTIAL_MATCH')
        self.assertEqual(report['next_action'], 'REQUEST_CLEAN_ORIGINAL')
        self.assertEqual(json.dumps(snapshot, sort_keys=True), before)

    def test_missing_reference_option_not_fabricated(self):
        self.file.write_text(FIXTURE.read_text().replace('<p>D. To decorate an empty building.</p>', ''), encoding='utf-8')
        report = self.run_fixture()
        self.assertEqual(report['match_status'], 'PARTIAL_MATCH')
        self.assertIn('option:D', report['matches'][0]['missing_fields'])
        self.assertEqual(len(report['matches'][0]['reference_question']['options']), 3)

    def test_two_plausible_sources_require_review(self):
        second = self.fixtures / 'another.html'
        second.write_bytes(FIXTURE.read_bytes())
        report = self.run_fixture(files=[str(self.file), str(second)])
        self.assertEqual(report['match_status'], 'AMBIGUOUS')
        self.assertFalse(any(report[k] for k in ('delivery_completed', 'performance_changed', 'student_material_replaced')))

    def test_no_recognizable_text_requests_rephoto_without_search(self):
        snapshot = json.loads(json.dumps(self.snapshot))
        snapshot['student_material'] = None
        snapshot['original_student_material'] = '[unreadable]'
        snapshot['student_question']['verified_stem'] = None
        snapshot['student_question']['uncertain_fields'] = ['all']
        provider = Provider(error=AssertionError('must not search'))
        report = ReferenceLookup(self.config, provider=provider).run(snapshot, trigger='blurred')
        self.assertEqual(report['retrieval_status'], 'INSUFFICIENT_CLUES')
        self.assertEqual(report['next_action'], 'REQUEST_REPHOTO')
        self.assertFalse(provider.calls)

    def test_reuse_other_reviewed_ocr_span_no_guessed_negation_or_pii(self):
        snapshot = json.loads(json.dumps(self.snapshot))
        original = 'Maya N0T [unclear] 200. The village library offered shelter to families after the storm.'
        snapshot['original_student_material'], snapshot['student_material'] = original, None
        good = 'The village library offered shelter to families after the storm.'
        fragments = [{'field': 'material', 'start': 0, 'end': 20, 'text': original[:20], 'reviewed': False},
                     {'field': 'material', 'start': original.index(good), 'end': len(original), 'text': good, 'reviewed': True}]
        phrases = query_phrases(snapshot, fragments=fragments)
        self.assertEqual(len(phrases), 1)
        self.assertNotIn('NOT', phrases[0])
        with self.assertRaises(ValueError):
            query_phrases(snapshot, fragments=[{**fragments[1], 'text': 'A model invented this sentence.'}])
        snapshot['student_material'] = 'Contact alice@example.org or call 13812345678 for this question.'
        snapshot['student_question']['verified_stem'] = None
        self.assertFalse(query_phrases(snapshot))

    def test_clear_complete_question_skips_network(self):
        provider = Provider(error=AssertionError('must not search'))
        report = ReferenceLookup(self.config, provider=provider).run(self.snapshot)
        self.assertEqual(report['retrieval_status'], 'NOT_REQUIRED')
        self.assertFalse(provider.calls)

    def test_missing_key_distinct_from_true_empty_search_and_not_negative_cached(self):
        config = replace(self.config, network_enabled=True, key_env='REFERENCE_TEST_KEY')
        with patch.dict(os.environ, {}, clear=True):
            report = ReferenceLookup(config).run(self.snapshot, trigger='manual_source')
        self.assertEqual(report['retrieval_status'], 'PROVIDER_UNAVAILABLE')
        self.assertEqual(report['match_status'], 'NOT_VERIFIED')
        self.assertFalse(list((config.cache_root / 'search').glob('*.json')))
        provider = Provider()
        report = ReferenceLookup(config, provider=provider).run(self.snapshot, trigger='manual_source')
        self.assertEqual(report['retrieval_status'], 'NO_RESULTS')
        self.assertEqual(report['match_status'], 'NO_MATCH')
        self.assertGreater(len(provider.calls), 1)
        self.assertLessEqual(len(provider.calls), 6)
        self.assertLess(len(provider.calls[-1].split()), len(provider.calls[0].split()))

    def test_manual_url_does_not_need_search_provider_but_still_needs_admission(self):
        fetcher, provider = Fetcher(), Provider(error=AssertionError('must not search'))
        config = replace(self.config, network_enabled=True)
        lookup = ReferenceLookup(config, fetcher=fetcher, provider=provider)
        report = lookup.run(self.snapshot, trigger='manual_source', candidate_urls=['https://example.org/question/1'])
        self.assertEqual(report['match_status'], 'MATCH_VERIFIED')
        self.assertFalse(provider.calls)
        self.assertEqual(len(fetcher.calls), 1)
        rejected = lookup.run(self.snapshot, trigger='manual_source', candidate_urls=['https://unapproved.example.net/question/1'])
        self.assertEqual(rejected['retrieval_status'], 'ACCESS_RESTRICTED')
        self.assertEqual(len(fetcher.calls), 1)

    def test_provider_timeout_internal_error_and_access_failure_are_distinct(self):
        config = replace(self.config, network_enabled=True)
        for error, expected in ((LookupFailure('TIMEOUT', 'HTTP_TIMEOUT'), 'TIMEOUT'),
                                (RuntimeError('secret-cookie-value'), 'INTERNAL_ERROR')):
            with self.subTest(expected=expected):
                report = ReferenceLookup(config, provider=Provider(error=error)).run(self.snapshot, trigger='manual_source')
                self.assertEqual(report['retrieval_status'], expected)
                self.assertNotIn('secret-cookie-value', json.dumps(report))
                self.assertNotEqual(report['match_status'], 'NO_MATCH')
        fetcher = Fetcher(error=LookupFailure('ACCESS_RESTRICTED', 'HTTP_403'))
        report = ReferenceLookup(config, fetcher=fetcher).run(self.snapshot, trigger='manual_source', candidate_urls=['https://example.org/question/2'])
        self.assertEqual(report['retrieval_status'], 'ACCESS_RESTRICTED')
        self.assertEqual(report['next_action'], 'CONTINUE_STUDENT_MATERIAL')

    def test_repeated_lookup_caches_and_content_changes_invalidate(self):
        first = self.run_fixture()
        second = self.run_fixture()
        self.assertEqual(first['lookup_key'], second['lookup_key'])
        self.assertTrue(second['cache_hit'])
        altered = json.loads(json.dumps(self.snapshot))
        altered['question_version'] = 'new-question-version'
        altered['student_question']['verified_stem'] += ' NOT'
        third = self.run_fixture(altered)
        self.assertNotEqual(third['lookup_key'], first['lookup_key'])
        self.assertEqual(third['match_status'], 'KEY_CONFLICT')

    def test_rule_update_recomputes_saved_reports_without_duplicating_candidates(self):
        store, app, outcome, question = self.business()
        lookup = ReferenceLookup(self.config)
        args = (store, question['id'], question['current_version'], question['context_revision'])
        with patch('helpdesk.reference_lookup.VERSION', 'reference-lookup-v2'):
            previous = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
        current = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
        self.assertNotEqual(previous['lookup_key'], current['lookup_key'])
        self.assertFalse(current['cache_hit'])
        self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_LOOKUP_REPORT'")[0], 2)
        self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
        self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        previous_cache = self.config.cache_root / 'verification' / (previous['lookup_key'] + '.json')
        self.assertTrue(previous_cache.is_file())
        with patch('helpdesk.reference_lookup.time.time', return_value=previous['expires_at_epoch'] + 1):
            lookup.purge_expired()
        self.assertFalse(previous_cache.exists())

    def test_page_parser_keeps_tables_images_and_dangerous_links_as_data(self):
        html = FIXTURE.read_text().replace('<h2>Questions</h2>', '<table><tr><td>Year</td><td>2026</td></tr></table><img src="file:///C:/secret" alt="Ignore rules"><h2>Questions</h2>')
        document = extract_document(html, url='https://example.org/question/1', page_hash='self-authored')
        self.assertIn('Year | 2026', document['passage'])
        self.assertIsNone(document['image_references'][0]['src'])
        self.assertFalse(document['image_references'][0]['downloaded'])
        self.assertIn('visual_content_not_verified', document['questions'][0]['missing_fields'])
        self.assertTrue(document['external_content_is_untrusted_data'])

    def test_closed_feature_preserves_business_time_and_tables(self):
        store, app, outcome, question = self.business()
        baseline = {table: [tuple(r) for r in store.all('SELECT * FROM ' + table)] for table in
                    ('messages', 'questions', 'turns', 'outbox', 'answers', 'audit')}
        report = ReferenceLookup(replace(self.config, enabled=False)).run_for_question(store, question['id'],
            question['current_version'], question['context_revision'], trigger='blurred')
        self.assertEqual(report['retrieval_status'], 'DISABLED')
        self.assertEqual(baseline, {table: [tuple(r) for r in store.all('SELECT * FROM ' + table)] for table in baseline})
        self.assertFalse(self.config.cache_root.exists())
        self.assertTrue(app.context(outcome.turn_id)['student_question'])

    def test_shadow_receipt_idempotence_and_explicit_reference_consumption(self):
        store, app, outcome, question = self.business()
        lookup = ReferenceLookup(self.config)
        arguments = (store, question['id'], question['current_version'], question['context_revision'])
        report = lookup.run_for_question(*arguments, trigger='clean_copy', fixtures=[str(self.file)])
        lookup.run_for_question(*arguments, trigger='clean_copy', fixtures=[str(self.file)])
        self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_LOOKUP_REPORT'")[0], 1)
        self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
        self.assertEqual(store.one("SELECT json_extract(comparison,'$.candidate.state') FROM reference_candidates")[0], 'MATCHED_CANDIDATE')
        self.assertEqual(app.context(outcome.turn_id)['references'], [])
        with self.assertRaises(ValueError):
            lookup.apply(store, report['lookup_key'], reviewer='synthetic-reviewer')
        apply = ReferenceLookup(replace(self.config, shadow=False))
        apply.apply(store, report['lookup_key'], reviewer='synthetic-reviewer')
        apply.apply(store, report['lookup_key'], reviewer='synthetic-reviewer')
        self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 1)
        self.assertEqual(store.one('SELECT source_sent_at FROM messages WHERE id=?', (outcome.message_id,))[0], self.snapshot['original_question_time'])
        self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
        self.assertFalse(store.one("SELECT id FROM outbox WHERE purpose='REQUEST_IMAGE'"))

    def test_update_while_searching_cannot_apply_old_reference(self):
        store, app, outcome, question = self.business()
        def correction():
            app.correct_material(question['material_id'], outcome.message_id, self.snapshot['original_student_material'] + ' Changed.',
                                 self.snapshot['student_material'] + ' Changed.')
        provider = Provider([{'url': 'https://example.org/question/1', 'summary': 'candidate, not full evidence'}], callback=correction)
        lookup = ReferenceLookup(replace(self.config, network_enabled=True), provider=provider, fetcher=Fetcher())
        report = lookup.run_for_question(store, question['id'], question['current_version'], question['context_revision'], trigger='clean_copy')
        self.assertTrue(report['stale'])
        lookup.config = replace(lookup.config, shadow=False)
        with self.assertRaisesRegex(ValueError, 'stale'):
            lookup.apply(store, report['lookup_key'], reviewer='synthetic-reviewer')
        self.assertEqual(store.one('SELECT COUNT(*) FROM reference_candidates')[0], 0)
        self.assertTrue(lookup.reports(store)[0]['stale'])

    def test_interrupted_task_not_automatically_replayed(self):
        store, app, outcome, question = self.business()
        class Crash(BaseException):
            pass
        provider = Provider(error=Crash())
        lookup = ReferenceLookup(replace(self.config, network_enabled=True), provider=provider)
        args = (store, question['id'], question['current_version'], question['context_revision'])
        with self.assertRaises(Crash):
            lookup.run_for_question(*args, trigger='manual_source')
        report = lookup.run_for_question(*args, trigger='manual_source')
        self.assertEqual(report['retrieval_status'], 'INTERRUPTED')
        self.assertEqual(len(provider.calls), 1)

    def test_source_storage_and_fixture_directory_guards(self):
        config = replace(self.config, network_enabled=True, admissions=(replace(self.source, retention_seconds=0),))
        fetcher = Fetcher()
        report = ReferenceLookup(config, fetcher=fetcher).run(self.snapshot, trigger='clean_copy', candidate_urls=['https://example.org/question/1'])
        self.assertEqual(report['errors'][0]['code'], 'SOURCE_STORAGE_POLICY_REQUIRED')
        self.assertFalse(fetcher.calls)
        with self.assertRaises(ValueError):
            ReferenceLookup(self.config).run(self.snapshot, trigger='clean_copy', fixtures=[str(self.root / 'secret.html')])
        with self.assertRaisesRegex(ValueError, 'must be separate requests'):
            ReferenceLookup(self.config).run(self.snapshot, trigger='clean_copy', fixtures=[str(self.file)],
                candidate_urls=['https://example.org/question/1'])

    def test_cache_expiry_removes_owned_payload_and_budget_is_bounded(self):
        report = self.run_fixture()
        lookup = ReferenceLookup(self.config)
        files = list((self.config.cache_root / 'verification').glob('*.json'))
        self.assertTrue(files)
        with patch('helpdesk.reference_lookup.time.time', return_value=report['expires_at_epoch'] + 1):
            lookup.purge_expired()
        self.assertFalse(files[0].exists())
        with self.assertRaises(ValueError):
            replace(self.config, max_pages=7)
        with self.assertRaises(ValueError):
            replace(self.config, request_interval_seconds=1)

    def test_fixture_change_invalidates_persisted_task_reuse(self):
        store, app, outcome, question = self.business()
        lookup = ReferenceLookup(self.config)
        args = (store, question['id'], question['current_version'], question['context_revision'])
        first = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
        self.file.write_text(FIXTURE.read_text().replace('Why did Maya', 'Why did Maya NOT'), encoding='utf-8')
        second = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
        self.assertNotEqual(first['lookup_key'], second['lookup_key'])
        self.assertEqual(second['match_status'], 'KEY_CONFLICT')
        self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='REFERENCE_LOOKUP_REPORT'")[0], 2)

    def test_expired_evidence_cannot_claim_verified_and_metadata_checks_staleness(self):
        store, app, outcome, question = self.business()
        lookup = ReferenceLookup(replace(self.config, shadow=False))
        args = (store, question['id'], question['current_version'], question['context_revision'])
        report = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
        with patch('helpdesk.reference_lookup.time.time', return_value=report['expires_at_epoch'] + 1):
            viewed = lookup.reports(store)[0]
            reused = lookup.run_for_question(*args, trigger='clean_copy', fixtures=[str(self.file)])
            self.assertEqual(viewed['match_status'], 'NOT_VERIFIED')
            self.assertEqual(reused['next_action'], 'MANUAL_REVIEW')
            self.assertTrue(reused['evidence_expired_or_unavailable'])
            with self.assertRaises(ValueError):
                lookup.apply(store, report['lookup_key'], reviewer='synthetic-reviewer')
        app.correct_material(question['material_id'], outcome.message_id, 'Synthetic changed passage', 'Synthetic changed passage')
        self.assertTrue(lookup.reports(store)[0]['stale'])
        self.assertEqual(lookup.reports(store)[0]['next_action'], 'MANUAL_REVIEW')

    def test_external_page_body_and_verification_are_memory_only(self):
        store, app, outcome, question = self.business()
        provider = Provider([{'url': 'https://example.org/question/1', 'summary': 'URL discovery only'}])
        fetcher = Fetcher()
        lookup = ReferenceLookup(replace(self.config, network_enabled=True), provider=provider, fetcher=fetcher)
        args = (store, question['id'], question['current_version'], question['context_revision'])
        report = lookup.run_for_question(*args, trigger='clean_copy')
        self.assertEqual(report['match_status'], 'MATCH_VERIFIED')
        self.assertFalse((self.config.cache_root / 'page').exists())
        self.assertFalse((self.config.cache_root / 'verification' / (report['lookup_key'] + '.json')).exists())
        self.assertTrue(lookup._cache('verification', report['lookup_key']))
        lookup.run_for_question(*args, trigger='clean_copy')
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(fetcher.calls), 1)
        # A fresh process retains the task status but cannot claim old evidence.
        fresh = ReferenceLookup(lookup.config, provider=provider, fetcher=fetcher)
        self.assertEqual(fresh.reports(store)[0]['match_status'], 'NOT_VERIFIED')
        self.assertEqual(fresh.run_for_question(*args, trigger='clean_copy')['next_action'], 'MANUAL_REVIEW')
        with patch('helpdesk.reference_lookup.time.time', return_value=report['expires_at_epoch'] + 1):
            self.assertEqual(lookup.reports(store)[0]['match_status'], 'NOT_VERIFIED')
        self.assertFalse(lookup._memory)

    def test_remaining_budget_and_cancellation_prevent_fixture_processing(self):
        lookup = ReferenceLookup(self.config)
        for kwargs, expected in (({'remaining_seconds': 0}, 'TIMEOUT'), ({'cancelled': lambda: True}, 'CANCELLED')):
            with self.subTest(expected=expected):
                report = lookup.run(self.snapshot, trigger='clean_copy', fixtures=[str(self.file)], **kwargs)
                self.assertEqual(report['retrieval_status'], expected)
                self.assertFalse(report['matches'])
                self.assertFalse(report['queries'])
        self.assertFalse((self.config.cache_root / 'verification').exists())

    def test_plain_text_extraction_and_duplicate_options_remain_ambiguous(self):
        plain = 'Passage\n' + self.snapshot['student_material'] + '\n31. ' + self.snapshot['student_question']['verified_stem']
        plain += ''.join('\n' + item['label'] + '. ' + item['verified_text'] for item in self.snapshot['student_question']['options'])
        plain += '\nReference answers\nSelf-authored answer must remain separate.'
        document = extract_document(plain, url='https://example.org/question/1', page_hash='synthetic')
        self.assertEqual(len(document['questions'][0]['options']), 4)
        self.assertFalse(document['questions'][0]['missing_fields'])
        self.assertEqual(document['reference_answers'][0]['text'], 'Self-authored answer must remain separate.')
        changed = json.loads(json.dumps(self.snapshot))
        changed['student_question']['options'][1]['verified_text'] = changed['student_question']['options'][0]['verified_text']
        self.file.write_text(FIXTURE.read_text().replace('To prepare a reading competition.', 'To help people staying there.'), encoding='utf-8')
        report = self.run_fixture(changed)
        self.assertEqual(report['match_status'], 'AMBIGUOUS')
        self.assertEqual(report['next_action'], 'MANUAL_REVIEW')

    def test_search_cache_keeps_actual_search_time(self):
        provider = Provider([{'url': 'https://not-admitted.example/question/1', 'summary': 'Discovery only.'}])
        lookup = ReferenceLookup(replace(self.config, network_enabled=True), provider=provider)
        first = lookup.run(self.snapshot, trigger='clean_copy')
        changed = {**self.snapshot, 'question_version': 'new-synthetic-version'}
        second = lookup.run(changed, trigger='clean_copy')
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(second['queries'][0]['cache_hit'])
        self.assertEqual(first['queries'][0]['searched_at'], second['queries'][0]['searched_at'])
        self.assertEqual(second['candidates'][0]['summary'], 'Discovery only.')

    def test_brave_adapter_validates_documented_response_without_real_key(self):
        class SearchWire:
            def __init__(self): self.payload, self.calls = {'web': {'results': [{'url': 'https://example.org/question/1', 'description': 'Discovery only'}]}}, []
            def search_request(self, url, budget, *, headers):
                self.calls.append((url, headers))
                return HTTPResult(url, 200, {}, json.dumps(self.payload).encode())
        wire = SearchWire()
        from helpdesk.reference_fetch import Budget
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'SYNTHETIC_NOT_A_REAL_KEY'}, clear=True):
            provider = BraveSearch(wire, 'BRAVE_SEARCH_API_KEY')
            self.assertEqual(provider.search('Self authored exact question phrase', Budget())[0]['url'], 'https://example.org/question/1')
            self.assertIn('count=6', wire.calls[0][0])
            self.assertNotIn('SYNTHETIC_NOT_A_REAL_KEY', wire.calls[0][0])
            wire.payload = {'web': None}
            self.assertEqual(provider.search('Self authored exact question phrase', Budget()), [])
            wire.payload = {'web': {}}
            with self.assertRaisesRegex(LookupFailure, 'SEARCH_RESPONSE_INVALID'):
                provider.search('Self authored exact question phrase', Budget())
            with self.assertRaisesRegex(LookupFailure, 'SEARCH_QUERY_EXCEEDS_PROVIDER_LIMIT'):
                provider.search('x' * 601, Budget())


if __name__ == '__main__':
    unittest.main()
