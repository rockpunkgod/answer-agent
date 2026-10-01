"""Existing CLI and workbench continue after the initial source review only."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from helpdesk.demo_server import operator_generation_context
from tests import test_automatic_preparation as fixture
from tests.test_mcp_preparation import FakeDesktop, URL
from tools import prepare_deepseek_session as command


class AutomaticPreparationEntrypoints(unittest.TestCase):
    setUp = fixture.AutomaticPreparationTests.setUp
    tearDown = fixture.AutomaticPreparationTests.tearDown
    frozen = fixture.AutomaticPreparationTests.frozen
    make_candidate = fixture.AutomaticPreparationTests.make_candidate

    def candidate_path(self):
        path = Path(self.task['preparation_path']).with_name('preparation-candidate.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_workbench_continues_from_fast_candidate_without_second_review(self):
        self.candidate = self.candidate_path()
        self.make_candidate()
        before = [dict(r) for r in self.db.all('SELECT * FROM audit')]
        config = {'database': self.db.path}
        with patch('helpdesk.mcp_transport.MCPProcess.__enter__', side_effect=AssertionError('no desktop')):
            task, run = operator_generation_context(self.db, self.task['id'], config)
            same_task, same_run = operator_generation_context(self.db, self.task['id'], config)
        self.assertEqual(run['state'], 'RUNNING')
        self.assertEqual(same_task, task); self.assertEqual(same_run, run)
        preparation = json.loads(Path(task['preparation_path']).read_bytes())
        self.assertEqual(preparation['status'], 'ATTACHMENTS_READY_SOURCE_REVIEWED')
        review = json.loads(Path(preparation['operator_review_evidence']).read_bytes())
        self.assertEqual(review['reviewed_at'], self.task['reviewed_at'])
        self.assertFalse(review['new_human_review'])
        self.assertEqual([dict(r) for r in self.db.all('SELECT * FROM audit')], before)
        for table in ('reviews', 'answers', 'performance_units'):
            self.assertEqual(self.db.one('SELECT COUNT(*) FROM '+table)[0], 0)

    def test_changed_candidate_never_publishes_workbench_authorization(self):
        content = json.loads(self.candidate.read_bytes()); content['input_fingerprint'] = 'changed'
        self.candidate_path().write_text(json.dumps(content), encoding='utf-8')
        with self.assertRaises(ValueError):
            operator_generation_context(self.db, self.task['id'], {'database': self.db.path})
        self.assertFalse(Path(self.task['preparation_path']).exists())

    def cli_args(self, *, task_id=None):
        controls = self.base / 'controls.json'
        controls.write_text(json.dumps({'preparation_mode': 'FAST_UPLOAD_THEN_GENERATE',
            'material_order': 'COURSE_THEN_QUESTION', 'upload_button': 'Upload files',
            'picker_window': 'Open', 'file_input': 'File name', 'open_button': 'Open'}), encoding='utf-8')
        return ['prepare_deepseek_session', '--database', str(self.db.path), '--run', self.task['run_id'],
            '--operator-task', task_id or self.task['id'], '--session-url', URL,
            '--controls', str(controls), '--evidence', str(self.candidate_path())]

    def test_preparation_cli_automatically_reuses_existing_review_without_model_readback(self):
        fake = FakeDesktop([{'path': str(self.course), 'name': self.course.name,
                            'content': self.course.read_text(encoding='utf-8')}])
        output = io.StringIO()
        with patch.object(sys, 'argv', self.cli_args()), patch.object(command, 'MCPProcess', return_value=nullcontext(fake)), redirect_stdout(output):
            command.main()
        result = json.loads(output.getvalue())
        self.assertTrue(result['initial_input_review_reused'])
        self.assertFalse(result['new_human_review'])
        self.assertTrue(result['operator_verified'])
        self.assertEqual(result['status'], 'ATTACHMENTS_READY_SOURCE_REVIEWED')
        self.assertEqual(result['preparation'], self.task['preparation_path'])
        self.assertFalse(any(t == 'Shortcut' and a.get('shortcut') == 'enter' for t, a in fake.calls))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM reviews')[0], 0)

    def test_unknown_task_is_rejected_before_starting_desktop(self):
        with patch.object(sys, 'argv', self.cli_args(task_id='missing')), patch.object(command, 'MCPProcess') as transport:
            with self.assertRaises(ValueError): command.main()
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
