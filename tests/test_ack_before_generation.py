"""ACK ordering policy tests; desktop confirmation is explicitly simulated."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from helpdesk.__main__ import demo_question
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.domain import Intent
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


class AckBeforeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "business.db"
        self.db = Store(self.path)
        self.desk = Helpdesk(self.db)
        self.binding = self.desk.bind("room", "student", "学生", verified=True)
        self.outcome = self.question("first")
        self.ack = self.db.one("SELECT * FROM outbox WHERE message_id=? AND purpose='ACK'", (self.outcome.message_id,))
        self.flow = Workflow(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def question(self, identity):
        return self.desk.ingest(Incoming(self.binding, "请问这道题怎么做？", Intent.NEW, platform_id=identity,
            verified_question=demo_question(), raw_material="Passage", verified_material="Passage"))

    def counts(self):
        return {table: self.db.one("SELECT COUNT(*) FROM " + table)[0] for table in
            ("sessions", "session_owners", "runs", "answers", "outbox", "audit", "human_tasks")}

    def assert_blocked_without_side_effect(self):
        before = self.counts()
        generate = Mock(wraps=self.flow.generation_adapter.generate)
        self.flow.generation_adapter.generate = generate
        with self.assertRaisesRegex(ValueError, "^ACK_REQUIRED$"):
            self.flow.start(self.outcome.turn_id)
        with self.assertRaisesRegex(ValueError, "^ACK_REQUIRED$"):
            self.flow.generate(self.outcome.turn_id)
        generate.assert_not_called()
        self.assertEqual(self.counts(), before)

    def test_pending_unknown_and_missing_ack_block_without_sessions_or_model_calls(self):
        self.flow.set_require_ack_before_generation(True)
        for state in ("PENDING", "SEND_UNKNOWN", "FAILED", "STALE"):
            with self.subTest(state=state):
                self.db.execute("UPDATE outbox SET state=? WHERE id=?", (state, self.ack["id"]))
                self.assert_blocked_without_side_effect()
        self.db.execute("DELETE FROM outbox WHERE id=?", (self.ack["id"],))
        self.assert_blocked_without_side_effect()

    def test_confirmed_original_binding_ack_allows_session_and_generation(self):
        self.flow.set_require_ack_before_generation(True)
        self.assertEqual(self.flow.dispatch(self.ack["id"]), "SENT_UI_CONFIRMED")
        generated = self.flow.generate(self.outcome.turn_id)
        self.assertEqual(generated["state"], "GENERATED")
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM sessions")[0], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM runs")[0], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM answers")[0], 1)
        self.assertTrue(self.flow.desktop.simulated)

    def test_other_message_ack_does_not_release_this_turn(self):
        self.flow.set_require_ack_before_generation(True)
        other = self.question("second")
        ack = self.db.one("SELECT id FROM outbox WHERE message_id=? AND purpose='ACK'", (other.message_id,))
        self.assertEqual(self.flow.dispatch(ack["id"]), "SENT_UI_CONFIRMED")
        self.assert_blocked_without_side_effect()

    def test_real_prepared_generator_rejects_old_simulated_ack(self):
        from hashlib import sha256
        from helpdesk.mcp_generation import PreparedDeepSeekGenerator
        course = self.root / 'course.md'
        course.write_text('Verified offline course fixture', encoding='utf-8')
        manifest = self.root / 'manifest.json'
        manifest.write_text('{}', encoding='utf-8')
        bundle = dict(answer_generation_allowed_by_course=True, question_type='阅读理解',
            workflow_teaching_paths=[str(course)], reviewed_policy_id='fixture',
            files=[dict(snapshot_path=str(course), snapshot_sha256=sha256(course.read_bytes()).hexdigest())])
        self.flow = Workflow(self.db, generation_adapter=PreparedDeepSeekGenerator(None,
            self.root / 'preparation.json', self.root / 'attempts'), teaching_manifest=manifest)
        self.flow.set_require_ack_before_generation(True)
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',simulated=1 WHERE id=?", (self.ack['id'],))
        with patch('helpdesk.workflow.verify_bundle', return_value=bundle):
            self.assert_blocked_without_side_effect()
            self.db.execute('UPDATE outbox SET simulated=0 WHERE id=?', (self.ack['id'],))
            # Only plan the run. This fixture never opens a browser or generates.
            self.assertIsNotNone(self.flow.start(self.outcome.turn_id))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM runs')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM answers')[0], 0)

    def test_wrong_binding_and_test_copy_or_progress_are_not_ack(self):
        self.flow.set_require_ack_before_generation(True)
        other = self.desk.bind("room", "student2", "学生2", verified=True)
        self.db.execute("UPDATE outbox SET state='SENT_UI_CONFIRMED',binding_id=? WHERE id=?", (other, self.ack["id"]))
        self.assert_blocked_without_side_effect()
        self.db.execute("UPDATE outbox SET binding_id=? WHERE id=?", (self.binding, self.ack["id"]))
        for purpose in ("TEST_ANSWER", "PROGRESS", "ANSWER", "CLARIFICATION"):
            with self.subTest(purpose=purpose):
                self.db.execute("UPDATE outbox SET purpose=? WHERE id=?", (purpose, self.ack["id"]))
                self.assert_blocked_without_side_effect()

    def test_strict_boolean_audited_changes_and_restart_preserve_policy(self):
        for value in ("true", "false", 1, 0, None, [], {}):
            with self.assertRaises(ValueError):
                self.flow.set_require_ack_before_generation(value)
        self.assertIsNone(self.db.one("SELECT value FROM settings WHERE key='require_ack_before_generation'"))
        self.flow.set_require_ack_before_generation(True, actor="operator")
        self.flow.set_require_ack_before_generation(True, actor="operator")
        rows = self.db.all("SELECT details FROM audit WHERE event='ACK_BEFORE_GENERATION_POLICY_CHANGED'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["actor"], "operator")
        self.db.close()
        self.db = Store(self.path)
        self.desk = Helpdesk(self.db)
        self.flow = Workflow(self.db)
        self.assert_blocked_without_side_effect()
        self.flow.set_require_ack_before_generation(False)
        self.assertIsNotNone(self.flow.start(self.outcome.turn_id))

    def test_default_remains_compatible_without_confirmed_ack(self):
        self.assertFalse(self.flow._ack_before_generation_required())
        self.assertIsNotNone(self.flow.start(self.outcome.turn_id))

    def test_trusted_workbench_stage_enables_persisted_gate_and_rejects_string(self):
        config = self.root / "collector.toml"
        config.write_text('[collector]\nbusiness_database=' + json.dumps(self.path.as_posix()) +
            '\nprocessing_mode="ACK_ONLY"\n[stage]\nname="ACK first"\nrequire_ack_before_generation=true\n[stage.delivery]\nACK="AUTO"\nANSWER="MANUAL"\n', encoding="utf-8")
        server = DemoHTTPServer(("127.0.0.1", 0), self.path, collector_config=config, processing_mode="ACK_ONLY")
        server.server_close()
        self.assertTrue(self.flow._ack_before_generation_required())
        audit = self.db.one("SELECT details FROM audit WHERE event='ACK_BEFORE_GENERATION_POLICY_CHANGED'")
        self.assertEqual(json.loads(audit[0])["actor"], "trusted_workbench_startup")
        self.assert_blocked_without_side_effect()
        config.write_text(config.read_text(encoding="utf-8").replace('require_ack_before_generation=true', 'require_ack_before_generation="true"'), encoding="utf-8")
        with self.assertRaises(ValueError):
            DemoHTTPServer(("127.0.0.1", 0), self.path, collector_config=config, processing_mode="ACK_ONLY")


if __name__ == "__main__":
    unittest.main()
