from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest

from helpdesk.locking import resource_lock
from helpdesk.worker_contracts import (Action, ExecutionBudget, ExecutionStatus, WorkerControl,
    WorkerPolicy, utc_now)
from helpdesk.worker_runtime import SafeReadFailure, WorkerRuntime
from tests.test_worker_contracts import approval, command, health


def control(c, **changes):
    values=dict(lease_live=True,version_current=True,authorization_current=True,takeover_epoch=c.execution_epoch)
    values.update(changes)
    return WorkerControl(**values)


def policy(c):
    return WorkerPolicy(mode='ASSISTED',allowed_actions=[c.action],allowed_accounts=[c.account_id],
                        allowed_scopes=[c.target_scope],allowed_profiles=[c.profile_id] if c.profile_id else [])


def evidence(kind='GENERATION'):
    return [dict(evidence_ref='fixture-evidence',sha256='a'*64,kind=kind,observed_at=utc_now().isoformat())]


class WorkerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime=WorkerRuntime(self.temp.name,native_timeout_seconds=.05)
        self.c=command()
        self.runtime.profiles.register(self.c.profile_id,self.c.account_id,self.runtime.profiles.root/'browser')

    def run_command(self,handler,*,c=None,controls=None,healthy=None,runtime=None):
        c=c or self.c
        return (runtime or self.runtime).execute(c,approval(c),policy(c),
            control_provider=controls or (lambda:control(c)),health_provider=healthy or (lambda:health(c)),handler=handler)

    def test_verified_bound_evidence_and_duplicate_never_reexecutes(self):
        calls=[]
        def handler(c,g):
            g.call(lambda:calls.append('native'),read_only=False)
            return g.verified(evidence())
        result=self.run_command(handler)
        self.assertEqual(result.status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertEqual(self.run_command(handler,runtime=WorkerRuntime(self.temp.name)),result)
        self.assertEqual(calls,['native'])
        duplicate=replace(self.c,command_id='another-command')
        self.assertEqual(self.run_command(handler,c=duplicate).reason_code,'IDEMPOTENCY_ALREADY_BOUND')
        self.assertEqual(self.runtime._read('idempotency',self.c.idempotency_key)['command_id'],self.c.command_id)
        changed=replace(self.c,parameters=dict(self.c.parameters,input_sha256='b'*64))
        self.assertEqual(self.run_command(handler,c=changed).status,ExecutionStatus.STALE)
        self.assertEqual(calls,['native'])

    def test_expired_authorization_and_login_have_zero_input(self):
        calls=[]
        handler=lambda c,g:g.call(lambda:calls.append(1),read_only=False)
        c=replace(self.c,created_at=(utc_now()-timedelta(hours=2)).isoformat(),
                  expires_at=(utc_now()-timedelta(hours=1)).isoformat())
        self.assertEqual(self.run_command(handler,c=c).status,ExecutionStatus.STALE)
        c=replace(self.c,command_id='second',idempotency_key='second')
        self.assertEqual(self.run_command(handler,c=c,healthy=lambda:health(c,state='LOGIN_REQUIRED')).reason_code,'LOGIN_REQUIRED')
        self.assertEqual(calls,[])

    def test_same_account_verification_pause_survives_new_instance(self):
        self.run_command(lambda c,g:None,healthy=lambda:health(self.c,state='VERIFICATION_REQUIRED'))
        c=replace(self.c,command_id='second',idempotency_key='second')
        calls=[]
        result=self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=False),c=c,runtime=WorkerRuntime(self.temp.name))
        self.assertEqual(result.reason_code,'VERIFICATION_REQUIRED')
        self.assertFalse(calls)

    def test_account_pause_released_only_after_verified_resume_session(self):
        self.run_command(lambda c,g:None,healthy=lambda:health(self.c,state='LOGIN_REQUIRED'))
        c=self.read_command(command_id='resume',idempotency_key='resume')
        def handler(c,g):
            g.call(lambda:None,read_only=True)
            return g.verified(evidence('SESSION'))
        result=self.run_command(handler,c=c,controls=lambda:control(c,takeover='RESUME_CHECK',stop=True,paused_reason='LOGIN_REQUIRED'))
        self.assertEqual(result.status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertIsNone(self.runtime._read('account-pause',c.account_id))
        self.assertTrue(self.runtime._read('account-recovery',c.account_id)['evidence'])

    def test_unregistered_profile_has_zero_native_calls(self):
        c=replace(self.c,profile_id='unregistered')
        calls=[]
        result=self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=False),c=c)
        self.assertNotEqual(result.status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertFalse(calls)

    def test_stop_and_takeover_checked_between_every_action(self):
        for state in ('stop','takeover'):
            with self.subTest(state=state):
                c=replace(self.c,command_id=state,idempotency_key=state)
                calls=[]
                values={}
                def handler(c,g):
                    def first():
                        calls.append(1)
                        values.update({'stop':True} if state=='stop' else {'takeover':'OWNED'})
                    g.call(first,read_only=False)
                    g.call(lambda:calls.append(2),read_only=False)
                result=self.run_command(handler,c=c,controls=lambda:control(c,**values))
                self.assertEqual(calls,[1])
                self.assertEqual(result.status,ExecutionStatus.OUTCOME_UNKNOWN)
                # Each subcase uses a distinct quarantined fixture root.
                self.runtime.reconcile_quarantine(c.command_id,health=health(c),evidence=evidence('HEALTH'),
                    native_probe=lambda:dict(command_id=c.command_id,trusted=True,native_call_pending=False,process_quiescent=True))

    def test_version_or_epoch_change_after_write_keeps_unknown(self):
        for tag,changed in [('version',{'version_current':False}),('epoch',{'takeover_epoch':2})]:
            with self.subTest(tag=tag):
                c=replace(self.c,command_id=tag,idempotency_key=tag)
                values={}
                def handler(c,g):
                    g.call(lambda:values.update(changed),read_only=False)
                    return g.verified(evidence())
                result=self.run_command(handler,c=c,controls=lambda:control(c,**values))
                self.assertEqual(result.status,ExecutionStatus.OUTCOME_UNKNOWN)
                self.assertEqual(result.side_effect,'UNKNOWN')
                self.runtime.reconcile_quarantine(c.command_id,health=health(c),evidence=evidence('HEALTH'),
                    native_probe=lambda:dict(command_id=c.command_id,trusted=True,native_call_pending=False,process_quiescent=True))

    def test_human_takeover_disallows_even_read(self):
        c=self.read_command()
        for takeover in ('REQUESTED','QUIESCING','OWNED'):
            c=replace(c,command_id=takeover,idempotency_key=takeover)
            calls=[]
            self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=True),c=c,
                             controls=lambda:control(c,takeover=takeover))
            self.assertFalse(calls)

    def read_command(self, **changes):
        return replace(self.c,action=Action.CHECK_SESSION,parameters={'session_ref':self.c.session_id},**changes)

    def test_resume_check_only_explicit_safe_read_despite_stop_pause(self):
        c=self.read_command()
        calls=[]
        def handler(c,g):
            g.call(lambda:calls.append(1),read_only=True)
            return g.verified(evidence('SESSION'))
        result=self.run_command(handler,c=c,controls=lambda:control(c,takeover='RESUME_CHECK',stop=True,paused_reason='LOGIN_REQUIRED'))
        self.assertEqual(result.status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertEqual(calls,[1])
        c=replace(c,command_id='not-read',idempotency_key='not-read')
        result=self.run_command(lambda c,g:g.call(lambda:calls.append(2),read_only=False),c=c,
                                controls=lambda:control(c,takeover='RESUME_CHECK'))
        self.assertNotEqual(result.status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertEqual(calls,[1])

    def test_finite_calls_refresh_and_no_progress(self):
        for name,budget,refresh,count in [('calls',ExecutionBudget(max_tool_calls=2),False,2),
                ('refresh',ExecutionBudget(max_refreshes=1),True,1),
                ('progress',ExecutionBudget(max_no_progress=2),False,2)]:
            c=self.read_command(command_id=name,idempotency_key=name,budget=budget)
            calls=[]
            def handler(c,g):
                while True:g.call(lambda:calls.append(1),read_only=True,refresh=refresh)
            result=self.run_command(handler,c=c)
            self.assertEqual(result.reason_code,'ACTION_BUDGET_EXHAUSTED')
            self.assertEqual(len(calls),count)

    def test_page_changed_is_not_refreshed_or_retried(self):
        calls=[]
        result=self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=False),
                                healthy=lambda:health(self.c,state='PAGE_CHANGED'))
        self.assertEqual(result.reason_code,'PAGE_CHANGED')
        self.assertEqual(calls,[])

    def test_generation_wait_and_total_budgets_checkpoint_before_new_input(self):
        c=self.read_command(budget=ExecutionBudget(total_seconds=1,generation_wait_seconds=1))
        result=self.run_command(lambda c,g:g.wait(1.1),c=c)
        self.assertEqual(result.reason_code,'GENERATION_WAIT_EXHAUSTED')
        c=replace(c,command_id='total',idempotency_key='total')
        calls=[]
        def handler(c,g):
            # Deterministic exhaustion without waiting or modifying the clock.
            g.started -= 2
            g.call(lambda:calls.append(1),read_only=True)
        result=self.run_command(handler,c=c)
        self.assertEqual(result.reason_code,'TOTAL_BUDGET_EXHAUSTED')
        self.assertFalse(calls)
        self.assertEqual(self.runtime._read('command',c.command_id)['checkpoint'],'TOTAL_BUDGET_EXHAUSTED')

    def test_only_certified_finished_read_errors_retry(self):
        c=self.read_command(budget=ExecutionBudget(read_retries=2))
        calls=[]
        def failed():
            calls.append(1)
            raise SafeReadFailure('cookie=must-not-log')
        result=self.run_command(lambda c,g:g.call(failed,read_only=True),c=c)
        self.assertEqual(result.reason_code,'READ_RETRIES_EXHAUSTED')
        self.assertEqual(len(calls),3)
        self.assertFalse(self.runtime._read('quarantine','desktop'))

    def test_timeout_new_runtime_still_quarantined_after_thread_finishes(self):
        released=threading.Event()
        finished=threading.Event()
        def pending():
            released.wait(2)
            finished.set()
        try:
            result=self.run_command(lambda c,g:g.call(pending,read_only=False))
            self.assertEqual(result.status,ExecutionStatus.OUTCOME_UNKNOWN)
            self.assertTrue(result.native_call_pending)
            new=WorkerRuntime(self.temp.name)
            c=replace(self.c,command_id='next',idempotency_key='next')
            calls=[]
            self.assertEqual(self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=False),c=c,runtime=new).reason_code,
                             'NATIVE_RESOURCE_QUARANTINED')
            self.assertEqual(calls,[])
            released.set(); self.assertTrue(finished.wait(1))
            self.assertIsNotNone(new._read('quarantine','desktop'))
            with self.assertRaises(ValueError):
                new.reconcile_quarantine(self.c.command_id,health=health(self.c),evidence=evidence('HEALTH'),native_probe=lambda:{})
            new.reconcile_quarantine(self.c.command_id,health=health(self.c),evidence=evidence('HEALTH'),
                native_probe=lambda:dict(command_id=self.c.command_id,trusted=True,native_call_pending=False,process_quiescent=True))
            self.assertIsNone(new._read('quarantine','desktop'))
            # Old idempotency remains UNKNOWN; recovery never pastes again.
            self.assertEqual(self.run_command(lambda c,g:self.fail('replayed'),runtime=new).status,ExecutionStatus.OUTCOME_UNKNOWN)
        finally:released.set();finished.wait(1)

    def test_generation_wait_releases_desktop_preserves_session_ownership(self):
        seen=[]
        def handler(c,g):
            g.call(lambda:None,read_only=False)
            g.wait(.01)
            with resource_lock(self.runtime.desktop_lock,timeout=.01):seen.append('free')
            self.assertEqual(self.runtime._read('session',c.session_id)['task_id'],c.task_id)
            return g.verified(evidence())
        self.assertEqual(self.run_command(handler).status,ExecutionStatus.SUCCEEDED_VERIFIED)
        self.assertEqual(seen,['free'])
        c=replace(self.c,task_id='other-task',command_id='other',idempotency_key='other')
        self.assertEqual(self.run_command(lambda c,g:self.fail('wrong task'),c=c).reason_code,'SESSION_ALREADY_BOUND')

    def test_existing_desktop_lock_blocks_native_input(self):
        calls=[]
        with resource_lock(self.runtime.desktop_lock):
            result=self.run_command(lambda c,g:g.call(lambda:calls.append(1),read_only=False))
        self.assertFalse(calls)
        self.assertEqual(result.reason_code,'BOUNDED_WAIT_EXPIRED')

    def test_tool_success_or_wrong_evidence_never_business_success(self):
        for tag,handler in [('raw',lambda c,g:g.call(lambda:True,read_only=False)),
                ('health',lambda c,g:(g.call(lambda:True,read_only=False),g.verified(evidence('HEALTH')))[1])]:
            c=replace(self.c,command_id=tag,idempotency_key=tag)
            self.assertNotEqual(self.run_command(handler,c=c).status,ExecutionStatus.SUCCEEDED_VERIFIED)
            self.runtime.reconcile_quarantine(c.command_id,health=health(c),evidence=evidence('HEALTH'),
                native_probe=lambda:dict(command_id=c.command_id,trusted=True,native_call_pending=False,process_quiescent=True))

    def test_exception_and_state_logs_never_record_private_content(self):
        def failed():raise RuntimeError('cookie=SECRET token=SECRET QR unrelatedchat')
        result=self.run_command(lambda c,g:g.call(failed,read_only=False))
        self.assertEqual(result.status,ExecutionStatus.OUTCOME_UNKNOWN)
        for p in self.runtime.state_root.glob('*.json'):
            raw=p.read_text(encoding='utf-8')
            for private in ('SECRET','cookie','token','QR','unrelatedchat'):
                self.assertNotIn(private,raw)


if __name__ == '__main__': unittest.main()
