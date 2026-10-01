from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from helpdesk.course_library import CourseLibraryError, search_course


class CourseLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'references/originals').mkdir(parents=True)
        (self.root / 'references/corpus').mkdir()
        (self.root / 'references/originals/source.pdf').write_bytes(b'fixture original')
        (self.root / 'references/corpus/source.txt').write_text(
            'page marker\ncontext before\nreading method\ncontext after', encoding='utf-8')
        self.record = {'id': 'source', 'name': 'source.pdf', 'original': 'X:/must-not-follow.pdf',
                       'text': 'references/corpus/source.txt',
                       'sha256': sha256(b'fixture original').hexdigest(), 'warnings': ['check image']}
        self.save()

    def save(self):
        (self.root / 'references/catalog.json').write_text(json.dumps([self.record]), encoding='utf-8')

    def test_preserves_context_provenance_and_warning(self):
        result = search_course(self.root, ['READING'], limit=1)
        hit = result['hits'][0]
        self.assertEqual(hit['match_line'], 3)
        self.assertIn('context before', hit['excerpt'])
        self.assertEqual(hit['warnings'], ['check image'])
        self.assertTrue(Path(hit['original_path']).is_relative_to(self.root))
        self.assertFalse(result['answer_generation_authorized'])

    def test_altered_original_rejected(self):
        (self.root / 'references/originals/source.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(CourseLibraryError, 'hash mismatch'):
            search_course(self.root, ['reading'])

    def test_catalog_cannot_escape_package(self):
        self.record['text'] = '../private.txt'
        self.save()
        with self.assertRaisesRegex(CourseLibraryError, 'Unsafe'):
            search_course(self.root, ['reading'])

    def test_visual_evidence_is_distinguished(self):
        (self.root / 'references/visual-recovery.md').write_text('reading method image', encoding='utf-8')
        result = search_course(self.root, ['reading'])
        visual = next(x for x in result['hits'] if x['source_id'] == 'visual-recovery')
        self.assertTrue(visual['requires_context_review'])
        self.assertNotIn('original_sha256', visual)

    def test_empty_query_invalid_limit_and_no_hit(self):
        with self.assertRaises(CourseLibraryError):
            search_course(self.root, [' '])
        with self.assertRaises(CourseLibraryError):
            search_course(self.root, ['reading'], limit=0)
        self.assertEqual(search_course(self.root, ['absent'])['hits'], [])
