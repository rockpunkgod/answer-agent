"""Display-scoped execution with anonymous fake pages; no desktop calls."""
from copy import deepcopy
from hashlib import sha256
import json
import re
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from helpdesk.mcp_generation import PreparedDeepSeekGenerator
from helpdesk.mcp_page_contract import DeepSeekPage, PageUnconfirmed
from helpdesk.mcp_preparation import DeepSeekSessionPreparer
from helpdesk.mcp_transport import MCPProcess
from helpdesk.mcp_preparation_review import PreparationReviewError
from tests import test_mcp_generation as generation_fixture
from tests import test_mcp_page_contract as page_fixture
from tests import test_mcp_preparation as upload_fixture
from tests import test_mcp_preparation_text as text_fixture


def scoped(record, *, region=(0, -100, 100, 0), selected='1', dy=-100):
    result = deepcopy(record)
    for item in result['content']:
        if item.get('type') == 'text':
            item['text'] = (f'Selected Displays: {selected}\n'
                            f'Screenshot Region: ({",".join(map(str, region))})\n'
                            + re.sub(r'\((\d+),(\d+)\)(?= (?:编辑|按钮|组|文档))',
                                     lambda m: f'({m[1]},{int(m[2]) + dy})', item['text']))
    return result


class DisplayDesktop(upload_fixture.FakeDesktop):
    def __init__(self, *args, outside_picker=False, unscaled_images=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.actual_calls = []
        self.outside_picker = outside_picker
        self.unscaled_images = unscaled_images

    def call(self, tool, arguments):
        self.actual_calls.append((tool, deepcopy(arguments)))
        if tool in ('Snapshot', 'Screenshot'):
            if arguments.get('display') != [1]:
                raise AssertionError('Observation was not scoped to display index 1')
        # The underlying anonymous picker fixture uses its own local pixels.
        args = deepcopy(arguments)
        if 'loc' in args:
            args['loc'][1] += 100
        result = super().call(tool, args)
        if tool in ('Snapshot', 'Screenshot'):
            result = scoped(result)
            if self.unscaled_images:
                result['content'][0]['text'] = result['content'][0]['text'].replace(
                    'Screenshot Original Size:', 'Screenshot Size:')
                if tool == 'Screenshot':
                    result['content'][0]['text'] = json.dumps([result['content'][0]['text']])
            if self.picker and self.outside_picker and tool == 'Snapshot':
                result['content'][0]['text'] = result['content'][0]['text'].replace(
                    '(2,-97) 编辑', '(2,3) 编辑')
        return result


class DisplayScopeTests(unittest.TestCase):
    def fixture(self, module, name):
        case = getattr(module, name)()
        case.setUp()
        self.addCleanup(case.tearDown)
        self.addCleanup(case.doCleanups)
        return case

    def test_signed_native_coordinates_are_preserved(self):
        page = DeepSeekPage(page_fixture.URL)
        observed = page_fixture.observation(
            '    └── (-120,-250) 编辑 "给 DeepSeek 发送消息"\n')
        self.assertEqual(page.stage_action(observed, 'question')['arguments']['loc'], [-120, -250])
        observed = page_fixture.observation(
            '    ├── (-120,-250) 编辑 "给 DeepSeek 发送消息" [focused]\n'
            '    └── (-110,-240) 按钮 "粘贴原文至输入框"\n')
        self.assertEqual(page.expand_pasted_text_action(observed)['arguments']['loc'], [-110, -240])

    def test_display_scope_uses_fresh_region_and_stops_on_wrong_or_missing_metadata(self):
        page = DeepSeekPage(page_fixture.URL, display_index=1)
        original = page_fixture.observation('    └── (8,9) 编辑 "给 DeepSeek 发送消息"\n')
        self.assertEqual(page.stage_action(scoped(original), 'question')['arguments']['loc'], [8, -91])
        for observed in (original, scoped(original, selected='0'),
                         scoped(original, selected='1,0'),
                         scoped(original, region=(100, -100, 200, 0)),
                         scoped(original, region=(0, 0, 0, 100))):
            with self.subTest(observed=observed), self.assertRaises(PageUnconfirmed):
                page.stage_action(observed, 'question')
        # Repositioned display and current native coordinates are valid together.
        changed = page_fixture.observation('    └── (-20,9) 编辑 "给 DeepSeek 发送消息"\n')
        changed = scoped(changed, region=(-100, 0, 0, 100), dy=0)
        self.assertEqual(page.stage_action(changed, 'question')['arguments']['loc'], [-20, 9])

    def test_submit_rechecks_focused_editor_is_on_selected_display(self):
        page = DeepSeekPage(page_fixture.URL, display_index=1)
        observed = page_fixture.observation(
            '    └── (8,9) 编辑 "给 DeepSeek 发送消息" [focused] [value:"question"]\n')
        self.assertEqual(page.submit_action(scoped(observed), 'question')['tool'], 'Shortcut')
        with self.assertRaises(PageUnconfirmed):
            page.submit_action(scoped(observed, dy=0), 'question')

    def make_upload(self, case, desktop, controls=None):
        controls = deepcopy(controls or case.controls)
        controls.update(display_index=1, preparation_mode='FAST_UPLOAD_THEN_GENERATE')
        return DeepSeekSessionPreparer(desktop, case.snapshot, upload_fixture.URL,
                                      case.candidate_path, controls, poll_interval=0, timeout=2)

    def test_native_upload_scopes_all_observations_and_keeps_negative_action_coordinates(self):
        case = self.fixture(text_fixture, 'TextPreparationTests')
        desktop = DisplayDesktop([case.course])
        candidate = self.make_upload(case, desktop).run()
        self.assertEqual(candidate['status'], 'ATTACHMENTS_READY_REQUIRES_SOURCE_REVIEW')
        self.assertEqual(candidate['display_index'], 1)
        self.assertTrue(all(args['loc'][1] < 0 for tool, args in desktop.actual_calls
                            if tool in ('Type', 'Click')))
        self.assertFalse(any(tool == 'Shortcut' and args.get('shortcut') == 'enter'
                             for tool, args in desktop.actual_calls))

    def test_picker_outside_selected_display_stops_before_path_input(self):
        case = self.fixture(text_fixture, 'TextPreparationTests')
        desktop = DisplayDesktop([case.course], outside_picker=True)
        with self.assertRaises(PageUnconfirmed):
            self.make_upload(case, desktop).run()
        self.assertFalse(any(tool == 'Type' for tool, _ in desktop.actual_calls))

    def test_visual_upload_converts_physical_origin_to_image_pixels_without_changing_click_point(self):
        case = self.fixture(text_fixture, 'TextPreparationTests')
        page, picker = case.base / 'page.png', case.base / 'picker.png'
        Image.new('RGB', (100, 100), (240, 240, 240)).save(page)
        Image.new('RGB', (100, 100), (80, 80, 80)).save(picker)

        def digest(path, box):
            with Image.open(path) as image:
                return sha256(image.crop(box).convert('RGB').tobytes()).hexdigest()

        controls = {
            'visual_upload': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                              'editor_offset': [10, 10], 'crop_size': [10, 10],
                              'crop_sha256': digest(page, (13, 14, 23, 24))},
            'visual_picker': {'screen_size': [100, 100], 'screenshot_size': [100, 100],
                              'file_input_point': [30, -30], 'open_point': [70, -25],
                              'regions': {name: {'box': list(box), 'sha256': digest(picker, box)}
                                          for name, box in {'header': (1, 1, 20, 10),
                                                            'file_label': (5, 40, 25, 50),
                                                            'open_button': (60, 70, 80, 80)}.items()}}}
        desktop = DisplayDesktop([case.course], visual_images=(page, picker), unscaled_images=True)
        candidate = self.make_upload(case, desktop, controls).run()
        self.assertEqual(candidate['last_visual_upload_check']['click_point'], [18, -81])
        self.assertEqual(candidate['last_visual_upload_check']['crop_box'], (13, 14, 23, 24))
        self.assertTrue(all(args.get('display') == [1] for tool, args in desktop.actual_calls
                            if tool in ('Snapshot', 'Screenshot')))

    def test_source_review_preserves_scope_and_rejects_candidate_or_preparation_downgrade(self):
        case = self.fixture(text_fixture, 'TextPreparationTests')
        candidate = self.make_upload(case, DisplayDesktop([case.course])).run()
        approved = case.approve(case.review())
        self.assertEqual(approved['display_index'], 1)
        PreparedDeepSeekGenerator(None, case.output, case.base)._preparation(case.snapshot)
        for display in (None, 0, True):
            altered = dict(approved, display_index=display)
            case.output.write_text(json.dumps(altered), encoding='utf-8')
            desktop = Mock()
            with self.subTest(display=display), self.assertRaises(ValueError):
                PreparedDeepSeekGenerator(desktop, case.output, case.base / 'attempts').generate(case.snapshot)
            desktop.call.assert_not_called()
        self.assertEqual(candidate['controls']['display_index'], 1)

    def test_real_transport_without_explicit_display_scope_stops_before_any_call(self):
        desktop = object.__new__(MCPProcess)  # No subprocess is constructed.
        with patch.object(MCPProcess, 'call') as called:
            case = self.fixture(text_fixture, 'TextPreparationTests')
            with self.assertRaisesRegex(ValueError, 'Explicit display_index'):
                DeepSeekSessionPreparer(desktop, case.snapshot, upload_fixture.URL,
                                        case.candidate_path, case.controls)
            case = self.fixture(generation_fixture, 'PreparedGenerationTests')
            with self.assertRaisesRegex(ValueError, 'Explicit display_index'):
                PreparedDeepSeekGenerator(desktop, case.path, case.base / 'attempts').generate(case.context)
            called.assert_not_called()

    def test_scoped_candidate_with_missing_or_inconsistent_scope_cannot_be_approved(self):
        for change in ('scope_removed', 'wrong_display'):
            with self.subTest(change=change):
                case = self.fixture(text_fixture, 'TextPreparationTests')
                candidate = self.make_upload(case, DisplayDesktop([case.course])).run()
                if change == 'scope_removed':
                    candidate.pop('display_index')
                else:
                    observed = candidate['readiness_snapshot']
                    observed['content'][0]['text'] = observed['content'][0]['text'].replace(
                        'Selected Displays: 1', 'Selected Displays: 0')
                case.candidate_path.write_text(json.dumps(candidate), encoding='utf-8')
                with self.assertRaises((PreparationReviewError, PageUnconfirmed)):
                    case.approve(case.review())
                self.assertFalse(case.output.exists())
                self.assertFalse(case.readback.exists())

    def test_generation_scopes_fresh_observations_and_stops_if_page_moves_before_submit(self):
        for move in (False, True):
            with self.subTest(move=move):
                case = self.fixture(generation_fixture, 'PreparedGenerationTests')
                proof = case.base / 'proof.json'
                proof.write_text(json.dumps(scoped(json.loads(proof.read_text(encoding='utf-8')),
                                                   region=(0, -1440, 2560, 0), dy=-1440)), encoding='utf-8')
                case.prep.update(display_index=1, readback_sha256=sha256(proof.read_bytes()).hexdigest())
                case.path.write_text(json.dumps(case.prep), encoding='utf-8')

                class Desktop(generation_fixture.Transport):
                    def call(self, tool, arguments):
                        if tool == 'Snapshot':
                            self_test.assertEqual(arguments.get('display'), [1])
                        if tool == 'Type':
                            self_test.assertEqual(arguments['loc'], [100, -1240])
                        result = super().call(tool, arguments)
                        if tool == 'Snapshot':
                            return scoped(result, region=(0, -1440, 2560, 0),
                                          dy=0 if move and self.prompt else -1440)
                        return result

                self_test = self
                desktop = Desktop()
                generator = PreparedDeepSeekGenerator(desktop, case.path, case.base / 'attempts',
                                                       poll_interval=0, timeout=2)
                if move:
                    with self.assertRaises(PageUnconfirmed):
                        generator.generate(case.context)
                    self.assertNotIn('Shortcut', desktop.calls)
                else:
                    self.assertEqual(generator.generate(case.context)['correct_option_id'], 'option-a')
                    self.assertEqual(desktop.calls.count('Shortcut'), 1)


if __name__ == '__main__':
    unittest.main()
