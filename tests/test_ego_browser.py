"""Anonymous runner fixtures; explicitly not real ego/DeepSeek verification."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.browser_adapter import BrowserPaused
from helpdesk.ego_browser import EgoDeepSeek


class EgoBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.file=self.root/'approved.md'
        self.file.write_text('anonymous fixture',encoding='utf-8')
        self.adapter=EgoDeepSeek(1,self.root/'receipts',allowed_files=[self.file],executable=self.root/'ego.exe')
        self.args=('run-teach','https://chat.deepseek.com/a/chat/s/fixture','fixture prompt')
        self.kwargs={'attachments':[self.file],'response_token':'answer_fixture'}

    def complete(self,*args,**kwargs):
        state_path=self.root/'receipts/run-teach.state.json'
        state=json.loads(state_path.read_text())
        result={'adapter':'EGO_LITE','text':'anonymous complete reply','delivered':False}
        raw=json.dumps(result).encode()
        (state_path.parent/'run-teach.answer.json').write_bytes(raw)
        state.update(phase='CAPTURED',output_sha256=sha256(raw).hexdigest())
        state_path.write_text(json.dumps(state))
        return SimpleNamespace(returncode=0,stdout=b'fixture only',stderr=b'')

    def test_capture_is_durable_and_second_call_never_starts_browser(self):
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete) as run:
            first=self.adapter.submit(*self.args,**self.kwargs)
            second=self.adapter.submit(*self.args,**self.kwargs)
        self.assertEqual(first,second)
        self.assertFalse(first['delivered'])
        self.assertEqual(run.call_count,1)
        command=run.call_args.args[0]
        self.assertEqual(command[1:3],['nodejs','-e'])
        self.assertNotIn('fixture prompt',command[-1])

    def test_timeout_never_resubmits_and_only_prior_submitted_can_be_captured(self):
        def timeout(*args,**kwargs):
            path=self.root/'receipts/run-teach.state.json'
            state=json.loads(path.read_text());state['phase']='SUBMISSION_INTENT'
            path.write_text(json.dumps(state))
            raise subprocess.TimeoutExpired('fixture',1)
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=timeout) as run:
            with self.assertRaisesRegex(BrowserPaused,'OUTCOME_UNKNOWN'):
                self.adapter.submit(*self.args,**self.kwargs)
            with self.assertRaisesRegex(BrowserPaused,'PRIOR_ATTEMPT'):
                self.adapter.submit(*self.args,**self.kwargs)
            self.assertEqual(run.call_count,1)
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete):
            self.assertEqual(self.adapter.submit(*self.args,**self.kwargs,capture_only=True)['adapter'],'EGO_LITE')

    def test_unapproved_file_and_changed_request_do_not_start_browser(self):
        other=self.root/'not-approved.txt';other.write_text('fixture')
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete) as run:
            with self.assertRaisesRegex(BrowserPaused,'ATTACHMENT_NOT_ALLOWED'):
                self.adapter.submit(*self.args,attachments=[other],response_token='answer_fixture')
            run.assert_not_called()
            self.adapter.submit(*self.args,**self.kwargs)
            changed=(*self.args[:2],'different prompt')
            with self.assertRaisesRegex(BrowserPaused,'REQUEST_CHANGED'):
                self.adapter.submit(*changed,**self.kwargs)
            self.assertEqual(run.call_count,1)

    def test_foreign_origin_unknown_space_and_tampered_capture_are_rejected(self):
        with self.assertRaises(ValueError):
            EgoDeepSeek(0,self.root)
        with patch('helpdesk.ego_browser.subprocess.run') as run:
            with self.assertRaises(ValueError):
                self.adapter.submit('run','https://example.org/','fixture',response_token='fixture')
            run.assert_not_called()
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete):
            self.adapter.submit(*self.args,**self.kwargs)
        (self.root/'receipts/run-teach.answer.json').write_text('tampered')
        with self.assertRaisesRegex(BrowserPaused,'CAPTURE_CHANGED'):
            self.adapter.submit(*self.args,**self.kwargs)
