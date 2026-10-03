import unittest

from helpdesk.mcp_page_contract import DeepSeekPage, DeepSeekNewChatPage, PageUnconfirmed


URL = 'https://chat.deepseek.com/a/chat/s/test-123'


def observation(tail='', window='Test - Microsoft Edge', url=URL):
    return {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text':
        f'UI Tree:\ndesktop\n└── window "{window}"\n'
        f'    ├── (10,10) 文档 "DeepSeek" [value:"{url}"]\n' + tail}]}


class PageContractTests(unittest.TestCase):
    def test_blank_root_is_not_a_conversation_and_rejects_existing_response(self):
        root = 'https://chat.deepseek.com/'
        with self.assertRaises(ValueError):
            DeepSeekPage(root, 0)
        page = DeepSeekNewChatPage(root, 0)
        meta = 'Selected Displays: 0\nScreenshot Region: (0,0,500,500)\n'
        observed = observation('    └── (120,250) 编辑 "给 DeepSeek 发送消息"\n', url=root)
        observed['content'][0]['text'] = meta + observed['content'][0]['text']
        self.assertEqual(page.stage_action(observed, 'first real request')['tool'], 'Type')
        observed['content'][0]['text'] += '    └── 按钮 "朗读"\n'
        with self.assertRaisesRegex(PageUnconfirmed, 'NEW_CHAT_NOT_BLANK'):
            page.stage_action(observed, 'first real request')

    def test_truncated_tree_cannot_prove_unique_editor_or_complete_response(self):
        token = 'run_1234567890'
        tail = ('    ├── 按钮 "朗读"\n'
                f'    ├── text "BEGIN_{token}"\n    ├── text "apparently complete"\n'
                f'    └── text "END_{token}"\n')
        truncated = '\n... [truncated: reached the 500-element capture limit — some elements were not visited.]'
        with self.assertRaisesRegex(PageUnconfirmed, 'PAGE_TREE_TRUNCATED'):
            DeepSeekPage(URL).completed_text(observation(tail + truncated), token)
        for tail, action in (
            ('    └── (120,250) 编辑 "给 DeepSeek 发送消息"\n', 'stage'),
            ('    └── (120,250) 编辑 "给 DeepSeek 发送消息" [focused] [value:"question"]\n', 'submit')):
            with self.subTest(action=action), self.assertRaisesRegex(PageUnconfirmed, 'PAGE_TREE_TRUNCATED'):
                getattr(DeepSeekPage(URL), action + '_action')(observation(tail + truncated), 'question')

    def test_login_or_verification_in_owned_page_blocks_input(self):
        tail = '    ├── (120,250) 编辑 "给 DeepSeek 发送消息"\n    └── (1,2) 编辑 "验证码"\n'
        with self.assertRaisesRegex(PageUnconfirmed, 'LOGIN_OR_VERIFICATION_REQUIRED'):
            DeepSeekPage(URL).stage_action(observation(tail), 'question')

    def test_input_requires_fresh_exact_conversation_and_empty_editor(self):
        page = DeepSeekPage(URL)
        tail = '    └── (120,250) 编辑 "给 DeepSeek 发送消息" [action: click]\n'
        self.assertEqual(page.stage_action(observation(tail), 'question')['arguments']['loc'], [120, 250])
        for snap in (observation(tail, window='企业微信'), observation(tail, url=URL+'x'),
                     observation(tail.rstrip()+' [value:"existing draft"]')):
            with self.assertRaises(PageUnconfirmed):
                page.stage_action(snap, 'question')
        with self.assertRaises(ValueError):
            page.stage_action(observation(tail), 'line1\nline2')

    def test_submit_requires_focus_and_exact_readback(self):
        page = DeepSeekPage(URL)
        tail = '    └── (120,250) 编辑 "给 DeepSeek 发送消息" [focused] [value:"question"]'
        self.assertEqual(page.submit_action(observation(tail), 'question')['tool'], 'Shortcut')
        for changed in (tail.replace('[focused]', ''), tail.replace('question', 'different')):
            with self.assertRaises(PageUnconfirmed):
                page.submit_action(observation(changed), 'question')

    def test_binding_accepts_exact_sent_message_while_generation_is_running(self):
        prompt = 'SYNTHETIC BEGIN_match_run_1234567890 "quoted" question'
        page = DeepSeekPage(URL)
        for node in (f'    ├── 组 "{prompt}"\n', f'    ├── (1,2) text "{prompt}" [action: click]\n',
                     f'        └── (1885,855) 组 "{prompt}"  [action: click]\n'):
            with self.subTest(node=node):
                page.require_submitted_prompt(observation(node +
                    '    ├── text "正在思考"\n    └── (120,250) 编辑 "给 DeepSeek 发送消息"\n'), prompt)

    def test_binding_rejects_prompt_in_editor_and_missing_or_ambiguous_composer(self):
        prompt = 'SYNTHETIC BEGIN_match_run_1234567890 question'
        editor = '    └── (120,250) 编辑 "给 DeepSeek 发送消息"\n'
        sent = f'    ├── 组 "{prompt}"\n'
        staged = editor.rstrip() + f' [value:"{prompt}"]\n'
        for tail in (staged, sent + staged, sent, sent + editor + editor,
                     editor + f'    └── 按钮 "{prompt}"\n',
                     editor + sent.replace(prompt, 'different message')):
            with self.subTest(tail=tail), self.assertRaises(PageUnconfirmed):
                DeepSeekPage(URL).require_submitted_prompt(observation(tail), prompt)
        truncated = editor + sent + '\n... [truncated: reached the 500-element capture limit]'
        with self.assertRaisesRegex(PageUnconfirmed, 'PAGE_TREE_TRUNCATED'):
            DeepSeekPage(URL).require_submitted_prompt(observation(truncated), prompt)

    def test_final_response_excludes_other_nodes_and_requires_unique_markers(self):
        token = 'run_1234567890'
        tail = ('    ├── 按钮 "朗读"\n    ├── text "earlier content"\n'
                f'    ├── text "BEGIN_{token}"\n    ├── text "同学，选A。"\n'
                f'    ├── text "END_{token}"\n    └── text "footer"\n')
        page = DeepSeekPage(URL)
        self.assertEqual(page.completed_text(observation(tail), token), '同学，选A。')
        for changed in (tail+'    └── text "正在思考"', tail.replace('朗读', '停止'),
                        tail.replace(f'END_{token}', 'not done'),
                        tail+f'    └── text "BEGIN_{token}"\n',
                        tail+f'    └── text "END_{token}"\n'):
            with self.assertRaises(PageUnconfirmed):
                page.completed_text(observation(changed), token)

    def test_reasoning_closing_marker_before_final_opening_is_not_a_response(self):
        token = 'answer_run_1234567890'
        tail = ('    ├── 按钮 "朗读"\n'
                f'    ├── text "END_{token}"\n'
                '    ├── text "reasoning metadata, not the answer"\n'
                f'    ├── text "BEGIN_{token}"\n'
                '    ├── text "final answer only"\n'
                f'    └── text "END_{token}"\n')
        self.assertEqual(DeepSeekPage(URL).completed_text(observation(tail), token),
                         'final answer only')


if __name__ == '__main__':
    unittest.main()
