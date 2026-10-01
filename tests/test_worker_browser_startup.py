"""Startup contract only. No real ACL, user browser, or desktop process."""
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from helpdesk.worker_browser_session import BrowserOutcome
from tools import prepare_worker_browser as tool
from tools.prepare_worker_browser import startup_allowed, recovery_allowed, local_native_quiescent


class BrowserStartupControlTests(unittest.TestCase):
    def valid(self):
        return dict(global_stop=False,worker_boundary=True,accounts=[],commands=[])

    def test_explicit_quiescent_operator_state_is_required(self):
        self.assertTrue(startup_allowed(self.valid()))
        for x in ({},dict(self.valid(),global_stop=None),dict(self.valid(),worker_boundary=False),
                  dict(self.valid(),global_stop=True)):
            self.assertFalse(startup_allowed(x))

    def test_human_ownership_pending_input_or_unknown_action_blocks_open(self):
        for takeover in ['REQUESTED','QUIESCING','OWNED','RESUME_CHECK']:
            a=dict(stop=False,takeover=takeover,quarantined=False)
            self.assertFalse(startup_allowed(dict(self.valid(),accounts=[a])))
        for status in ['RUNNING','OUTCOME_UNKNOWN','WAITING_HUMAN']:
            self.assertFalse(startup_allowed(dict(self.valid(),commands=[{'status':status}])))

    def test_account_stop_and_quarantine_are_not_overridden_by_empty_queue(self):
        a=dict(stop=False,takeover='AUTO_ACTIVE',quarantined=False)
        self.assertTrue(startup_allowed(dict(self.valid(),accounts=[a])))
        self.assertFalse(startup_allowed(dict(self.valid(),accounts=[dict(a,stop=True)])))
        self.assertFalse(startup_allowed(dict(self.valid(),accounts=[dict(a,quarantined=True)])))

    def test_unknown_or_missing_command_state_does_not_authorize_browser_start(self):
        for command in ({}, {'status': None}, {'status': 'INVALID_STATE'},
                        {'status': []}):
            self.assertFalse(startup_allowed(dict(self.valid(),commands=[command])))

    def test_account_pause_blocks_preparation_even_with_empty_queue(self):
        a=dict(stop=False,takeover='AUTO_ACTIVE',quarantined=False,pause_reason='VERIFICATION_REQUIRED')
        self.assertFalse(startup_allowed(dict(self.valid(),accounts=[a])))

    def test_recovery_requires_positive_global_stop_including_zero_accounts(self):
        self.assertFalse(recovery_allowed(self.valid()))
        self.assertTrue(recovery_allowed(dict(self.valid(),global_stop=True)))
        self.assertFalse(startup_allowed(dict(self.valid(),global_stop=True)))
        for changes in ({'global_stop':None},{'worker_boundary':False},
                        {'commands':[{'status':'OUTCOME_UNKNOWN'}]}, {'commands':[{}]}):
            value=dict(self.valid(),global_stop=True)
            value.update(changes)
            self.assertFalse(recovery_allowed(value))

    def test_recovery_rejects_pending_takeover_or_other_account_quarantine(self):
        stopped=dict(self.valid(),global_stop=True)
        a=dict(stop=True,takeover='AUTO_ACTIVE',quarantined=False)
        self.assertTrue(recovery_allowed(dict(stopped,accounts=[a])))
        self.assertTrue(recovery_allowed(dict(stopped,accounts=[dict(a,takeover='OWNED')])))
        for changes in ({'stop':False},{'quarantined':True},{'takeover':'QUIESCING'},
                        {'takeover':'RESUME_CHECK'},{'takeover':[]}):
            with self.subTest(changes=changes):
                self.assertFalse(recovery_allowed(dict(stopped,accounts=[dict(a,**changes)])))


class LocalNativeJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.state=self.root/'data/worker-runtime'

    def seed(self,name,value):
        self.state.mkdir(parents=True,exist_ok=True)
        (self.state/name).write_text(json.dumps(value),encoding='utf-8')

    def test_absent_journal_means_no_known_pending_not_machine_proof(self):
        self.assertTrue(local_native_quiescent(self.root))

    def test_any_native_quarantine_pause_or_interrupted_write_blocks(self):
        for name in ('quarantine-any.json','account-pause-any.json','command-any.tmp'):
            with self.subTest(name=name):
                self.seed(name,{})
                self.assertFalse(local_native_quiescent(self.root))
                (self.state/name).unlink()

    def test_unfinished_unknown_or_corrupt_local_command_blocks(self):
        for value in ({}, {'native_call_pending':True},
                      {'native_call_pending':False},
                      {'native_call_pending':False,'result':{'native_call_pending':False,'status':'OUTCOME_UNKNOWN'}},
                      {'native_call_pending':False,'result':{'native_call_pending':True,'status':'SUCCEEDED_VERIFIED'}}):
            with self.subTest(value=value):
                self.seed('command-any.json',value)
                self.assertFalse(local_native_quiescent(self.root))
        (self.state/'command-any.json').write_text('broken',encoding='utf-8')
        self.assertFalse(local_native_quiescent(self.root))

    def test_complete_confirmed_local_record_is_safe_and_never_deleted(self):
        value={'native_call_pending':False,'result':{'native_call_pending':False,'status':'SUCCEEDED_VERIFIED'}}
        self.seed('command-any.json',value)
        before=(self.state/'command-any.json').read_bytes()
        self.assertTrue(local_native_quiescent(self.root))
        self.assertEqual((self.state/'command-any.json').read_bytes(),before)


class FixedMaintenanceEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.profile_root=self.root/'data/worker-profiles'
        self.profile_root.mkdir(parents=True)
        self.edge=self.root/'fake-msedge.exe'
        self.edge.write_text('never executed',encoding='utf-8')
        self.control=dict(global_stop=True,worker_boundary=True,accounts=[],commands=[])

    def invoke(self,args,control=None):
        output=StringIO()
        profiles=MagicMock(root=self.profile_root)
        session=MagicMock()
        session.recover_unstarted.return_value=BrowserOutcome('CLOSED','UNSTARTED_PROFILE_RECOVERED',tool.PROFILE_ID,tool.ACCOUNT_ID)
        session.prepare.return_value=BrowserOutcome('PREPARED','PROFILE_READY',tool.PROFILE_ID,tool.ACCOUNT_ID)
        with ExitStack() as stack:
            stack.enter_context(patch.object(tool,'ROOT',self.root))
            stack.enter_context(patch.object(tool,'EDGE_CANDIDATES',(self.edge,)))
            stack.enter_context(patch.object(tool,'control_snapshot',return_value=self.control if control is None else control))
            manager=stack.enter_context(patch.object(tool,'ProfileManager',return_value=profiles))
            factory=stack.enter_context(patch.object(tool,'DedicatedBrowserSession',return_value=session))
            observer=stack.enter_context(patch.object(tool,'WindowsProfileQuiescenceObserver'))
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(StringIO()))
            result=tool.main(args)
        return result,output.getvalue(),manager,profiles,session,factory,observer

    def test_recovery_only_uses_existing_fixed_binding_and_does_not_register(self):
        code,output,manager,profiles,session,factory,observer=self.invoke(['--recover-preparation'])
        self.assertEqual(code,0)
        profiles.register.assert_not_called()
        session.prepare.assert_not_called()
        session.start.assert_not_called()
        self.assertIn('FAILED_PREPARATION_MAINTENANCE',output)
        self.assertFalse(json.loads(output)['gui_ready_verified'])
        self.assertEqual(factory.call_args.args[1:3],(tool.PROFILE_ID,tool.ACCOUNT_ID))
        self.assertTrue(callable(session.recover_unstarted.call_args.kwargs['control_gate']))
        observer.return_value.assert_not_called()

    def test_running_server_blocks_recovery_before_profile_constructor(self):
        code,output,manager,profiles,session,factory,observer=self.invoke(
            ['--recover-preparation'],dict(self.control,global_stop=False))
        self.assertEqual(code,2)
        manager.assert_not_called()
        factory.assert_not_called()
        observer.assert_not_called()

    def test_stop_blocks_regular_preparation_before_profile_creation(self):
        code,output,manager,profiles,session,factory,observer=self.invoke([])
        self.assertEqual(code,2)
        manager.assert_not_called()

    def test_local_pending_blocks_recovery_before_profile_registration(self):
        state=self.root/'data/worker-runtime'
        state.mkdir()
        (state/'quarantine-local.json').write_text('{}',encoding='utf-8')
        code,output,manager,profiles,session,factory,observer=self.invoke(['--recover-preparation'])
        self.assertEqual(code,2)
        manager.assert_not_called()
        self.assertTrue((state/'quarantine-local.json').exists())

    def test_recovery_and_open_cannot_share_invocation(self):
        with self.assertRaises(SystemExit) as raised, redirect_stdout(StringIO()):
            self.invoke(['--recover-preparation','--open'])
        self.assertEqual(raised.exception.code,2)

    def test_arbitrary_alternative_profile_is_not_accepted(self):
        with self.assertRaises(SystemExit) as raised, redirect_stdout(StringIO()):
            self.invoke(['--profile-id','replacement-profile'])
        self.assertEqual(raised.exception.code,2)
