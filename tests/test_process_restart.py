"""Abrupt process exits over anonymous SQLite and fake browser/UI fixtures.

These exercise persisted recovery, not real DeepSeek or WeCom. The child exits
without closing SQLite or releasing locks; recovery opens the same databases.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpdesk.delivery import MockDesktop
from helpdesk.delivery_tasks import AutomaticDelivery
from helpdesk.reviewed_question_queue import advance, get
from helpdesk.storage import Store


ROOT = Path(__file__).resolve().parents[1]
CRASH_EXIT = 86
STAGES = ('SEARCHING', 'MATCH_COMPLETE', 'GENERATING', 'BEFORE_SEND',
          'PART1_SENT', 'SEND_UNKNOWN')


def _crash(workspace, stage, fixture):
    db = fixture.db
    state = {'stage': stage, 'database': db.path,
             'message_ids': [row[0] for row in db.all('SELECT id FROM messages ORDER BY rowid')],
             'run_ids': [row[0] for row in db.all('SELECT id FROM runs ORDER BY rowid')],
             'ack_ids': [row[0] for row in db.all("SELECT id FROM outbox WHERE purpose='ACK' ORDER BY rowid")]}
    if stage in ('PART1_SENT', 'SEND_UNKNOWN'):
        state.update(outbox_id=fixture.oid, ui_database=fixture.fx.desktop.path,
                     receipts=fixture.fx.desktop.receipts())
    else:
        state.update(task_id=fixture.task['id'], run_id=fixture.task['run_id'],
                     source_pin=str(fixture.source.pin))
    with (workspace / 'checkpoint.json').open('x', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(CRASH_EXIT)


def _crash_worker(stage, workspace):
    # Every fixture directory stays under the parent's disposable workspace.
    # No cleanup/finally runs when the intended interruption occurs.
    tempfile.tempdir = str(workspace.resolve(strict=True))
    if stage in ('PART1_SENT', 'SEND_UNKNOWN'):
        from tests.test_delivery_batches import BatchTests
        fixture = BatchTests()
        fixture.setUp()
        if fixture.fx.engine.tick()['state'] != 'PART_DELIVERED':
            raise AssertionError('Synthetic first part was not confirmed')
        if stage == 'SEND_UNKNOWN':
            fixture.fx.desktop.fault = 'unknown'
            if fixture.fx.engine.tick()['state'] != 'SEND_UNKNOWN':
                raise AssertionError('Synthetic second part was not unknown')
        _crash(workspace, stage, fixture)

    from tests.test_automatic_answer_runtime import AutomaticAnswerTests
    from helpdesk import question_matching
    fixture = AutomaticAnswerTests()
    if stage == 'SEARCHING':
        # Set up the original run before its first search, without clearing or
        # recreating any task/database as part of the subsequent recovery.
        def before_search(store, run_id, lookup, **kwargs):
            return json.loads(store.one('SELECT input_json FROM runs WHERE id=?', (run_id,))[0])
        with patch.object(question_matching, 'prepare_input', side_effect=before_search):
            fixture.setUp()
        from helpdesk.reference_lookup import ReferenceLookup
        with patch.object(ReferenceLookup, 'run_for_question',
                          side_effect=lambda *a, **k: _crash(workspace, stage, fixture)):
            fixture.runtime.tick()
    else:
        fixture.setUp()
        if fixture.runtime.tick()['state'] != 'READY_FOR_PREPARATION':
            raise AssertionError('Synthetic session was not created')
        if stage == 'MATCH_COMPLETE':
            record = question_matching.record_result
            def after_matching(*args, **kwargs):
                record(*args, **kwargs)
                _crash(workspace, stage, fixture)
            with patch.object(question_matching, 'record_result', side_effect=after_matching):
                fixture.runtime.tick()
        else:
            if fixture.runtime.tick()['state'] != 'ATTACHMENTS_READY':
                raise AssertionError('Synthetic attachments were not ready')
            if stage == 'GENERATING':
                original = fixture.desktop.call
                def after_submission(tool, arguments):
                    result = original(tool, arguments)
                    if (tool == 'Shortcut' and arguments.get('shortcut') == 'enter'
                            and 'BEGIN_answer_run_' in fixture.desktop.prompt):
                        _crash(workspace, stage, fixture)
                    return result
                with patch.object(fixture.desktop, 'call', side_effect=after_submission):
                    fixture.runtime.tick()
            else:
                from helpdesk.workflow import Workflow
                finish = Workflow.finish
                def after_draft(flow, *args, **kwargs):
                    outcome = finish(flow, *args, **kwargs)
                    if outcome['state'] != 'GENERATED':
                        raise AssertionError('Synthetic draft failed: ' + repr(outcome))
                    _crash(workspace, stage, fixture)
                with patch.object(Workflow, 'finish', new=after_draft):
                    fixture.runtime.tick()
    raise AssertionError('Requested crash point was not reached')


class ProcessRestartTests(unittest.TestCase):
    def test_six_abrupt_exits_reuse_records_without_replaying_uncertain_actions(self):
        for stage in STAGES:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory).resolve()
                result = subprocess.run(
                    [sys.executable, '-X', 'utf8', '-B', '-m', 'tests.test_process_restart',
                     '--crash-worker', stage, str(workspace)],
                    cwd=ROOT, capture_output=True, encoding='utf-8', timeout=45,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.assertEqual(result.returncode, CRASH_EXIT, result.stdout + result.stderr)
                saved = json.loads((workspace / 'checkpoint.json').read_text(encoding='utf-8'))
                database = Path(saved['database']).resolve(strict=True)
                self.assertTrue(database.is_relative_to(workspace))
                with closing(Store(database)) as store:
                    self.assertEqual(store.one('PRAGMA integrity_check')[0], 'ok')
                    if stage in ('PART1_SENT', 'SEND_UNKNOWN'):
                        self._recover_delivery(store, saved, stage)
                    else:
                        self._recover_generation(store, saved, stage)
                    self.assertEqual([r[0] for r in store.all('SELECT id FROM messages ORDER BY rowid')], saved['message_ids'])
                    self.assertEqual([r[0] for r in store.all('SELECT id FROM runs ORDER BY rowid')], saved['run_ids'])
                    self.assertEqual([r[0] for r in store.all("SELECT id FROM outbox WHERE purpose='ACK' ORDER BY rowid")], saved['ack_ids'])
                    self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def _recover_generation(self, store, saved, stage):
        from helpdesk import answer_teaching
        pin = Path(saved['source_pin']).resolve(strict=True)
        self.assertEqual(pin.parent, Path(store.path).parent)
        # A fresh process must load the SAME synthetic source configuration,
        # rather than compare this fixture to the real user's ANSWER pin.
        with patch.object(answer_teaching, 'PIN_CONFIG', pin):
            self._recover_generation_with_original_pin(store, saved, stage)

    def _recover_generation_with_original_pin(self, store, saved, stage):
        forbidden = Mock(side_effect=AssertionError('Interrupted side effects must not be replayed'))
        phases = []
        for _ in range(2):
            outcome = advance(store, saved['task_id'], executor='LUNA',
                              session_creator=forbidden, preparer=forbidden, generator=forbidden)
            phases.append(outcome['phase'])
        forbidden.assert_not_called()
        run = store.one('SELECT state FROM runs WHERE id=?', (saved['run_id'],))
        if stage == 'BEFORE_SEND':
            self.assertEqual(phases, ['GENERATED', 'GENERATED'], repr(outcome))
            self.assertEqual(run[0], 'GENERATED')
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 1)
            self.assertEqual(store.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER' AND state='PENDING'")[0], 1)
            self.assertEqual(get(store, saved['task_id'])['attempts'][-1]['state'], 'RECOVERED_FROM_RUN')
        else:
            if stage in ('SEARCHING', 'GENERATING'):
                self.assertEqual(phases, ['EXECUTION_UNCERTAIN', 'EXECUTION_UNCERTAIN'], repr(outcome))
                interrupted_stage = 'SESSION_CREATION' if stage == 'SEARCHING' else 'GENERATION'
                self.assertEqual(outcome['last_error'], interrupted_stage + '_NOT_REPLAYED')
                self.assertEqual(outcome['attempts'][-1]['state'], 'UNCERTAIN')
            else:
                self.assertEqual(phases, ['NEEDS_ATTENTION', 'NEEDS_ATTENTION'], repr(outcome))
            self.assertEqual(run[0], 'RUNNING')
            self.assertEqual(store.one('SELECT COUNT(*) FROM answers')[0], 0)
            self.assertEqual(store.one("SELECT COUNT(*) FROM outbox WHERE purpose='ANSWER'")[0], 0)
        self.assertEqual(store.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        if stage == 'MATCH_COMPLETE':
            from helpdesk.question_matching import validate_receipt
            frozen = json.loads(store.one('SELECT input_json FROM runs WHERE id=?', (saved['run_id'],))[0])
            self.assertEqual(validate_receipt(store, frozen), frozen['question_match_result'])
            self.assertEqual(store.one("SELECT COUNT(*) FROM audit WHERE event='QUESTION_MATCH_VERIFIED'")[0], 1)
        if stage == 'GENERATING':
            from helpdesk.call_costs import task_cost
            usage = task_cost(store, saved['run_id'])
            self.assertEqual(usage['call_attempts']['DEEPSEEK_MATCH'], 1)
            self.assertEqual(usage['call_attempts']['DEEPSEEK_TEACH'], 1)
            self.assertEqual(usage['retry_calls'], 0)

    def _recover_delivery(self, store, saved, stage):
        from helpdesk.delivery_batches import PLAN_EVENT, progress, read_plan
        desktop = MockDesktop(saved['ui_database'])
        engine = AutomaticDelivery(store, desktop, clock=lambda: datetime.now(timezone.utc))
        row = lambda: store.one('SELECT * FROM outbox WHERE id=?', (saved['outbox_id'],))
        if stage == 'SEND_UNKNOWN':
            with patch.object(desktop, 'send', side_effect=AssertionError('Unknown send must not retry')) as send:
                for _ in range(2):
                    self.assertEqual(engine.tick()['state'], 'NEEDS_ATTENTION')
                send.assert_not_called()
            self.assertEqual(row()['state'], 'SEND_UNKNOWN')
            self.assertEqual(progress(store, row())['unknown'], 2)
            self.assertEqual(desktop.receipts(), saved['receipts'])
        else:
            plan = read_plan(store, row())
            self.assertGreaterEqual(len(plan['parts']), 3)
            first = engine.tick()
            self.assertEqual(first['part_number'], 2)
            for _ in range(len(plan['parts']) - 2):
                engine.tick()
            self.assertEqual(row()['state'], 'SENT_UI_CONFIRMED')
            self.assertEqual(engine.tick()['state'], 'IDLE')
            receipts = [r for r in desktop.receipts() if r['outbox_id'].startswith(saved['outbox_id'])]
            self.assertEqual(len(receipts), len(plan['parts']))
            self.assertEqual(''.join(r['body'] for r in receipts), row()['body'])
        self.assertEqual(store.one('SELECT COUNT(*) FROM audit WHERE event=? AND outbox_id=?',
                                  (PLAN_EVENT, saved['outbox_id']))[0], 1)
        self.assertEqual(len([r for r in desktop.receipts() if r['outbox_id'] in saved['ack_ids']]), 1)
        self.assertEqual(len([r for r in desktop.receipts() if r['outbox_id'] == saved['outbox_id'] + ':part:1']), 1)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--crash-worker' and sys.argv[2] in STAGES:
        _crash_worker(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
