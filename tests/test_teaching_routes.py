from pathlib import Path
import tempfile
import unittest

from helpdesk.teaching_routes import resolve_route, inspect_route, OBJECTIVE, GRAMMAR, WRITING, QA


class TeachingRouteTests(unittest.TestCase):
    def test_types_and_methods_use_own_skill(self):
        for kind in ('阅读理解', '七选五', '完形填空'):
            self.assertEqual(resolve_route(kind, 'method').skill, OBJECTIVE)
        self.assertEqual(resolve_route('语法填空', 'method').skill, GRAMMAR)
        for kind in ('应用文', '读后续写'):
            self.assertEqual(resolve_route(kind, 'correction').skill, WRITING)

    def test_source_lookup_is_distinct_from_method_explanation(self):
        self.assertEqual(resolve_route('阅读理解', 'course_basis').skill, QA)
        self.assertNotEqual(resolve_route('阅读理解', 'method').skill, QA)

    def test_unknown_type_and_wrong_correction_do_not_guess(self):
        for kind, request in [('听力', 'answer'), ('../secret', 'answer'), ('阅读理解', 'correction')]:
            with self.assertRaises(ValueError):
                resolve_route(kind, request)

    def test_missing_checker_never_falls_back_to_other_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / GRAMMAR / 'scripts/check_lesson.py'
            old.parent.mkdir(parents=True)
            old.write_text('raise RuntimeError("must not execute")', encoding='utf-8')
            report = inspect_route(root, '阅读理解')
            self.assertIn(OBJECTIVE + '/scripts/check_lesson.py', report['missing_dependencies'])
            self.assertFalse(report['generation_authorized'])
