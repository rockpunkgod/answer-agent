"""Durable independent task scheduling with anonymous SQLite/mock delivery."""
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from contextlib import closing
from threading import Event, Thread
import unittest
from unittest.mock import patch

from helpdesk.__main__ import demo_question
from helpdesk.delivery import MockDesktop, NotSubmitted, RetryablePreflightFailure, SimulatedCrash
from helpdesk.delivery_tasks import AckTask, AnswerTask, AutomaticDelivery, RetryPolicy, task_for
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow, DemoGenerationAdapter


class TransientDesktop(MockDesktop):
    def __init__(self, path, failures=1):
        super().__init__(path)
        self.failures = failures
        self.preflights = 0

    def preflight(self, message):
        self.preflights += 1
        if self.preflights <= self.failures:
            raise RetryablePreflightFailure('WINDOW_UNAVAILABLE')
        super().preflight(message)


class DeliveryTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Store(self.root / 'business.db')
        self.app = Helpdesk(self.db)
        self.binding = self.app.bind('anonymous-English', 'student-1', '匿名学生', verified=True)
        self.date = datetime(2026, 10, 2, 2, tzinfo=timezone.utc)
        self.desktop = MockDesktop(self.root / 'ui.db')
        self.flow = Workflow(self.db, desktop=self.desktop)
        self.flow.set_delivery_policy({'ACK': 'AUTO', 'ANSWER': 'AUTO', 'CORRECTION': 'AUTO'}, 'approved-auto')
        self.engine = self.make_engine()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def make_engine(self, desktop=None):
        return AutomaticDelivery(self.db, desktop or self.desktop, clock=lambda: self.date)

    def question(self, identity='message-1'):
        return self.app.ingest(Incoming(self.binding, '请讲这道题', Intent.NEW,
            platform_id=identity, verified_question=demo_question(), raw_material='Passage', verified_material='Passage'))

    def test_ack_is_independent_of_question_generation_and_answer_review(self):
        original = self.question()
        ack = self.db.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (original.message_id,))
        self.assertIsInstance(task_for(ack), AckTask)
        first = self.engine.tick()
        self.assertEqual((first['kind'], first['state']), ('ACK', 'SENT_UI_CONFIRMED'))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 0)
        generated = self.flow.generate(original.turn_id)
        answer = self.db.one('SELECT * FROM outbox WHERE id=?', (generated['outbox_id'],))
        self.assertIsInstance(task_for(answer), AnswerTask)
        self.assertEqual(self.engine.tick()['state'], 'IDLE')
        self.flow.approve(answer['id'])
        self.assertEqual(self.engine.tick()['state'], 'SENT_UI_CONFIRMED')
        self.assertEqual(self.engine.tick()['state'], 'IDLE')
        self.assertEqual(len(self.desktop.receipts()), 2)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 0)

    def test_safe_retry_deadline_and_budget_survive_restart(self):
        original = self.question()
        desktop = TransientDesktop(self.root / 'flaky.db', failures=2)
        engine = self.make_engine(desktop)
        first = engine.tick()
        self.assertEqual((first['state'], first['retry_scheduled'], first['attempt']), ('FAILED', True, 1))
        self.assertEqual(engine.tick()['state'], 'IDLE')
        self.db.close()
        self.db = Store(self.root / 'business.db')
        self.app = Helpdesk(self.db)
        engine = self.make_engine(desktop)
        self.assertEqual(engine.snapshot()['ack_tasks'][0]['attempts'], 1)
        self.date += timedelta(seconds=5)
        self.assertEqual(engine.tick()['attempt'], 2)
        self.date += timedelta(seconds=9)
        self.assertEqual(engine.tick()['state'], 'IDLE')
        self.date += timedelta(seconds=1)
        final = engine.tick()
        self.assertEqual((final['state'], final['attempt']), ('SENT_UI_CONFIRMED', 3))
        self.assertEqual(len(desktop.receipts()), 1)
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE message_id=? AND purpose='ACK'", (original.message_id,))[0], 'SENT_UI_CONFIRMED')

    def test_maximum_attempts_stop_and_leave_review_record(self):
        self.question()
        desktop = TransientDesktop(self.root / 'unavailable.db', failures=100)
        engine = self.make_engine(desktop)
        for seconds in (0, 5, 10):
            self.date += timedelta(seconds=seconds)
            result = engine.tick()
        self.assertFalse(result['retry_scheduled'])
        for _ in range(3):
            self.date += timedelta(minutes=5)
            self.assertEqual(engine.tick()['state'], 'IDLE')
        self.assertEqual(desktop.preflights, 3)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM human_tasks WHERE reason LIKE 'AUTOMATIC_SEND_RETRIES_EXHAUSTED:%'")[0], 1)

    def test_unknown_send_is_never_retried(self):
        self.question()
        desktop = MockDesktop(self.root / 'unknown.db', fault='unknown')
        engine = self.make_engine(desktop)
        self.assertEqual(engine.tick()['state'], 'SEND_UNKNOWN')
        for _ in range(5):
            self.date += timedelta(minutes=5)
            self.assertEqual(engine.tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(len(desktop.receipts()), 1)

    def test_restart_after_actual_side_effect_marks_unknown_without_resending(self):
        self.question()
        desktop = MockDesktop(self.root / 'crashed.db', fault='after_send')
        with self.assertRaises(SimulatedCrash):
            self.make_engine(desktop).tick()
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE purpose='ACK'")[0], 'SENDING')
        desktop.fault = None
        self.assertEqual(self.make_engine(desktop).tick()['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.db.one("SELECT state FROM outbox WHERE purpose='ACK'")[0], 'SEND_UNKNOWN')
        self.assertEqual(len(desktop.receipts()), 1)

    def test_draft_failure_is_not_safe_retry_permission(self):
        self.question()

        class DraftDesktop(MockDesktop):
            def send(self, message):
                raise NotSubmitted('DRAFT_VERIFICATION_FAILED')

        engine = self.make_engine(DraftDesktop(self.root / 'draft.db'))
        result = engine.tick()
        self.assertEqual((result['state'], result['retry_scheduled']), ('FAILED', False))
        self.date += timedelta(minutes=5)
        self.assertEqual(engine.tick()['state'], 'IDLE')

    def test_manual_disabled_and_stop_policies_are_preserved(self):
        self.question()
        self.flow.set_delivery_policy({'ACK': 'MANUAL', 'ANSWER': 'AUTO'}, 'manual-ack')
        self.assertEqual(self.engine.tick()['state'], 'IDLE')
        self.flow.set_delivery_policy({'ACK': 'DISABLED'}, 'disabled')
        self.assertEqual(self.engine.tick()['state'], 'IDLE')
        self.flow.set_delivery_policy({'ACK': 'AUTO'}, 'automatic')
        self.flow.set_stop(True)
        self.assertEqual(self.engine.tick()['state'], 'STOPPED')
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit WHERE event='TASK_SEND_STARTED'")[0], 0)
        self.flow.set_stop(False)
        self.assertEqual(self.engine.tick()['state'], 'SENT_UI_CONFIRMED')

    def test_waiting_answer_retry_does_not_delay_new_ack(self):
        original = self.question()
        self.engine.tick()
        answer = self.flow.generate(original.turn_id)['outbox_id']
        self.flow.approve(answer)
        desktop = TransientDesktop(self.root / 'answer-retry.db')
        engine = self.make_engine(desktop)
        self.assertTrue(engine.tick()['retry_scheduled'])
        next_question = self.question('message-2')
        self.date += timedelta(seconds=5)
        result = engine.tick()
        self.assertEqual(result['kind'], 'ACK')
        ack = self.db.one("SELECT id FROM outbox WHERE message_id=? AND purpose='ACK'", (next_question.message_id,))[0]
        self.assertEqual(result['task_id'], ack)

    def test_invalid_retry_policy_is_rejected(self):
        for values in ({'max_attempts': True}, {'max_attempts': 0}, {'max_attempts': 6},
                       {'initial_retry_seconds': 0}, {'max_retry_seconds': 10000}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RetryPolicy(**values)

    def test_waiting_generation_does_not_hold_ack_sender_or_message_database(self):
        original = self.question()
        self.engine.tick()
        entered, release = Event(), Event()
        errors = []

        class WaitingGenerator(DemoGenerationAdapter):
            def generate(self, snapshot):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Synthetic bounded generator wait expired')
                return super().generate(snapshot)

        def generate():
            try:
                with closing(Store(self.db.path)) as store:
                    Workflow(store, generation_adapter=WaitingGenerator()).generate(original.turn_id)
            except BaseException as exc:
                errors.append(exc)
        worker = Thread(target=generate)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            incoming = self.question('question-during-generation')
            result = self.engine.tick()
            self.assertEqual((result['kind'], result['state']), ('ACK', 'SENT_UI_CONFIRMED'))
            self.assertEqual(self.db.one('SELECT message_id FROM outbox WHERE id=?', (result['task_id'],))[0], incoming.message_id)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_unexpected_preflight_timeout_stops_without_blind_retries(self):
        self.question()
        class UnknownTimeout(MockDesktop):
            def preflight(self, message):
                raise TimeoutError('Synthetic non-lock timeout')
        desktop = UnknownTimeout(self.root / 'timeout.db')
        result = self.make_engine(desktop).tick()
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(result['retry_scheduled'])
        self.date += timedelta(hours=1)
        self.assertEqual(self.make_engine(desktop).tick()['state'], 'IDLE')
        self.assertEqual(len(desktop.receipts()), 0)

    def test_desktop_busy_budget_exhaustion_is_visible_as_failed(self):
        self.question()
        with patch.object(self.engine.flow, 'dispatch', side_effect=TimeoutError('Resource busy; bounded wait expired')):
            for seconds in (0, 5, 10):
                self.date += timedelta(seconds=seconds)
                result = self.engine.tick()
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(result['retry_scheduled'])
        self.assertEqual(self.db.one("SELECT last_error FROM outbox WHERE purpose='ACK'")[0], 'AUTOMATIC_SEND_RETRIES_EXHAUSTED')
        self.assertEqual(len(self.desktop.receipts()), 0)


if __name__ == '__main__':
    unittest.main()
