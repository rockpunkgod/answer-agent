import json
import ctypes
import unittest
from datetime import timedelta

from helpdesk.windows_worker_probe import (DESKTOP_STATUS_COMMAND, parse_desktop_status,
    parse_desktop_record, build_worker_health)
from helpdesk.worker_contracts import WorkerHealth, utc_now


class WindowsWorkerProbeTests(unittest.TestCase):
    def setUp(self):
        self.now = utc_now()
        self.after = (self.now - timedelta(seconds=2)).isoformat()
        self.value = dict(schema_version=1, observed_at=(self.now-timedelta(seconds=1)).isoformat(),
            session_id=1, wts_state=0, session_flags=1, remote=False, input_desktop='DEFAULT',
            input_accessible=True, windows={'wxwork': 1, 'edge': 2})

    def parse(self, **changes):
        return parse_desktop_status(json.dumps(dict(self.value, **changes)),
            observed_after=self.after, now=self.now)

    def app(self, **changes):
        value = dict(worker_id='worker-anon', account_id='account-anon', connected=True,
            interactive_desktop=True, desktop_unlocked=True, state='HEALTHY', profile_id='profile-anon',
            observed_scope='scope-anon', observed_session='session-anon',
            observed_at=self.value['observed_at'], native_call_pending=False)
        value.update(changes)
        return WorkerHealth(**value)

    def health(self, status, app=None, expected_platform='deepseek'):
        return build_worker_health(status,worker_id='worker-anon',account_id='account-anon',
            app_observation=app,expected_platform=expected_platform,now=self.now)

    def test_valid_console_needs_independent_application_observation(self):
        status = self.parse()
        self.assertTrue(status.interactive)
        self.assertTrue(status.unlocked)
        self.assertFalse(self.health(status).gui_ready)
        self.assertEqual(self.health(status).state, 'UNKNOWN')
        self.assertTrue(self.health(status,self.app()).gui_ready)

    def test_session_zero_service_is_not_interactive(self):
        status = self.parse(session_id=0)
        self.assertFalse(status.interactive)
        self.assertFalse(self.health(status,self.app()).gui_ready)

    def test_locked_disconnected_unknown_and_nondefault_desktop_fail_closed(self):
        for changes in ({'session_flags':0}, {'wts_state':4}, {'wts_state':-1},
                {'session_flags':-1}, {'remote':None}, {'input_desktop':'OTHER'},
                {'input_desktop':None,'input_accessible':False}, {'session_id':-1},
                {'windows':None}):
            with self.subTest(changes=changes):
                self.assertFalse(self.health(self.parse(**changes),self.app()).gui_ready)

    def test_remote_active_session_can_be_interactive_but_not_infer_unlocked(self):
        self.assertTrue(self.parse(remote=True).interactive)
        self.assertTrue(self.parse(remote=True).unlocked)
        self.assertFalse(self.parse(remote=False,session_flags=-1).unlocked)
        self.assertFalse(self.parse(remote=True,session_flags=0).unlocked)

    def test_stale_future_pre_attempt_and_naive_times_rejected(self):
        for at in ((self.now-timedelta(seconds=40)).isoformat(),
                   (self.now+timedelta(seconds=1)).isoformat(),
                   (self.now-timedelta(seconds=3)).isoformat(), '2026-10-01T10:00:00'):
            with self.subTest(at=at), self.assertRaises(ValueError):
                self.parse(observed_at=at)

    def test_unknown_extra_fields_wrong_booleans_and_counts_rejected(self):
        for changes in ({'secret':'sentinel'}, {'schema_version':True}, {'remote':0},
                {'input_accessible':1}, {'session_id':True}, {'wts_state':10},
                {'session_flags':2}, {'input_desktop':'Winlogon'},
                {'windows':{'wxwork':True,'edge':1}}, {'windows':{'title':'private'}},
                {'input_desktop':None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(**changes)

    def test_mixed_duplicate_oversized_and_error_output_rejected(self):
        valid=json.dumps(self.value)
        for text in ('noise\n'+valid, valid+'\nnoise', valid+valid,
                valid.replace('"schema_version": 1','"schema_version": 1, "schema_version": 1'),
                'x'*4097, 'Response: '+valid+'\nStatus Code: 1'):
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                parse_desktop_status(text,observed_after=self.after,now=self.now)

    def test_exact_mcp_text_envelope_and_attempt_time_binding(self):
        record={'tool':'PowerShell','is_error':False,'content':[{'type':'text',
            'text':'Response: '+json.dumps(self.value)+'\nStatus Code: 0'}]}
        self.assertTrue(parse_desktop_record(record,observed_after=self.after,now=self.now).unlocked)
        for changed in (dict(record,is_error=True), dict(record,tool='Other'),
                        dict(record,content=record['content']*2)):
            with self.assertRaises(ValueError):
                parse_desktop_record(changed,observed_after=self.after,now=self.now)

    def test_app_identity_time_scope_and_state_preserved(self):
        status=self.parse()
        for app in (self.app(account_id='other'), self.app(observed_at=(self.now-timedelta(seconds=40)).isoformat())):
            with self.assertRaises(ValueError):self.health(status,app)
        self.assertFalse(self.health(status,self.app(observed_scope=None)).gui_ready)
        self.assertEqual(self.health(status,self.app(state='LOGIN_REQUIRED')).state,'LOGIN_REQUIRED')
        self.assertFalse(self.health(status,self.app(native_call_pending=True)).gui_ready)
        with self.assertRaises(ValueError): self.health(status,{'state':'HEALTHY'})

    def test_probe_constant_uses_metadata_only_and_no_mutation_calls(self):
        for token in ('ProcessIdToSessionId','WTSQuerySessionInformation','OpenInputDesktop','CloseDesktop','RtlGetVersion'):
            self.assertIn(token,DESKTOP_STATUS_COMMAND)
        for token in ('GetWindowText','SetForegroundWindow','SwitchDesktop','SetThreadDesktop',
                      'GetClipboard','SendInput','Start-Process','Get-Credential'):
            self.assertNotIn(token,DESKTOP_STATUS_COMMAND)

    def test_app_observation_requires_explicit_supported_platform_and_window(self):
        app=self.app()
        for platform, windows in ((None,{'wxwork':1,'edge':1}),
                ('deepseek',{'wxwork':1,'edge':0}), ('wecom',{'wxwork':0,'edge':1}),
                ('deepseek',{'wxwork':0,'edge':0}), ('wecom',None)):
            with self.subTest(platform=platform,windows=windows):
                h=self.health(self.parse(windows=windows),app,platform)
                self.assertEqual(h.state,'UNKNOWN')
                self.assertFalse(h.gui_ready)
        self.assertTrue(self.health(self.parse(),app,'wecom').gui_ready)
        with self.assertRaises(ValueError): self.health(self.parse(),app,'other')

    def test_app_desktop_unavailable_cannot_be_overridden_by_wts(self):
        for changes in ({'interactive_desktop':False}, {'desktop_unlocked':False},
                {'interactive_desktop':False,'desktop_unlocked':False}):
            with self.subTest(changes=changes):
                h=self.health(self.parse(),self.app(**changes))
                self.assertEqual(h.state,'DESKTOP_UNAVAILABLE')
                self.assertFalse(h.gui_ready)
                for field in changes:
                    self.assertFalse(getattr(h,field))

    def test_wts_information_class_and_unicode_abi_are_explicit(self):
        for name, number in (('WTS_CONNECT_STATE',8), ('WTS_CLIENT_PROTOCOL',16), ('WTS_SESSION_INFO_EX',25)):
            self.assertIn('public const int '+name+'='+str(number)+';',DESKTOP_STATUS_COMMAND)
            self.assertIn('kind=='+name,DESKTOP_STATUS_COMMAND)
            self.assertIn('::Query($probeSession,[HelpdeskDesktopStatusV2]::'+name+')',DESKTOP_STATUS_COMMAND)
        self.assertNotIn('kind==29',DESKTOP_STATUS_COMMAND)
        self.assertNotIn('::Query($probeSession,29)',DESKTOP_STATUS_COMMAND)
        self.assertIn('EntryPoint="WTSQuerySessionInformationW",CharSet=CharSet.Unicode,ExactSpelling=true',DESKTOP_STATUS_COMMAND)
        self.assertIn('CharSet=CharSet.Unicode,Pack=8)] struct Level1',DESKTOP_STATUS_COMMAND)
        self.assertIn('CharSet=CharSet.Unicode,Pack=8)] struct InfoEx',DESKTOP_STATUS_COMMAND)
        self.assertIn('Marshal.OffsetOf(typeof(InfoEx),"Data").ToInt32()!=8',DESKTOP_STATUS_COMMAND)
        self.assertIn('Marshal.SizeOf(typeof(InfoEx))!=232',DESKTOP_STATUS_COMMAND)
        self.assertIn('Marshal.SizeOf(typeof(Level1))!=224',DESKTOP_STATUS_COMMAND)

    def test_anonymous_unicode_w_layout_offsets_without_windows_calls(self):
        class Level1(ctypes.Structure):
            _pack_=8
            _fields_=[('session',ctypes.c_uint32),('state',ctypes.c_int32),('flags',ctypes.c_int32),
                ('station',ctypes.c_uint16*33),('user',ctypes.c_uint16*21),('domain',ctypes.c_uint16*18),
                ('times',ctypes.c_int64*5),('counters',ctypes.c_uint32*6)]
        class InfoEx(ctypes.Structure):
            _pack_=8
            _fields_=[('level',ctypes.c_uint32),('data',Level1)]
        self.assertEqual(Level1.flags.offset,8)
        self.assertEqual(Level1.times.offset,160)
        self.assertEqual(ctypes.sizeof(Level1),224)
        self.assertEqual(InfoEx.data.offset,8)
        self.assertEqual(ctypes.sizeof(InfoEx),232)


if __name__ == '__main__':
    unittest.main()
