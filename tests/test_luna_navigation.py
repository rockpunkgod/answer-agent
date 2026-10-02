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

    def suggest(self, runner, **kwargs):
        return navigation.suggest(self.record, 'Locate the WeCom title bar only.',
            ['ACTIVATE_WECOM', 'STOP'], runner=runner, **kwargs)

    def display_record(self, index, bounds, image_size, *, device='DISPLAY9'):
        region = ','.join(map(str, bounds))
        self.source['content'][0]['text'] = (f'Selected Displays: {index}\n'
            f'Screenshot Region: ({region})\n{index}:{device} ({region})')
        self.record.write_text(json.dumps(self.source), encoding='utf-8')
        Image.new('RGB', image_size, 'white').save(self.image)

    def test_configured_display_uses_current_bounds_not_device_number_or_old_layout(self):
        for index, bounds, size, point, expected in (
                (0, (0, 0, 2560, 1600), (1280, 800), (640, 400), [1280, 800]),
                (1, (-1920, 0, 0, 1080), (960, 540), (10, 20), [-1900, 40]),
                (2, (2560, -1080, 4480, 0), (1920, 1080), (1919, 1079), [4479, -1])):
            with self.subTest(index=index):
                self.display_record(index, bounds, size)
                runner = self.runner(json.dumps({'action': 'ACTIVATE_WECOM',
                    'image_x': point[0], 'image_y': point[1]}))
                result = self.suggest(runner, display_index=index)
                self.assertEqual(result['loc'], expected)
                self.assertEqual(result['display_index'], index)
                self.assertEqual(result['display_region'], list(bounds))
                runner.assert_called_once()

    def test_same_display_moved_between_observations_uses_new_origin(self):
        runner = self.runner('{"action":"ACTIVATE_WECOM","image_x":900,"image_y":30}')
        first = self.suggest(runner)
        self.display_record(1, (2560, 0, 5120, 1440), (1920, 1080))
        second = self.suggest(runner)
        self.assertEqual(first['loc'], [1200, -1400])
        self.assertEqual(second['loc'], [3760, 40])

    def test_native_device_prefix_and_crlf_metadata(self):
        self.display_record(0, (0, 0, 2560, 1600), (1280, 800), device=r'\\.\DISPLAY1')
        self.source['content'][0]['text'] = self.source['content'][0]['text'].replace('\n', '\r\n')
        self.record.write_text(json.dumps(self.source), encoding='utf-8')
        result = self.suggest(self.runner(
            '{"action":"ACTIVATE_WECOM","image_x":1,"image_y":1}'), display_index=0)
        self.assertEqual(result['loc'], [2, 2])

    def test_upscaled_bitmap_last_pixel_stays_inside_display(self):
        self.display_record(0, (-640, -360, 0, 0), (1920, 1080))
        result = self.suggest(self.runner(
            '{"action":"ACTIVATE_WECOM","image_x":1919,"image_y":1079}'), display_index=0)
        self.assertEqual(result['loc'], [-1, -1])

    def test_display_scope_conflicts_stop_before_model(self):
        text = self.source['content'][0]['text']
        for changed in (
                text.replace('Selected Displays: 1', 'Selected Displays: 0'),
                text.replace('Selected Displays: 1', 'Selected Displays: 0, 1'),
                text + '\nScreenshot Region: (0,-1440,2560,0)',
                text.replace('1:DISPLAY2 (0,-1440,2560,0)', '1:DISPLAY2 (0,0,2560,1440)'),
                text.replace('1:DISPLAY2', '0:DISPLAY2'),
                text + '\n1:DISPLAY2 (0,-1440,2560,0)',
                text.replace('(0,-1440,2560,0)', '(0,0,0,1440)')):
            with self.subTest(text=changed):
                self.source['content'][0]['text'] = changed
                self.record.write_text(json.dumps(self.source), encoding='utf-8')
                runner = self.runner('{}')
                with self.assertRaises(ValueError):
                    self.suggest(runner)
                runner.assert_not_called()

    def test_invalid_configured_display_stops_before_model(self):
        for index in (None, True, -1, '0', [0, 1]):
            with self.subTest(index=index):
                runner = self.runner('{}')
                with self.assertRaises(ValueError):
                    self.suggest(runner, display_index=index)
                runner.assert_not_called()

    def test_distorted_capture_stops_before_model(self):
        self.display_record(0, (0, 0, 2560, 1600), (1920, 1080))
        runner = self.runner('{}')
        with self.assertRaisesRegex(ValueError, 'GEOMETRY'):
            self.suggest(runner, display_index=0)
        runner.assert_not_called()

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
