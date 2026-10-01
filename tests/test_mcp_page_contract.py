import unittest

from helpdesk.mcp_page_contract import DeepSeekPage, PageUnconfirmed


URL = 'https://chat.deepseek.com/a/chat/s/test-123'


def observation(tail='', window='Test - Microsoft Edge', url=URL):
    return {'tool': 'Snapshot', 'is_error': False, 'content': [{'type': 'text', 'text':
        f'UI Tree:\ndesktop\n└── window "{window}"\n'
        f'    ├── (10,10) 文档 "DeepSeek" [value:"{url}"]\n' + tail}]}


class PageContractTests(unittest.TestCase):
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
