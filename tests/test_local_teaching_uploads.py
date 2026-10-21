"""Original local-folder selection and existing CLI wiring; no browser calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from helpdesk import local_teaching_uploads as uploads
from tools import run_ego_deepseek as cli


class LocalTeachingUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(uploads,'SKILLS_ROOT',self.root).start()
        for folder in set(uploads.QUESTION_FOLDERS.values()):
            root=self.root/folder;(root/'references').mkdir(parents=True)
            (root/'SKILL.md').write_bytes(('Original '+folder+'\r\n').encode())
            (root/'references/rule.md').write_text('Original rule.',encoding='utf-8')

    def test_one_folder_only_and_original_bytes_unchanged(self):
        for kind,folder in uploads.QUESTION_FOLDERS.items():
            files=uploads.local_teaching_files(kind)
            self.assertEqual(files[0].name,'SKILL.md')
            self.assertTrue(all(path.is_relative_to(self.root/folder) for path in files))
            self.assertEqual(files[0].read_bytes(),('Original '+folder+'\r\n').encode())
            self.assertEqual(len(files),2)
        with self.assertRaises(ValueError):uploads.local_teaching_files('unknown')

    def test_cli_passes_original_folder_files_with_question_and_image(self):
        image=self.root/'original.jpg';image.write_bytes(b'anonymous fixture')
        adapter=Mock();adapter.submit.return_value={'adapter':'EGO_LITE'}
        argv=['run_ego_deepseek','--space','3','--request-id','anonymous-request',
              '--session-url','https://chat.deepseek.com/a/chat/s/example',
              '--question','第7题','--attachment',str(image),'--question-type','语法填空',
              '--evidence-dir',str(self.root/'evidence')]
        with patch('sys.argv',argv),patch.object(cli,'EgoDeepSeek',return_value=adapter) as factory,patch('builtins.print'):
            cli.main()
        expected=[image,*uploads.local_teaching_files('语法填空')]
        self.assertEqual(factory.call_args.kwargs['allowed_files'],expected)
        self.assertEqual(adapter.submit.call_args.kwargs['attachments'],expected)
        self.assertEqual(adapter.submit.call_args.args[2],'第7题')

    def test_changed_original_is_seen_without_cached_repackaging(self):
        path=uploads.local_teaching_files('阅读理解')[0]
        path.write_text('Updated original.',encoding='utf-8')
        self.assertEqual(uploads.local_teaching_files('阅读理解')[0].read_text(),'Updated original.')
        path.unlink()
        with self.assertRaisesRegex(ValueError,'SKILL_MISSING'):
            uploads.local_teaching_files('阅读理解')
