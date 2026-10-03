"""Separate student/question chats persist across process restarts."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent, Option
from helpdesk.service import Helpdesk, Incoming
from helpdesk.session_isolation import claim_deepseek_chat
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


URL = 'https://chat.deepseek.com/a/chat/s/isolated-question'


class QuestionSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'business.db'
        self.db = Store(self.path)
        self.app = Helpdesk(self.db)
        self.flow = Workflow(self.db)
        self.student = self.app.bind('group', 'member1', '同名学生', verified=True)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def first(self, student=None):
        return self.app.ingest(Incoming(student or self.student, '请讲12题', Intent.NEW,
            verified_question=demo_question(), raw_material='Passage', verified_material='Passage'))

    def start(self, turn):
        run = self.flow.start(turn.turn_id)
        return json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (run,))[0])

    def test_two_independent_questions_in_one_case_get_distinct_sessions(self):
        first = self.first()
        a = self.start(first)
        second = self.app.ingest(Incoming(self.student, '请讲另一小题', Intent.SUBQUESTION,
            quote_message_id=first.message_id, verified_question=demo_question()))
        b = self.start(second)
        self.assertEqual(first.case_id, second.case_id)
        self.assertNotEqual(a['session_id'], b['session_id'])
        claim_deepseek_chat(a, URL)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_CHAT_ALREADY_OWNED'):
            claim_deepseek_chat(b, URL)

    def test_pending_webpage_checks_business_owner_without_reserving_a_root_url(self):
        a = self.start(self.first())
        claim_deepseek_chat(a, None, reserve=False)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM deepseek_chats')[0], 0)
        for url in (None, 'https://chat.deepseek.com/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                claim_deepseek_chat(a, url)
        self.db.execute('DELETE FROM session_owners WHERE session_id=?', (a['session_id'],))
        with self.assertRaisesRegex(ValueError, 'SESSION_OWNERSHIP_MISSING'):
            claim_deepseek_chat(a, None, reserve=False)

    def test_existing_chat_cannot_be_treated_as_unbound_for_another_first_request(self):
        a = self.start(self.first())
        claim_deepseek_chat(a, URL)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_SESSION_URL_CHANGED'):
            claim_deepseek_chat(a, None, reserve=False)
        self.assertEqual(self.db.one('SELECT session_url FROM deepseek_chats')[0], URL)

    def test_two_students_cannot_reuse_browser_chat_after_restart(self):
        a = self.start(self.first())
        other = self.app.bind('group', 'member2', '同名学生', verified=True)
        b = self.start(self.first(other))
        claim_deepseek_chat(a, URL)
        self.db.close()
        self.db = Store(self.path)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_CHAT_ALREADY_OWNED'):
            claim_deepseek_chat(b, URL)
        forged = dict(b, session_id=a['session_id'])
        with self.assertRaisesRegex(ValueError, 'SESSION_OWNERSHIP_MISMATCH'):
            claim_deepseek_chat(forged, URL)

    def test_followup_resumes_owned_chat_and_refuses_different_url(self):
        first = self.first()
        a = self.start(first)
        claim_deepseek_chat(a, URL)
        self.flow.finish(a['run_id'], self.flow.generation_adapter.generate(a))
        follow = self.app.ingest(Incoming(self.student, '为什么不选B', Intent.FOLLOWUP,
                                         quote_message_id=first.message_id))
        b = self.start(follow)
        self.assertEqual(a['session_id'], b['session_id'])
        claim_deepseek_chat(b, URL)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_SESSION_URL_CHANGED'):
            claim_deepseek_chat(b, URL + '-other')

    def test_reordering_options_keeps_question_session(self):
        first = self.first()
        a = self.start(first)
        self.flow.finish(a['run_id'], self.flow.generation_adapter.generate(a))
        question = demo_question()
        reordered = replace(question, options=tuple(Option.confirmed(label, option.verified_text, i, 'reordered')
            for i, (label, option) in enumerate(zip('ABCD', reversed(question.options)))))
        correction = self.app.ingest(Incoming(self.student, '选项顺序更正', Intent.CORRECTION,
            quote_message_id=first.message_id, verified_question=reordered))
        b = self.start(correction)
        self.assertEqual(a['session_id'], b['session_id'])

    def test_legacy_mixed_session_is_preserved_but_not_resumed(self):
        first = self.first()
        a = self.start(first)
        self.flow.finish(a['run_id'], self.flow.generation_adapter.generate(a))
        sub = self.app.ingest(Incoming(self.student, '另一小题', Intent.SUBQUESTION,
            quote_message_id=first.message_id, verified_question=demo_question()))
        b = self.start(sub)
        self.db.execute('UPDATE runs SET session_id=? WHERE id=?', (a['session_id'], b['run_id']))
        old_runs = [tuple(row) for row in self.db.all('SELECT * FROM runs ORDER BY rowid')]
        self.db.execute('DROP TABLE deepseek_chats')
        self.db.execute('DROP TABLE session_owners')
        self.db.execute('DELETE FROM schema_migrations WHERE version=6')
        self.db.close()
        self.db = Store(self.path)
        self.assertEqual(old_runs, [tuple(row) for row in self.db.all('SELECT * FROM runs ORDER BY rowid')])
        self.assertIsNone(self.db.one('SELECT * FROM session_owners WHERE session_id=?', (a['session_id'],)))

    def test_legacy_single_question_claim_uses_trusted_store_without_rewrite(self):
        a = self.start(self.first())
        legacy = {k: v for k, v in a.items() if k not in ('binding_id', 'session_store_path')}
        encoded = json.dumps(legacy)
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?', (encoded, a['run_id']))
        self.db.execute('DROP TABLE deepseek_chats')
        self.db.execute('DROP TABLE session_owners')
        self.db.execute('DELETE FROM schema_migrations WHERE version=6')
        self.db.close()
        self.db = Store(self.path)
        claim_deepseek_chat(legacy, URL, store_path=self.path)
        self.assertEqual(self.db.one('SELECT input_json FROM runs WHERE id=?', (a['run_id'],))[0], encoded)
        self.assertEqual(self.db.one('SELECT session_id FROM deepseek_chats WHERE session_url=?', (URL,))[0], a['session_id'])


if __name__ == '__main__':
    unittest.main()
