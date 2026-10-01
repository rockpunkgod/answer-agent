"""Anonymous classifier and proof tests. No native inventory or user profile."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk.worker_browser_recovery import (
    ProfileQuiescence, WindowsProfileQuiescenceObserver, profile_path_digest,
)


class RecoveryProofTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 0, 0, 2, tzinfo=timezone.utc)
        self.path = Path('C:/anonymous/已批准目录')
        self.proof = ProfileQuiescence('profile-a', 'account-a',
            profile_path_digest(self.path), 'a' * 64, self.now.isoformat(),
            7, 0, 0, 0, True, 'MACHINE_CONFIGURED_EXECUTORS')

    def validate(self, proof):
        proof.validate('profile-a', 'account-a', self.path, 'a' * 64,
                       after=(self.now - timedelta(seconds=1)).isoformat(), now=self.now)

    def test_fresh_bound_machine_scan_is_accepted(self):
        self.validate(self.proof)

    def test_incomplete_or_other_session_scope_is_not_machine_proof(self):
        for changes in ({'scan_scope':'CURRENT_SESSION_ONLY'}, {'scan_scope':'UNCONFIRMED'},
                        {'scan_completed':False}, {'session_id':0}, {'session_id':True},
                        {'account_id':'account-b'}, {'marker_sha256':'b'*64},
                        {'profile_path_sha256':'c'*64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(replace(self.proof, **changes))

    def test_delayed_future_or_pre_request_proof_is_rejected(self):
        for seconds in (-6, -2, 1):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.validate(replace(self.proof,
                    observed_at=(self.now + timedelta(seconds=seconds)).isoformat()))

    def test_any_actor_or_unknown_count_blocks_recovery(self):
        for field in ('matching_browser_processes', 'unknown_processes', 'other_executor_processes'):
            for count in (1, -1, True, '0'):
                with self.subTest(field=field, count=count), self.assertRaises(ValueError):
                    self.validate(replace(self.proof, **{field:count}))

    @unittest.skipUnless(os.name == 'nt', 'Windows fixed subprocess seam')
    def test_fixed_probe_only_returns_scoped_metadata_and_discards_errors(self):
        value = dict(session_id=7, matching_browser_processes=0, unknown_processes=0,
            other_executor_processes=0, scan_completed=True,
            scan_scope='MACHINE_CONFIGURED_EXECUTORS', observed_at=self.now.isoformat())
        with patch('helpdesk.worker_browser_recovery.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value))) as run:
            self.validate(WindowsProfileQuiescenceObserver()('profile-a', 'account-a', self.path, 'a'*64))
        self.assertEqual(run.call_args.args[0],
            ['powershell.exe','-NoProfile','-NonInteractive','-Command',WindowsProfileQuiescenceObserver._SCRIPT])
        self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        for stdout in ('{}', json.dumps(dict(value, command_line='private-unrelated-value')), 'x'*2049):
            with self.subTest(stdout_bytes=len(stdout)), patch(
                'helpdesk.worker_browser_recovery.subprocess.run',
                return_value=SimpleNamespace(returncode=0, stdout=stdout)):
                with self.assertRaisesRegex(RuntimeError, '^PROFILE_PROCESS_SCAN_UNAVAILABLE$'):
                    WindowsProfileQuiescenceObserver()('profile-a','account-a',self.path,'a'*64)


@unittest.skipUnless(os.name == 'nt', 'Anonymous PowerShell classifier')
class AnonymousWindowsClassifierTests(unittest.TestCase):
    def run_classifier(self, rows, *, include_owner=True, scanner_parent=40401):
        # This executes the production classifier with invented rows only. It
        # never imports/runs CIM, lists processes, or reads a user's command line.
        script = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$inputData=ConvertFrom-Json ([Console]::In.ReadToEnd())
$path='c:\anonymous\已批准目录'
$ownPid=40401
$session=7
$matchingBrowsers=0
$unknown=0
$executors=0
$rows=@($inputData.rows)
if ($inputData.include_owner) {
 $rows+= [pscustomobject]@{Name='python.exe';ProcessId=$ownPid;ParentProcessId=1;SessionId=$session;CommandLine='anonymous helper'}
}
$rows+= [pscustomobject]@{Name='powershell.exe';ProcessId=$PID;ParentProcessId=[int]$inputData.scanner_parent;SessionId=$session;CommandLine='anonymous scanner'}
''' + WindowsProfileQuiescenceObserver._CLASSIFY
        self.assertNotIn('Get-CimInstance', script)
        self.assertNotIn('Get-WmiObject', script)
        reply = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
            input=json.dumps(dict(rows=rows,include_owner=include_owner,scanner_parent=scanner_parent)),
            text=True, encoding='utf-8', errors='strict', timeout=15, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return reply.returncode, json.loads(reply.stdout) if reply.returncode == 0 else None

    def row(self, name='msedge.exe', line='msedge --user-data-dir=C:/anonymous/已批准目录', session=7):
        return dict(Name=name, CommandLine=line, ProcessId=55001,
                    ParentProcessId=1, SessionId=session)

    def test_owned_helper_and_scanner_alone_are_quiet(self):
        code, proof = self.run_classifier([])
        self.assertEqual(code, 0)
        self.assertEqual([proof[x] for x in ('matching_browser_processes','unknown_processes','other_executor_processes')],[0,0,0])
        self.assertEqual(proof['scan_scope'],'MACHINE_CONFIGURED_EXECUTORS')

    def test_cross_session_browser_is_not_ignored(self):
        code, proof = self.run_classifier([self.row(session=42)])
        self.assertEqual(code, 0)
        self.assertEqual(proof['matching_browser_processes'],1)

    def test_alias_default_or_unrelated_edge_is_unknown(self):
        # None of these lines may be used as positive absence proof.
        for line in ('msedge', 'msedge --user-data-dir=C:/ANONYM~1/PROFILE',
                     'msedge --user-data-dir=C:/unrelated/profile', 'msedge --type=renderer', ''):
            with self.subTest(line=line):
                code, proof = self.run_classifier([self.row(line=line,session=42)])
                self.assertEqual(code,0)
                self.assertEqual(proof['unknown_processes'],1)

    def test_other_python_shell_node_or_script_executor_blocks(self):
        names=('python.exe','pythonw.exe','python3.13.exe','py.exe','powershell.exe',
               'pwsh.exe','node.exe','uv.exe','msedgedriver.exe','chromedriver.exe',
               'wscript.exe','cscript.exe','AutoHotkeyU64.exe')
        rows=[dict(self.row(name=name,line='unrelated anonymous program',session=42),ProcessId=55001+i)
              for i,name in enumerate(names)]
        code, proof = self.run_classifier(rows)
        self.assertEqual(code,0)
        self.assertEqual(proof['other_executor_processes'],len(names))

    def test_inaccessible_executor_is_unknown(self):
        code, proof = self.run_classifier([self.row(name='node.exe',line=None)])
        self.assertEqual(code,0)
        self.assertEqual(proof['unknown_processes'],1)

    def test_missing_owner_or_unowned_scanner_never_returns_scan(self):
        for changes in ({'include_owner':False},{'scanner_parent':40402}):
            with self.subTest(changes=changes):
                code, proof = self.run_classifier([],**changes)
                self.assertNotEqual(code,0)
                self.assertIsNone(proof)


if __name__ == '__main__': unittest.main()
