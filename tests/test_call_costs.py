"""Synthetic billing, anonymous SQLite and injected adapters only; no paid calls."""
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from helpdesk import call_costs as costs
from helpdesk.domain import Intent
from helpdesk.reference_lookup import ReferenceLookup
from helpdesk.service import Incoming
from helpdesk.storage import Store, encode
from helpdesk.workflow import Workflow
from tests import test_performance as ledger_fixture
from tests import test_reference_lookup as lookup_fixture
from tools.report_task_costs import main


class CallCostsTests(unittest.TestCase):
    def setUp(self):
        self.fx = ledger_fixture.PerformanceTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.db, self.root = self.fx.db, Path(self.fx.tmp.name)
        self.receipts = self.root / 'approved-billing'
        self.receipts.mkdir()
        self.serial = 0
        self.flow = Workflow(self.db)

    def task(self, *, completed=False):
        self.serial += 1
        outcome, unit = self.fx.question('2026-09-30T23:10:00+08:00', material=f'Synthetic passage {self.serial}')
        run = self.flow.start(outcome.turn_id)
        if completed:
            self.fx.real_delivery(outcome, unit, '2026-10-01T00:20:00+08:00')
            self.fx.ledger.confirm(unit, reviewer='SYNTHETIC reviewer', evidence='SYNTHETIC counted passage')
        return run, outcome, unit

    def receipt(self, text='SYNTHETIC billing evidence; not an actual invoice'):
        self.serial += 1
        path = self.receipts / f'bill-{self.serial}.txt'
        path.write_text(text + f'\nfixture record {self.serial}', encoding='utf-8')
        return path

    def call(self, run, *, attempt='attempt-1', request='query-1', kind='SEARCH'):
        return costs.start_call(self.db, run, kind, 'fixture_provider', request, attempt, injected=True)

    def price(self, run, call, amount, receipt=None, **kwargs):
        costs.confirm_charge(self.db, run, call, amount, receipt or self.receipt(),
                             self.receipts, 'SYNTHETIC billing reviewer', **kwargs)

    def accounted(self, run, amount):
        call = self.call(run)
        costs.finish_call(self.db, run, call, 'CONFIRMED', 'SYNTHETIC response observed')
        self.price(run, call, amount)
        for kind in costs.KINDS:
            costs.close_stage(self.db, run, kind, 'SYNTHETIC measured scope', injected=True)
        return call

    def test_missing_capture_and_charge_are_unknown_never_free(self):
        run, _, _ = self.task(completed=True)
        value = costs.task_cost(self.db, run)
        self.assertIsNone(value['search_provider_count'])
        self.assertIsNone(value['paid_calls'])
        self.assertTrue(all(v is None for v in value['call_attempts'].values()))
        self.assertIsNone(value['total_cny'])
        call = self.call(run)
        costs.finish_call(self.db, run, call, 'UNKNOWN', 'SYNTHETIC uncertain submission')
        costs.close_stage(self.db, run, 'SEARCH', 'SYNTHETIC adapter ended', injected=True)
        value = costs.task_cost(self.db, run)
        self.assertEqual(value['call_attempts']['SEARCH'], 1)
        self.assertIn('CHARGE_UNVERIFIED:' + call, value['unverified'])
        report = costs.report(self.db)
        self.assertEqual(report['limit_status'], 'UNVERIFIED')
        self.assertIsNone(report['average_cny'])

    def test_started_call_survives_restart_and_does_not_authorize_replay(self):
        run, _, _ = self.task()
        call = self.call(run)
        other = Store(self.db.path)
        self.addCleanup(other.close)
        self.assertEqual(costs.task_cost(other, run)['calls'][0]['status'], 'UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'ALREADY_STARTED'):
            costs.start_call(other, run, 'SEARCH', 'fixture_provider', 'query-1', 'attempt-1', injected=True)
        self.assertEqual(len(costs.task_cost(other, run)['calls']), 1)
        with self.assertRaisesRegex(ValueError, 'RESULT_PENDING'):
            costs.close_stage(other, run, 'SEARCH', 'SYNTHETIC scope')
        costs.finish_call(other, run, call, 'UNKNOWN', 'SYNTHETIC interrupted call')
        retry = self.call(run, attempt='attempt-2')
        costs.finish_call(self.db, run, retry, 'CONFIRMED', 'SYNTHETIC explicit retry response')
        self.assertEqual(costs.task_cost(other, run)['retry_calls'], 1)

    def test_observed_result_and_fee_are_idempotent_and_immutable(self):
        run, _, _ = self.task()
        call = self.call(run)
        receipt = self.receipt()
        for _ in range(2):
            costs.finish_call(self.db, run, call, 'UNKNOWN', 'SYNTHETIC uncertain result')
            self.price(run, call, '0.20', receipt)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (costs.RESULT,))[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (costs.CHARGE,))[0], 1)
        self.assertEqual(costs.task_cost(self.db, run)['known_subtotal_cny'], '0.20')
        with self.assertRaisesRegex(ValueError, 'RESULT_CHANGED'):
            costs.finish_call(self.db, run, call, 'CONFIRMED', 'Different assertion')
        with self.assertRaisesRegex(ValueError, 'VERIFIED_DIFFERENTLY'):
            self.price(run, call, '0.21', receipt)

    def test_receipt_cannot_be_double_allocated_to_another_task(self):
        first, _, _ = self.task()
        second, _, _ = self.task()
        one, two = self.call(first), self.call(second)
        receipt = self.receipt()
        self.price(first, one, '0.10', receipt)
        copy = self.receipts / 'copy.txt'
        copy.write_bytes(receipt.read_bytes())
        with self.assertRaisesRegex(ValueError, 'ITEM_ALREADY_ALLOCATED'):
            self.price(second, two, '0.10', copy)
        with self.assertRaisesRegex(ValueError, 'ITEM_ALREADY_ALLOCATED'):
            self.price(second, two, '0.10', copy, item_ref='line-2')
        self.assertEqual(costs.task_cost(self.db, second)['known_subtotal_cny'], '0')

    def test_explicit_invoice_lines_are_distinct_but_whole_receipt_is_not_reusable(self):
        run, _, _ = self.task()
        one, two, three = self.call(run), self.call(run, attempt='attempt-2'), self.call(run, attempt='attempt-3')
        receipt = self.receipt('SYNTHETIC invoice lines: line-1=0.10, line-2=0.20')
        self.price(run, one, '0.10', receipt, item_ref='line-1')
        self.price(run, two, '0.20', receipt, item_ref='line-2')
        with self.assertRaisesRegex(ValueError, 'ITEM_ALREADY_ALLOCATED'):
            self.price(run, three, '0.10', receipt, item_ref='line-1')
        with self.assertRaisesRegex(ValueError, 'ITEM_ALREADY_ALLOCATED'):
            self.price(run, three, '0.30', receipt)
        self.assertEqual(costs.task_cost(self.db, run)['known_subtotal_cny'], '0.30')

    def test_changed_missing_or_outside_billing_proof_is_not_accepted(self):
        run, _, _ = self.task()
        call = self.call(run)
        outside = self.root / 'outside-bill.txt'
        outside.write_text('SYNTHETIC outside receipt', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_APPROVED_DIRECTORY'):
            self.price(run, call, '0.10', outside)
        receipt = self.receipt()
        self.price(run, call, '0.10', receipt)
        receipt.write_text('Changed synthetic evidence', encoding='utf-8')
        self.assertIsNone(costs.task_cost(self.db, run)['calls'][0]['amount_cny'])
        receipt.unlink()
        self.assertIsNone(costs.task_cost(self.db, run)['calls'][0]['amount_cny'])

    def test_invalid_values_and_foreign_identifiers_do_not_create_events(self):
        run, _, _ = self.task()
        call = self.call(run)
        baseline = self.db.one('SELECT COUNT(*) FROM audit')[0]
        for amount in ('-0.1', 'NaN', 'Infinity', '1000001', 0.1, True):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.price(run, call, amount)
        with self.assertRaisesRegex(ValueError, 'RUN_NOT_FOUND'):
            self.call('not-an-existing-run')
        with self.assertRaisesRegex(ValueError, 'CALL_NOT_FOUND'):
            costs.finish_call(self.db, run, 'fabricated-call', 'CONFIRMED', 'SYNTHETIC proof')
        with self.assertRaisesRegex(ValueError, 'IDENTIFIER_INVALID'):
            self.call(run, request='student raw text / secret')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit')[0], baseline)

    def test_tampered_snapshot_cannot_change_cost_owner(self):
        run, _, _ = self.task()
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        snapshot['binding_id'] = 'another-student'
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encode(snapshot), run))
        with self.assertRaisesRegex(ValueError, 'RUN_BINDING_CHANGED'):
            self.call(run)

    def test_explicit_no_call_reconciliation_remains_manual_and_rechecks_proof(self):
        run, _, _ = self.task()
        receipt = self.receipt('SYNTHETIC reconciliation: scheduler and other calls absent')
        for _ in range(2):
            for kind in ('SCHEDULER', 'OTHER'):
                costs.confirm_no_calls(self.db, run, kind, receipt, self.receipts, 'SYNTHETIC reviewer')
        value = costs.task_cost(self.db, run)
        self.assertEqual(value['call_attempts']['SCHEDULER'], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='ANSWER_COST_SCOPE_RECONCILED'")[0], 2)
        receipt.write_text('Changed synthetic reconciliation', encoding='utf-8')
        self.assertIn('COVERAGE_PROOF_CHANGED:SCHEDULER', costs.task_cost(self.db, run)['unverified'])
        self.call(run, kind='SCHEDULER')
        with self.assertRaisesRegex(ValueError, 'CALLS_EXIST'):
            costs.confirm_no_calls(self.db, run, 'SCHEDULER', receipt, self.receipts, 'SYNTHETIC reviewer')

    def test_statistics_use_delivered_passages_and_configured_limit(self):
        for amount in ('0.20', '0.40', '0.60', '0.80'):
            run, _, _ = self.task(completed=True)
            self.accounted(run, amount)
        result = costs.report(self.db)
        self.assertEqual(result['passage_count'], 4)
        self.assertEqual(Decimal(result['average_cny']), Decimal('0.50'))
        self.assertEqual(Decimal(result['p50_cny']), Decimal('0.50'))
        self.assertEqual(Decimal(result['p95_cny']), Decimal('0.80'))
        self.assertEqual(Decimal(result['max_cny']), Decimal('0.80'))
        self.assertEqual(result['limit_status'], 'WITHIN_LIMIT')
        self.assertTrue(result['injected_evidence'])
        self.assertEqual(costs.report(self.db, max_average_cny='0.49')['limit_status'], 'EXCEEDED')
        self.assertEqual(len(result['high_cost_runs']), 2)
        self.task(completed=True)
        unknown = costs.report(self.db)
        self.assertEqual(unknown['limit_status'], 'UNVERIFIED')
        self.assertIsNone(unknown['average_cny'])
        self.assertEqual(unknown['verified_cost_passages'], 4)

    def test_draft_unknown_delivery_and_multi_passage_allocation_do_not_claim_completion(self):
        run, outcome, unit = self.task()
        self.accounted(run, '0.20')
        self.assertEqual(costs.report(self.db)['passage_count'], 0)
        oid = self.fx.real_delivery(outcome, unit, '2026-10-01T00:20:00+08:00')
        self.fx.ledger.confirm(unit, reviewer='SYNTHETIC', evidence='SYNTHETIC verification')
        self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN' WHERE id=?", (oid,))
        result = costs.report(self.db)
        self.assertIn('DELIVERY_NOT_VERIFIED', result['passages'][0]['unverified'])
        self.assertEqual(result['limit_status'], 'UNVERIFIED')
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED' WHERE id=?", (oid,))
        self.db.execute('UPDATE performance_units SET confirmed_quantity=2 WHERE id=?', (unit,))
        self.assertIn('MULTI_PASSAGE_COST_ALLOCATION_UNCONFIRMED', costs.report(self.db)['passages'][0]['unverified'])

    def test_followup_costs_share_original_unit_and_report_is_readonly_repeatable(self):
        run, original, unit = self.task(completed=True)
        self.accounted(run, '0.30')
        snapshot = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
        self.flow.finish(run, self.flow.generation_adapter.generate(snapshot))
        follow = self.fx.app.ingest(Incoming(self.fx.person, 'SYNTHETIC followup', Intent.FOLLOWUP,
                                             quote_message_id=original.message_id))
        self.fx.ledger.link_activity(unit, follow.message_id, question_id=original.question_id,
                                    kind='FOLLOWUP', reason='SYNTHETIC same question')
        next_run = self.flow.start(follow.turn_id)
        self.accounted(next_run, '0.05')
        baseline = list(self.db.connection.iterdump())
        result = costs.report(self.db)
        self.assertEqual(result['passage_count'], 1)
        self.assertEqual(set(result['passages'][0]['run_ids']), {run, next_run})
        self.assertEqual(Decimal(result['average_cny']), Decimal('0.35'))
        self.assertEqual(costs.report(self.db), result)
        self.assertEqual(list(self.db.connection.iterdump()), baseline)

    def test_readonly_cli_does_not_create_or_migrate_database(self):
        missing = self.root / 'missing.db'
        with self.assertRaises(FileNotFoundError):
            main(['--db', str(missing)])
        self.assertFalse(missing.exists())
        self.task(completed=True)
        baseline = list(self.db.connection.iterdump())
        output = StringIO()
        with patch.object(Store, '_migrate', side_effect=AssertionError('Readonly tool must not migrate')), redirect_stdout(output):
            main(['--db', self.db.path])
        self.assertEqual(json.loads(output.getvalue())['limit_status'], 'UNVERIFIED')
        self.assertEqual(list(self.db.connection.iterdump()), baseline)

    def test_unallocated_task_cost_cannot_be_hidden_from_threshold(self):
        run, _, _ = self.task(completed=True)
        self.accounted(run, '0.10')
        orphan, _, _ = self.task()
        self.accounted(orphan, '0.80')
        result = costs.report(self.db)
        self.assertEqual(result['unallocated_runs'], [orphan])
        self.assertEqual(result['limit_status'], 'UNVERIFIED')
        self.assertIsNone(result['average_cny'])
        self.assertIn(orphan, result['high_cost_runs'])
        self.assertEqual(costs.task_cost(self.db, orphan)['paid_calls'], 1)

    def test_meter_does_not_recreate_a_missing_database(self):
        path = self.root / 'isolated-meter-fixture.db'
        isolated = Store(path)
        isolated.close()
        meter = costs.RunMeter(path, 'fixture-run', injected=True)
        path.unlink()  # Closed, self-owned test DB only.
        with self.assertRaises(FileNotFoundError):
            meter.start('SEARCH', 'fixture_provider', 'query', 'attempt')
        self.assertFalse(path.exists())


class LookupUsageTests(unittest.TestCase):
    def setUp(self):
        self.fx = lookup_fixture.ReferenceLookupTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.db, _, outcome, self.question = self.fx.business()
        self.run = Workflow(self.db).start(outcome.turn_id)

    def lookup(self, lookup, **kwargs):
        return lookup.run_for_question(self.db, self.question['id'], self.question['current_version'],
                                       self.question['context_revision'], run_id=self.run,
                                       trigger='initial_question', **kwargs)

    def test_provider_fallback_actual_attempts_and_cache_do_not_double_count(self):
        first = lookup_fixture.Provider(error=RuntimeError('private fixture detail'))
        backup = lookup_fixture.Provider([{'url': 'https://example.org/question/backup'}])
        backup.identity = 'MOCK_BACKUP'
        lookup = ReferenceLookup(replace(self.fx.config, network_enabled=True),
                                 providers=[first, backup], fetcher=lookup_fixture.Fetcher())
        self.lookup(lookup)
        value = costs.task_cost(self.db, self.run)
        self.assertEqual(value['call_attempts']['SEARCH'], 2)
        self.assertEqual(value['search_provider_count'], 2)
        self.assertEqual([c['status'] for c in value['calls']], ['UNKNOWN', 'CONFIRMED'])
        self.assertTrue(value['injected_evidence'])
        self.assertNotIn('private fixture detail', encode(value))
        self.lookup(lookup)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(backup.calls), 1)
        self.assertEqual(costs.task_cost(self.db, self.run)['call_attempts']['SEARCH'], 2)

    def test_explicit_provider_retry_counts_new_attempt_cache_hit_does_not(self):
        provider = lookup_fixture.Provider(error=RuntimeError('SYNTHETIC provider unavailable'))
        lookup = ReferenceLookup(replace(self.fx.config, network_enabled=True), provider=provider)
        self.lookup(lookup)
        self.lookup(lookup, retry=True)
        self.assertEqual(len(provider.calls), 2)
        value = costs.task_cost(self.db, self.run)
        self.assertEqual(value['retry_calls'], 1)
        self.assertEqual(value['call_attempts']['SEARCH'], 2)

    def test_missing_key_and_disabled_source_are_not_external_calls(self):
        lookup = ReferenceLookup(replace(self.fx.config, network_enabled=True))
        with patch.dict('os.environ', {}, clear=True):
            result = self.lookup(lookup)
        self.assertEqual(result['retrieval_status'], 'PROVIDER_UNAVAILABLE')
        self.assertEqual(costs.task_cost(self.db, self.run)['call_attempts']['SEARCH'], 0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM audit WHERE event=?', (costs.START,))[0], 0)
        self.db.execute('DELETE FROM audit WHERE event=?', (costs.COVERAGE,))
        self.lookup(ReferenceLookup(replace(self.fx.config, enabled=False)))
        self.assertIsNone(costs.task_cost(self.db, self.run)['call_attempts']['SEARCH'])


if __name__ == '__main__':
    unittest.main()
