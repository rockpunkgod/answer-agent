"""Anonymous journal/schema tests. No desktop, real model or credentials."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from helpdesk import luna_navigation as navigation


class LunaNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / 'data/private/windows-mcp'
        self.archive.mkdir(parents=True)
        (self.root / 'data/private/windows-native-demo').mkdir()
        self.image = self.archive / 'fixture-1.png'
        Image.new('RGB', (1920, 1080), 'white').save(self.image)
        self.record = self.archive / 'fixture.json'
        self.attempt = self.archive / ('attempt-' + 'a' * 32 + '.json')
        self.source = {'tool': 'Screenshot', 'is_error': False, 'attempt_id': 'a' * 32, 'content': [
            {'type': 'text', 'text': 'Selected Displays: 1\nScreenshot Region: (0,-1440,2560,0)\n1:DISPLAY2 (0,-1440,2560,0)'},
            {'type': 'image', 'path': str(self.image)}]}
        self.journal = {'tool': 'Screenshot', 'status': 'TOOL_RETURNED', 'attempt_id': 'a' * 32,
            'result_path': str(self.record), 'started_at': datetime.now(timezone.utc).isoformat()}
        self.record.write_text(json.dumps(self.source), encoding='utf-8')
        self.attempt.write_text(json.dumps(self.journal), encoding='utf-8')
        self.root_patch = patch.object(navigation, 'ROOT', self.root)
        self.executable_patch = patch.object(navigation.shutil, 'which', return_value='fixture-codex.exe')
        self.root_patch.start(); self.executable_patch.start()

    def tearDown(self):
        self.executable_patch.stop(); self.root_patch.stop(); self.temporary.cleanup()

    def runner(self, value, *, item_type='agent_message'):
        return Mock(return_value=SimpleNamespace(returncode=0, stderr='private diagnostics',
            stdout='\n'.join(json.dumps(event) for event in (
                {'type': 'item.completed', 'item': {'type': item_type, 'text': value}},
                {'type': 'turn.completed'}))))

    def suggest(self, runner):
        return navigation.suggest(self.record, 'Locate the WeCom title bar only.',
            ['ACTIVATE_WECOM', 'STOP'], runner=runner)

    def test_one_schema_only_luna_request_maps_screen2_without_executing_a_tool(self):
        runner = self.runner(json.dumps({'action': 'ACTIVATE_WECOM', 'image_x': 900, 'image_y': 30}))
        result = self.suggest(runner)
        self.assertEqual(result['loc'], [1200, -1400])
        self.assertEqual(result['source_record'], str(self.record.resolve()))
        self.assertEqual(runner.call_count, 1)
        args, kwargs = runner.call_args
        command = args[0]
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-luna')
        self.assertIn('model_reasoning_effort="medium"', command)
        for feature in navigation._DISABLED:
            self.assertIn(feature, command)
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ephemeral', command)
        self.assertFalse(kwargs['shell'])
        self.assertEqual(command[command.index('--image') + 1], str(self.image))
        self.assertIn('Never answer or judge an English question', kwargs['input'])

    def test_stop_has_no_action_coordinates(self):
        self.assertIsNone(self.suggest(self.runner('{"action":"STOP","image_x":0,"image_y":0}'))['loc'])

    def test_extra_authority_duplicate_keys_and_bad_coordinates_are_rejected(self):
        invalid = (
            '{"action":"ACTIVATE_WECOM","image_x":true,"image_y":1}',
            '{"action":"ACTIVATE_WECOM","image_x":-1,"image_y":1}',
            '{"action":"ACTIVATE_WECOM","image_x":1920,"image_y":1}',
            '{"action":"SEND","image_x":1,"image_y":1}',
            '{"action":"ACTIVATE_WECOM","image_x":1,"image_y":1,"recipient":"someone"}',
            '{"action":"ACTIVATE_WECOM","image_x":1,"image_y":1,"confidence":1}',
            '{"action":"ACTIVATE_WECOM","action":"STOP","image_x":1,"image_y":1}',
        )
        for output in invalid:
            runner = self.runner(output)
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                self.suggest(runner)
            self.assertEqual(runner.call_count, 1)

    def test_tool_output_timeout_and_secrets_never_replay_or_leak(self):
        for runner in (self.runner('{}', item_type='command_execution'),
                       Mock(side_effect=subprocess.TimeoutExpired('private-key', 50)),
                       Mock(return_value=SimpleNamespace(returncode=1, stdout='private-key', stderr='private-key'))):
            with self.subTest(runner=runner), self.assertRaises(RuntimeError) as error:
                self.suggest(runner)
            self.assertNotIn('private-key', str(error.exception))
            self.assertEqual(runner.call_count, 1)

    def test_unapproved_or_old_or_multiscreen_input_never_reaches_the_model(self):
        runner = self.runner('{}')
        for change in ('old', 'multiple', 'outside'):
            source, journal = dict(self.source), dict(self.journal)
            if change == 'old':
                journal['started_at'] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            elif change == 'multiple':
                source['content'] = self.source['content'] + [self.source['content'][1]]
            else:
                other = self.root / 'outside.png'
                other.write_bytes(self.image.read_bytes())
                source['content'] = [self.source['content'][0], {'type': 'image', 'path': str(other)}]
            self.record.write_text(json.dumps(source), encoding='utf-8')
            self.attempt.write_text(json.dumps(journal), encoding='utf-8')
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.suggest(runner)
        runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
