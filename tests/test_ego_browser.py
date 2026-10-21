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
        controls=patch('helpdesk.ego_browser.page_control_directory',return_value=self.root/'controls')
        controls.start();self.addCleanup(controls.stop)
        self.file=self.root/'approved.md'
        self.file.write_text('anonymous fixture',encoding='utf-8')
        self.adapter=EgoDeepSeek(1,self.root/'receipts',allowed_files=[self.file],executable=self.root/'ego.exe')
        self.args=('run-teach','https://chat.deepseek.com/a/chat/s/fixture','fixture prompt')
        self.kwargs={'attachments':[self.file]}

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

    def test_recreated_space_binds_name_and_keeps_unsubmitted_legacy_journal(self):
        controls=self.root/'controls';controls.mkdir()
        prior=self.root/'old-state.json';prior.write_text(json.dumps({'phase':'PREPARING'}))
        owner=controls/'space-1-p1.owner';owner.write_text(str(prior))
        adapter=EgoDeepSeek(1,self.root/'receipts',space_name='new-space',executable=self.root/'ego.exe')
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete):
            adapter.submit(*self.args)
        scripts=list((self.root/'receipts').glob('round-*.mjs'))
        self.assertIn('"space_name": "new-space"',scripts[0].read_text())
        self.assertEqual(owner.read_text(),str(prior))
        self.assertEqual(json.loads(prior.read_text())['phase'],'PREPARING')

    def test_named_space_cannot_bypass_unknown_legacy_submission(self):
        controls=self.root/'controls';controls.mkdir()
        prior=self.root/'old-state.json';prior.write_text(json.dumps({'phase':'SUBMISSION_INTENT'}))
        (controls/'space-1-p1.owner').write_text(str(prior))
        adapter=EgoDeepSeek(1,self.root/'receipts',space_name='new-space',executable=self.root/'ego.exe')
        with patch('helpdesk.ego_browser.subprocess.run') as run:
            with self.assertRaisesRegex(BrowserPaused,'LEGACY_OUTCOME'):
                adapter.submit(*self.args)
            run.assert_not_called()

    def test_named_unknown_request_blocks_legacy_and_other_names_on_same_numeric_page(self):
        first=EgoDeepSeek(1,self.root/'receipts',space_name='space-name',executable=self.root/'ego.exe')
        def timeout(*args,**kwargs):
            path=self.root/'receipts/run-teach.state.json'
            state=json.loads(path.read_text());state['phase']='SUBMISSION_INTENT'
            path.write_text(json.dumps(state))
            raise subprocess.TimeoutExpired('fixture',1)
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=timeout) as run:
            with self.assertRaisesRegex(BrowserPaused,'OUTCOME_UNKNOWN'):first.submit(*self.args)
            for name in (None,'renamed-space'):
                other=EgoDeepSeek(1,self.root/'other',space_name=name,executable=self.root/'ego.exe')
                with self.assertRaisesRegex(BrowserPaused,'PAGE_HAS_UNRESOLVED_REQUEST'):
                    other.submit('another',self.args[1],'another question')
            self.assertEqual(run.call_count,1)

    def test_named_spaces_share_the_original_physical_page_lock(self):
        from helpdesk.locking import resource_lock
        adapter=EgoDeepSeek(1,self.root/'receipts',space_name='named-space',executable=self.root/'ego.exe')
        with resource_lock(self.root/'controls/space-1-p1.lock'),patch('helpdesk.ego_browser.resource_lock',
                side_effect=lambda path,timeout:resource_lock(path,timeout=.02)),patch('helpdesk.ego_browser.subprocess.run') as run:
            with self.assertRaises(TimeoutError):adapter.submit(*self.args)
            run.assert_not_called()

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

    def test_old_marker_protocol_cannot_start_a_new_request(self):
        with patch('helpdesk.ego_browser.subprocess.run') as run:
            with self.assertRaisesRegex(BrowserPaused, 'LEGACY_PROTOCOL_RECOVERY_ONLY'):
                self.adapter.submit(*self.args, response_token='old_marker')
            run.assert_not_called()
        self.assertFalse((self.root/'receipts/run-teach.state.json').exists())

    def test_old_captured_request_is_readable_without_browser(self):
        request={'space_id':1,'page':'p1','session_url':self.args[1],
                 'prompt':self.args[2],'files':[],'response_token':'old_marker'}
        fingerprint=sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        directory=self.root/'receipts';directory.mkdir()
        raw=json.dumps({'text':'historical reply','delivered':False}).encode()
        (directory/'run-teach.answer.json').write_bytes(raw)
        (directory/'run-teach.state.json').write_text(json.dumps({
            'fingerprint':fingerprint,'phase':'CAPTURED','output_sha256':sha256(raw).hexdigest()}))
        with patch('helpdesk.ego_browser.subprocess.run') as run:
            result=self.adapter.submit(*self.args,response_token='old_marker')
            self.assertEqual(result['text'],'historical reply')
            run.assert_not_called()

    def test_uncertain_page_blocks_other_requests_across_evidence_directories(self):
        def timeout(*args, **kwargs):
            path=self.root/'receipts/run-teach.state.json'
            state=json.loads(path.read_text());state['phase']='SUBMISSION_INTENT'
            path.write_text(json.dumps(state))
            raise subprocess.TimeoutExpired('fixture',1)
        other=EgoDeepSeek(1,self.root/'other-receipts',executable=self.root/'ego.exe')
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=timeout) as run:
            with self.assertRaisesRegex(BrowserPaused,'OUTCOME_UNKNOWN'):
                self.adapter.submit(*self.args,**self.kwargs)
            with self.assertRaisesRegex(BrowserPaused,'PAGE_HAS_UNRESOLVED_REQUEST'):
                other.submit('other-run',self.args[1],'different question')
            self.assertEqual(run.call_count,1)
        self.assertFalse((self.root/'other-receipts/other-run.state.json').exists())
        with patch('helpdesk.ego_browser.subprocess.run',side_effect=self.complete):
            self.adapter.submit(*self.args,**self.kwargs,capture_only=True)
        self.assertFalse((self.root/'controls/space-1-p1.owner').exists())

    def test_busy_page_lock_stops_before_creating_request_or_browser_action(self):
        from helpdesk.locking import resource_lock
        control=self.root/'controls/space-1-p1.lock'
        with resource_lock(control), patch('helpdesk.ego_browser.resource_lock',
                side_effect=lambda path,timeout:resource_lock(path,timeout=.02)), \
                patch('helpdesk.ego_browser.subprocess.run') as run:
            with self.assertRaises(TimeoutError):
                self.adapter.submit(*self.args,**self.kwargs)
            run.assert_not_called()
        self.assertFalse((self.root/'receipts/run-teach.state.json').exists())
