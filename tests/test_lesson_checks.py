"""Synthetic Git/checker/model fixtures, never actual teaching or desktop proof."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from helpdesk import lesson_checks
from helpdesk.__main__ import demo_question
from helpdesk.delivery import MockDesktop
from helpdesk.domain import Intent
from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_preparation import question_text_payload, prepare_question_text
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.teaching_routes import OBJECTIVE_CHECKER
from helpdesk.test_answer_queue import validate_source_answer
from helpdesk.workflow import Workflow
from tests import test_answer_teaching as source_fixture


# This is a labelled process-contract fixture, NOT the ANSWER implementation.
SYNTHETIC_CHECKER = '''import argparse, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--source')
parser.add_argument('--draft')
parser.add_argument('--kind')
parser.add_argument('--only')
args = parser.parse_args()
if args.draft:
    flags = [{'code':'synthetic_flag','message':'SYNTHETIC CHECKER TEST ONLY'}] if 'INVALID_DRAFT' in Path(args.draft).read_text(encoding='utf-8') else []
    output = {'automatic_flags':flags, 'next_step':'SYNTHETIC DRAFT CHECK; NOT TEACHING PROOF'}
else:
    flags = []
    output = {'source_paragraphs':[{'paragraph':1,'text':Path(args.source).read_text(encoding='utf-8')}], 'next_step':'SYNTHETIC SOURCE CHECK; NOT TEACHING PROOF'}
print(json.dumps(output, ensure_ascii=False))
raise SystemExit(int(bool(flags)))
'''


class LessonCheckTests(unittest.TestCase):
    def setUp(self):
        self.source = source_fixture.AnswerTeachingTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.base = self.source.base
        self.set_checker(SYNTHETIC_CHECKER)
        self.db = Store(self.base / 'business.db')
        self.addCleanup(self.db.close)
        self.app = Helpdesk(self.db)
        self.binding = self.app.bind('synthetic-English', 'synthetic-student', '匿名', verified=True)
        self.incoming = self.app.ingest(Incoming(self.binding, 'SELF_AUTHORED_SYNTHETIC_QUESTION', Intent.NEW,
            verified_question=demo_question(), raw_material='The family needed help.',
            verified_material='The family needed help.'))
        self.desktop = MockDesktop(self.base / 'mock-desktop.db')
        self.flow = Workflow(self.db, desktop=self.desktop, teaching_manifest=self.manifest)

    def set_checker(self, code):
        (self.source.repo / OBJECTIVE_CHECKER).write_text(code, encoding='utf-8')
        self.source.git('add', '--', OBJECTIVE_CHECKER)
        self.source.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-m', 'Synthetic checker protocol fixture')
        self.source.commit = self.source.git('rev-parse', 'HEAD').decode().strip()
        self.source.write_pin()
        self.manifest = self.source.bundle(for_generation=True)

    def start(self):
        run = self.flow.start(self.incoming.turn_id)
        return run, json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])

    def result(self, context, *, text='合成测试讲解。选A。'):
        option = next(o for o in context['student_question']['options'] if o['label'] == 'A')
        return {'text': text, 'correct_option_id': option['id'], 'complete': True,
                'uploads_confirmed': True, 'session_id': context['session_id'], 'simulated': True}

    def test_workflow_runs_both_checks_and_keeps_generated_answer_pending(self):
        run, context = self.start()
        evidence = context['teaching_source_check']
        self.assertEqual(evidence['status'], 'SOURCE_READY')
        self.assertEqual(evidence['binding']['question_version'], context['question_version'])
        self.assertIn('The family needed help.', evidence['output']['source_paragraphs'][0]['text'])
        result = self.flow.finish(run, self.result(context))
        self.assertEqual(result['state'], 'GENERATED')
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (result['outbox_id'],))[0], 'PENDING')
        checks = self.db.all("SELECT event,details FROM audit WHERE run_id=? AND event LIKE 'TEACHING_%'", (run,))
        self.assertEqual([r['event'] for r in checks], ['TEACHING_SOURCE_CHECKED', 'TEACHING_DRAFT_CHECKED'])
        self.assertTrue(all(json.loads(r['details'])['teaching_correctness_proven'] is False for r in checks))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)
        self.assertEqual(list((self.base / 'lesson-check-inputs').iterdir()), [])

    def test_flags_reject_draft_even_if_model_claims_success(self):
        run, context = self.start()
        model = self.result(context, text='INVALID_DRAFT。选A。')
        model.update(confidence=1, teaching_correctness_proven=True, automatic_flags=[])
        result = self.flow.finish(run, model)
        self.assertEqual(result['state'], 'REJECTED')
        self.assertEqual(result['reason'], 'ANSWER_DRAFT_CHECK_REQUIRES_REVIEW')
        self.assertIsNone(result['outbox_id'])
        evidence = json.loads(self.db.one("SELECT details FROM audit WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'", (run,))[0])
        self.assertEqual(evidence['exit_code'], 1)
        self.assertEqual(len(evidence['output']['automatic_flags']), 1)
        self.assertTrue(self.db.one("SELECT id FROM human_tasks WHERE reason='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW'"))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_send_gate_rechecks_body_and_persisted_checks_without_rerunning_script(self):
        run, context = self.start()
        result = self.flow.finish(run, self.result(context))
        row = self.db.one('SELECT * FROM outbox WHERE id=?', (result['outbox_id'],))
        with patch('helpdesk.lesson_checks.check_lesson', side_effect=AssertionError('No checker replay at send')):
            validate_source_answer(self.db, row, approval=False)
        changed = dict(row)
        changed['body'] += 'changed after checking'
        with self.assertRaisesRegex(ValueError, 'ANSWER_DRAFT_CHECK_MISSING_OR_STALE'):
            validate_source_answer(self.db, changed, approval=False)
        audit = self.db.one("SELECT id,details FROM audit WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'", (run,))
        for change in ('missing', 'wrong-version', 'flags'):
            details = json.loads(audit['details'])
            if change == 'missing':
                details = {}
            elif change == 'wrong-version':
                details['binding']['question_version'] = 'different-version'
            else:
                details['output']['automatic_flags'] = [{'code': 'unresolved'}]
            with self.subTest(change=change):
                self.db.execute('UPDATE audit SET details=? WHERE id=?', (json.dumps(details), audit['id']))
                try:
                    with self.assertRaisesRegex(ValueError, 'ANSWER_DRAFT_CHECK_MISSING_OR_STALE'):
                        validate_source_answer(self.db, row, approval=False)
                finally:
                    self.db.execute('UPDATE audit SET details=? WHERE id=?', (audit['details'], audit['id']))

    def test_source_change_after_generation_blocks_delivery(self):
        run, context = self.start()
        result = self.flow.finish(run, self.result(context))
        row = self.db.one('SELECT * FROM outbox WHERE id=?', (result['outbox_id'],))
        original = (self.source.repo / OBJECTIVE_CHECKER).read_bytes()
        (self.source.repo / OBJECTIVE_CHECKER).write_bytes(original + b'\n# source changed after generation\n')
        with self.assertRaises(ValueError):
            validate_source_answer(self.db, row, approval=False)
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?', (row['id'],))[0], 'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_source_failure_does_not_block_independent_ack(self):
        self.set_checker('raise RuntimeError("private-source-error-must-not-be-logged")\n')
        self.flow = Workflow(self.db, desktop=self.desktop, teaching_manifest=self.manifest)
        with self.assertRaisesRegex(lesson_checks.LessonCheckError, 'ANSWER_CHECKER_FAILED'):
            self.start()
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        ack = self.db.one("SELECT * FROM outbox WHERE purpose='ACK'")
        self.assertEqual(ack['body'], '收到')
        self.assertEqual(self.flow.dispatch(ack['id']), 'SENT_UI_CONFIRMED')

    def test_current_source_check_enters_frozen_text_and_survives_restart(self):
        run, context = self.start()
        payload = question_text_payload(context)
        self.assertEqual(payload['source_localization']['output'], context['teaching_source_check']['output'])
        first = prepare_question_text(context, self.base / 'preparation')
        reopened = Store(self.base / 'business.db')
        try:
            restored = json.loads(reopened.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])
            self.assertEqual(prepare_question_text(restored, self.base / 'preparation'), first)
            self.assertEqual(lesson_checks.validate_source_check(restored), context['teaching_source_check'])
        finally:
            reopened.close()

    def test_changed_or_missing_source_check_stops_before_desktop(self):
        _, context = self.start()
        for key in ('question_version', 'context_revision', 'run_id'):
            changed = copy.deepcopy(context)
            changed[key] = changed[key] + 1 if isinstance(changed[key], int) else changed[key] + 'changed'
            with self.subTest(key=key), self.assertRaisesRegex(lesson_checks.LessonCheckError, 'MISSING_OR_STALE'):
                lesson_checks.validate_source_check(changed)
        changed = copy.deepcopy(context)
        del changed['teaching_source_check']
        transport = Mock()
        with self.assertRaisesRegex(lesson_checks.LessonCheckError, 'MISSING_OR_STALE'):
            PreparedDeepSeekGenerator(transport, self.base / 'nonexistent-preparation.json', self.base / 'generation').generate(changed)
        transport.call.assert_not_called()

    def test_version_update_preserves_old_answer_but_never_creates_send(self):
        run, context = self.start()
        material_id = self.db.one('SELECT material_id FROM questions WHERE id=?', (context['question_id'],))[0]
        self.app.correct_material(material_id, self.incoming.message_id, 'A changed source.', 'A changed source.')
        with patch('helpdesk.lesson_checks.check_lesson', side_effect=AssertionError('Stale teaching must not execute')):
            result = self.flow.finish(run, self.result(context))
        self.assertEqual(result['state'], 'STALE')
        self.assertIsNone(result['outbox_id'])
        self.assertTrue(self.db.one('SELECT id FROM answers WHERE id=?', (result['answer_id'],)))

    def test_timeout_and_malformed_checker_result_are_bounded_failures(self):
        _, context = self.start()
        original = subprocess.run
        for failure, code in [('timeout', 'TIMEOUT'), ('json', 'RESULT_INVALID'), ('exit', 'FAILED')]:
            commands = []
            def invoke(command, **kwargs):
                if command[0] == 'git':
                    return original(command, **kwargs)
                commands.append((command, kwargs))
                if failure == 'timeout':
                    raise subprocess.TimeoutExpired(command, 10)
                return subprocess.CompletedProcess(command, 2 if failure == 'exit' else 0,
                    stdout=b'not JSON', stderr=b'private details')
            with self.subTest(failure=failure), patch('helpdesk.lesson_checks.subprocess.run', side_effect=invoke):
                with self.assertRaisesRegex(lesson_checks.LessonCheckError, 'ANSWER_CHECKER_' + code):
                    lesson_checks.check_lesson(context, draft='合成测试。选A。')
            self.assertEqual(len(commands), 1)
            self.assertFalse(commands[0][1]['shell'])
            self.assertEqual(commands[0][1]['timeout'], 10)
            self.assertIn('--draft', commands[0][0])
            self.assertEqual(list((self.base / 'lesson-check-inputs').iterdir()), [])
