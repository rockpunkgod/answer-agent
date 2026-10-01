"""Actual-delivery context uses anonymous SQLite and fake pages, never live GUI."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent
from helpdesk.manual_delivery import ManualDeliveries
from helpdesk.mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
from helpdesk.mcp_page_contract import DeepSeekPage
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.service import Helpdesk, Incoming
from helpdesk.workflow import Workflow
from tests import test_mcp_preparation_text as text_fixture
from tests import test_prepared_run_resume as run_fixture
from tests.test_mcp_generation import Transport, URL as GENERATION_URL
from tests.test_mcp_preparation import FakeDesktop, URL
from tests.test_shared_source_delivery_integration import FixtureManualAdapter


class FollowupPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fx = text_fixture.TextPreparationTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.addCleanup(self.fx.tearDown)
        self.fx.snapshot.update(intent='FOLLOWUP', simulated=False, session_id='anonymous-session',
            student_words='为什么不选B？\n请看第二处证据。', previous_delivery_simulated=0,
            previous_sent_answer='老师实际回复第一部分。\n原文含 {保留字符}。',
            sent_history=[dict(body='老师实际回复第一部分。\n原文含 {保留字符}。',
                question_version='version', outbox_id='anonymous-actual-part-1',
                sent_at='2026-10-01T10:00:00+08:00', simulated=0,
                delivery_method='MANUAL_ATTESTATION', part_number=1, total_parts=2,
                attachments=[dict(name='explained.png', sha256='a'*64, bytes=123,
                                  path='Z:/unapproved-do-not-read/explained.png')])])

    def approved(self):
        self.fx.candidate()
        return self.fx.approve(self.fx.review())

    def test_uploaded_context_preserves_actual_partial_reply_and_original_words(self):
        fake, candidate = self.fx.candidate()
        text = next(f for f in candidate['files'] if f['kind'] == 'question_text')
        document = json.loads(Path(text['path']).read_bytes())
        context = document['turn_context']
        self.assertEqual(context['student_words'], self.fx.snapshot['student_words'])
        self.assertEqual(context['intent'], 'FOLLOWUP')
        delivered = context['actual_delivery']['sent_history']
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]['body'], self.fx.snapshot['previous_sent_answer'])
        self.assertEqual((delivered[0]['part_number'], delivered[0]['total_parts']), (1, 2))
        self.assertEqual(delivered[0]['question_version'], 'version')
        self.assertEqual(delivered[0]['delivery_method'], 'MANUAL_ATTESTATION')
        self.assertNotIn('path', delivered[0]['attachments'][0])
        self.assertNotIn('unapproved-do-not-read', Path(text['path']).read_text(encoding='utf-8'))
        self.assertIn(text['name'], fake.uploaded)
        self.assertEqual(len(fake.uploaded), 2)

    def test_followup_with_image_also_uploads_frozen_actual_reply_context(self):
        image = self.fx.base / 'question.png'
        image.write_bytes(b'anonymous image fixture, not a real student photo')
        self.fx.snapshot['attachments'] = [dict(path=str(image), sha256=sha256(image.read_bytes()).hexdigest())]
        controls = {**self.fx.controls, 'preparation_mode': 'FAST_UPLOAD_THEN_GENERATE'}
        fake = FakeDesktop([self.fx.course])
        candidate = DeepSeekSessionPreparer(fake, self.fx.snapshot, URL, self.fx.candidate_path,
            controls, poll_interval=0, timeout=2).run()
        self.assertEqual([f['kind'] for f in candidate['files']], ['course', 'question_image', 'question_text'])
        self.assertEqual(fake.uploaded, [f['name'] for f in candidate['files']])
        self.assertFalse(any(t == 'Shortcut' and a.get('shortcut') == 'enter' for t, a in fake.calls))
        review = self.fx.review() | dict(reviewed_image_hashes={str(image): sha256(image.read_bytes()).hexdigest()},
            image_review_statement='I inspected all frozen question images and verified the question stem.')
        approved = self.fx.approve(review)
        PreparedDeepSeekGenerator(None, self.fx.output, self.fx.base)._preparation(self.fx.snapshot)
        self.assertIn('question_text_file', approved)

    def test_rehashed_wrong_actual_reply_and_missing_context_do_not_reach_desktop(self):
        approved = self.approved()
        original_text = Path(approved['question_text_file']['path'])
        document = json.loads(original_text.read_bytes())
        document['turn_context']['actual_delivery']['sent_history'][0]['body'] = 'Another student reply.'
        original_text.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        changed = deepcopy(approved)
        changed['question_text_file']['sha256'] = sha256(original_text.read_bytes()).hexdigest()
        self.fx.output.write_text(json.dumps(changed), encoding='utf-8')
        transport = Mock()
        with self.assertRaisesRegex(ValueError, 'question text or review changed'):
            PreparedDeepSeekGenerator(transport, self.fx.output, self.fx.base/'runs').generate(self.fx.snapshot)
        transport.call.assert_not_called()
        self.assertFalse((self.fx.base/'runs/1234567890123456.json').exists())
        changed.pop('question_text_file')
        self.fx.output.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'follow-up or confirmed reference'):
            PreparedDeepSeekGenerator(transport, self.fx.output, self.fx.base/'runs').generate(self.fx.snapshot)
        transport.call.assert_not_called()

    def test_actual_followup_prompt_uses_frozen_context_and_student_option(self):
        approved = self.approved()
        class MatchingTransport(Transport):
            def call(self, tool, arguments):
                result = super().call(tool, arguments)
                for item in result.get('content', []):
                    if item.get('type') == 'text':
                        item['text'] = item['text'].replace(GENERATION_URL, URL)
                return result
        transport = MatchingTransport()
        result = PreparedDeepSeekGenerator(transport, self.fx.output, self.fx.base/'runs').generate(self.fx.snapshot)
        self.assertEqual(result['correct_option_id'], 'option-A')
        self.assertIn('本轮为同一题目的普通追问', transport.prompt)
        self.assertIn('不重复已经讲明的首轮解答', transport.prompt)
        self.assertIn(approved['question_text_file']['name'], transport.prompt)
        self.assertIn('未发送草稿', transport.prompt)
        self.assertIn('ANSWER是唯一教学来源', transport.prompt)
        self.assertIn('其余部分', transport.prompt)
        self.assertEqual(transport.calls.count('Shortcut'), 1)

    def test_simulated_previous_reply_cannot_be_uploaded_as_actual_history(self):
        self.fx.snapshot['sent_history'][0]['simulated'] = 1
        self.fx.snapshot['previous_delivery_simulated'] = 1
        fake = FakeDesktop([self.fx.course])
        with self.assertRaisesRegex(ValueError, 'Simulated delivery'):
            DeepSeekSessionPreparer(fake, self.fx.snapshot, URL, self.fx.candidate_path, self.fx.controls)
        self.assertEqual(fake.calls, [])
        self.assertFalse(self.fx.candidate_path.exists())


class FollowupExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fx = run_fixture.ResumeTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.store = self.fx.store
        # The shared resume fixture begins with a MOCK source. Create a separate
        # declared anonymous intake through the real ingest API; do not relabel it.
        app = Helpdesk(self.store)
        binding = app.bind('anonymous-delivery-group', 'anonymous-delivery-student', '匿名学生', verified=True)
        original = app.ingest(Incoming(binding, '请讲解第12题。', Intent.NEW,
            source='anonymous-intake', platform_id='anonymous-original', verified_question=demo_question(),
            raw_material='A complete anonymous passage.', verified_material='A complete anonymous passage.'))
        self.fx.turn = original.turn_id
        with patch('helpdesk.workflow.verify_bundle', return_value=self.fx.bundle):
            self.fx.run = Workflow(self.store, generation_adapter=PreparedDeepSeekGenerator(
                None, self.fx.preparation, self.fx.attempts), teaching_manifest=self.fx.manifest).start(original.turn_id)
        self.fx.snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?', (self.fx.run,))[0])
        first = Workflow(self.store).finish(self.fx.run, self.result(self.fx.snapshot))
        self.first = dict(self.store.one('SELECT * FROM outbox WHERE id=?', (first['outbox_id'],)))
        self.registry = ManualDeliveries(self.store)
        self.record = dict(question_version=self.first['question_version'], context_revision=self.first['context_revision'],
            reviewer='Anonymous human fixture', verification_evidence='Synthetic attestation, no platform receipt',
            total_parts=2)
        self.registry.register(self.first['id'], **self.record, part_number=1,
            delivered_at='2026-10-01T10:00:00+08:00', content='老师实际交付第一部分，与生成稿不同。')
        app = Helpdesk(self.store)
        outcome = app.ingest(Incoming(self.first['binding_id'], '为什么不选B？', Intent.FOLLOWUP,
            source='anonymous-followup', platform_id='anonymous-followup', quote_message_id=self.first['message_id']))
        self.fx.turn = outcome.turn_id
        with patch('helpdesk.workflow.verify_bundle', return_value=self.fx.bundle):
            self.fx.run = Workflow(self.store, generation_adapter=PreparedDeepSeekGenerator(
                None, self.fx.preparation, self.fx.attempts), teaching_manifest=self.fx.manifest).start(outcome.turn_id)
        self.fx.snapshot = json.loads(self.store.one('SELECT input_json FROM runs WHERE id=?', (self.fx.run,))[0])
        self.fx.preparation.write_text(json.dumps(dict(run_id=self.fx.run,
            input_fingerprint=input_fingerprint(self.fx.snapshot))), encoding='utf-8')

    def result(self, snapshot):
        return dict(adapter=PreparedDeepSeekGenerator.identity, simulated=False, run_id=snapshot['run_id'],
            session_id=snapshot['session_id'], web_session_evidence='anonymous page fixture, not live DeepSeek',
            uploaded_teaching_hashes={s['path']: s['sha256'] for s in snapshot['teaching_skills']},
            uploads_confirmed=True, complete=True, correct_option_id=snapshot['student_question']['options'][0]['id'],
            text='这是隔离样例的内部讲解草稿。')

    def complete_first_reply(self):
        return self.registry.register(self.first['id'], **self.record, part_number=2,
            delivered_at='2026-10-01T10:01:00+08:00', content='老师实际补充第二部分。')

    def generator(self, transport):
        # Only the page/preparation fixture is mocked; current DB state and the
        # pre-input/finish/approval guards use production code and real SQLite.
        prepared = dict(status='OPERATOR_VERIFIED_UPLOAD_AND_INPUT', session_url=GENERATION_URL,
            uploaded_teaching_hashes=self.result(self.fx.snapshot)['uploaded_teaching_hashes'],
            question_text_file=dict(name='anonymous-context.txt'))
        for stub in (patch('helpdesk.mcp_generation.verify_frozen_teaching', return_value={}),
                     patch.object(PreparedDeepSeekGenerator, '_preparation',
                                  return_value=(prepared, DeepSeekPage(GENERATION_URL)))):
            stub.start()
            self.addCleanup(stub.stop)
        return PreparedDeepSeekGenerator(transport, self.fx.preparation, self.fx.attempts)

    def test_new_actual_part_after_freeze_blocks_before_transport_startup(self):
        self.complete_first_reply()
        factory = Mock(side_effect=AssertionError('no desktop startup'))
        with self.assertRaisesRegex(ValueError, 'Current turn no longer matches frozen input'):
            self.fx.resume(factory)
        factory.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM runs WHERE id=?', (self.fx.run,))[0], 'RUNNING')

    def test_actual_reply_change_after_typing_prevents_submit_and_replay(self):
        owner = self
        class ReplyChanged(Transport):
            def call(self, tool, arguments):
                result = super().call(tool, arguments)
                if tool == 'Type':
                    owner.complete_first_reply()
                return result
        transport = ReplyChanged()
        generator = self.generator(transport)
        with self.assertRaisesRegex(ValueError, 'Current input differs'):
            generator.generate(self.fx.snapshot)
        self.assertEqual(transport.calls.count('Shortcut'), 0)
        self.assertEqual(transport.calls.count('Type'), 1)
        evidence = json.loads((self.fx.attempts/(self.fx.run+'.json')).read_bytes())
        self.assertEqual(evidence['status'], 'OUTCOME_REQUIRES_REVIEW')
        with self.assertRaises(ValueError):
            generator.generate(self.fx.snapshot)
        self.assertEqual(transport.calls.count('Type'), 1)

    def test_new_actual_reply_during_generation_rejects_output_without_pending_answer(self):
        self.complete_first_reply()
        result = Workflow(self.store).finish(self.fx.run, self.result(self.fx.snapshot))
        self.assertEqual((result['state'], result['reason']), ('REJECTED', 'GENERATION_CONTEXT_CHANGED'))
        self.assertIsNone(result['outbox_id'])
        self.assertEqual(self.store.one("SELECT COUNT(*) FROM outbox WHERE run_id=?", (self.fx.run,))[0], 0)
        self.assertEqual(Helpdesk(self.store).context(self.fx.turn)['previous_sent_answer'], '老师实际补充第二部分。')

    def test_new_actual_reply_after_generation_blocks_approval_and_staging(self):
        flow = Workflow(self.store)
        generated = flow.finish(self.fx.run, self.result(self.fx.snapshot))
        self.assertEqual(generated['state'], 'GENERATED')
        self.complete_first_reply()
        with self.assertRaisesRegex(ValueError, 'GENERATION_CONTEXT_CHANGED'):
            flow.approve(generated['outbox_id'])
        flow.set_manual_send(True)
        actor = FixtureManualAdapter(self.fx.base)
        with self.assertRaisesRegex(ValueError, 'GENERATION_CONTEXT_CHANGED'):
            flow.stage_manual_answer(generated['outbox_id'], adapter=actor)
        self.assertEqual(actor.stages, 0)
        self.assertEqual(self.store.one('SELECT state FROM outbox WHERE id=?', (generated['outbox_id'],))[0], 'PENDING')

    def test_stop_after_typing_prevents_submission(self):
        owner = self
        class Stopped(Transport):
            def call(self, tool, arguments):
                result = super().call(tool, arguments)
                if tool == 'Type':
                    owner.store.execute("UPDATE settings SET value='true' WHERE key='stop_requested'")
                return result
        transport = Stopped()
        with self.assertRaisesRegex(ValueError, '^STOPPED$'):
            self.generator(transport).generate(self.fx.snapshot)
        self.assertEqual(transport.calls.count('Shortcut'), 0)


if __name__ == '__main__':
    unittest.main()
