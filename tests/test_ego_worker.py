"""Run the anonymous JS worker regressions with an already installed Node."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import unittest


class EgoWorkerTests(unittest.TestCase):
    def test_worker_side_effect_guards(self):
        node=shutil.which('node')
        if not node:
            package=importlib.util.find_spec('playwright')
            candidate=Path(package.origin).parent/'driver/node.exe' if package else None
            if candidate and candidate.is_file():
                node=str(candidate)
        if not node:
            self.skipTest('Existing Node runtime unavailable; no runtime installed for this test')
        script=Path(__file__).with_name('ego_deepseek_worker.test.mjs')
        result=subprocess.run([node,'--test',str(script)],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('tests 6',result.stdout)
