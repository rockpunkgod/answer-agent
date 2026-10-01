import unittest

from tools.extract_history_candidates import identify


class HistoryExtractionTests(unittest.TestCase):
    def test_date_separator_followed_by_join_notice_is_not_question(self):
        result = identify({'sender_ocr': '', 'body_ocr': '英语班主任梦梦老师邀请你加入了外部群聊'})
        self.assertEqual(result[1], 'system_notice_excluded')

    def test_lost_sender_requires_review_not_student_classification(self):
        result = identify({'sender_ocr': '', 'body_ocr': '老师为什么选B？'})
        self.assertEqual(result[1], 'unknown_sender_context_review')

    def test_identified_student_question_remains_a_candidate(self):
        result = identify({'sender_ocr': '小明@微信', 'body_ocr': '老师为什么选B？'})
        self.assertEqual(result[1], 'student_question_candidate')
