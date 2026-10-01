"""23 required reliability cases. Anonymous fixtures; never a real GUI/account."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest

from helpdesk.locking import resource_lock
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports
from helpdesk.storage import Store
from helpdesk.worker_client import WorkerClient
from helpdesk.worker_contracts import (Action, ExecutionBudget, HealthState,
    PermissionMode, WorkerCommand, WorkerControl, WorkerPolicy, utc_now)
from helpdesk.worker_coordinator import WorkerCoordinator
from helpdesk.worker_environment import classify_health, minimal_state
from helpdesk.worker_runtime import WorkerRuntime
from tests.test_worker_contracts import command, approval, health


class LocalBusinessAPI:
    def __init__(self, coordinator):
        self.coordinator=coordinator
        self.online=True
        self.lose_ack=False
        self.pulls=0

    def check(self):
        if not self.online:raise ConnectionError('anonymous simulated disconnect')

    def heartbeat(self,h):self.check();return self.coordinator.heartbeat(h)
    def pull(self,w):self.check();self.pulls+=1;return self.coordinator.pull(w)
    def command(self,w,c):self.check();return self.coordinator.command(w,c)
    def authorize(self,*args):self.check();return self.coordinator.authorize(*args)
    def result(self,*args):
        self.check()
        result=self.coordinator.result(*args)
        if self.lose_ack:raise ConnectionError('anonymous lost response after commit')
        return result


class WorkerReliabilityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='worker-acceptance-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=Store(self.root/'server-local.db')
        self.addCleanup(self.db.close)
        self.current=True
        self.coordinator=WorkerCoordinator(self.db,task_validator=lambda c:self.current,
            evidence_verifier=lambda c,r:{'verified':True})  # Deliberately anonymous evidence verifier.
        self.policy=WorkerPolicy(mode='ASSISTED',allowed_actions=list(Action),
            allowed_accounts=['account-1','account-2'],allowed_scopes=['scope-1','scope-2'],allowed_profiles=['profile-1'])
        self.coordinator.configure_worker('worker-1',self.policy)
        self.c=command(execution_epoch=0)
        self.h=health(self.c)
        self.runtime=WorkerRuntime(self.root/'worker-local')
        self.runtime.profiles.register('profile-1','account-1',self.runtime.profiles.root/'automation')
        self.api=LocalBusinessAPI(self.coordinator)
        self.calls=[]
        self.clients=[]
        self.addCleanup(lambda:[c.close() for c in self.clients])

    def queue(self,c=None):
        c=c or self.c
        self.coordinator.issue(c,approval(c))
        self.coordinator.heartbeat(health(c))
        return c

    def evidence(self,kind='GENERATION'):
        return [dict(evidence_ref='anonymous-evidence',sha256='b'*64,kind=kind,observed_at=utc_now().isoformat())]

    def handler(self,c,g):
        g.call(lambda:self.calls.append(c.command_id),read_only=c.action in {Action.READ_MESSAGES,Action.CHECK_SESSION,Action.VERIFY_OUTBOX,Action.READ_BOUND_GENERATION})
        kind={Action.READ_MESSAGES:'MESSAGE_CAPTURE',Action.CHECK_SESSION:'SESSION',Action.STAGE_OUTBOX:'DRAFT',
            Action.VERIFY_OUTBOX:'DELIVERY',Action.EXECUTE_APPROVED_OUTBOX:'DELIVERY'}.get(c.action,'GENERATION')
        return g.verified(self.evidence(kind))

    def client(self,handler=None):
        client=WorkerClient(self.api,'worker-1',self.root/'worker-local'/'journal.db',runtime=self.runtime,
            health_provider=lambda:self.h,handler=handler or self.handler)
        self.clients.append(client)
        return client

    def run_direct(self,c=None,handler=None,control=None):
        c=c or self.c
        self.queue(c)
        package=self.coordinator.pull(c.worker_id)
        if not package['command']:return None
        return self.runtime.execute(c,approval(c),self.policy,
            control_provider=control or (lambda:WorkerControl.from_dict(self.coordinator.authorize(c.worker_id,c.command_id,package['lease_id'],c.execution_epoch)['control'])),
            health_provider=lambda:self.h,handler=handler or self.handler)

    def test_01_login_expired_pauses_without_login_retry(self):
        self.queue()
        self.h=health(self.c,state='LOGIN_REQUIRED')
        self.assertEqual(self.client().run_once()['state'],'WAITING_HEALTH')
        self.assertEqual(self.coordinator.snapshot()['accounts'][0]['pause_reason'],'LOGIN_REQUIRED')
        self.assertEqual(self.api.pulls,0)
        self.assertEqual(self.calls,[])

    def test_02_verification_is_a_distinct_human_state(self):
        observation=dict(trusted_controls=True,connected=True,interactive_desktop=True,desktop_unlocked=True,
            login_control=False,verification_control=True,rate_limit_control=False,access_denied_control=False,expected_page_controls=True)
        self.assertEqual(classify_health(observation),HealthState.VERIFICATION_REQUIRED)
        self.queue()
        self.coordinator.heartbeat(health(self.c,state='VERIFICATION_REQUIRED'))
        self.assertIsNone(self.coordinator.pull('worker-1')['command'])

    def test_03_verification_pauses_other_tasks_using_same_account(self):
        self.queue()
        other=replace(self.c,command_id='cmd-2',idempotency_key='once-2',task_id='task-2',session_id='session-2',
            parameters=dict(self.c.parameters,session_ref='session-2'))
        self.coordinator.issue(other,approval(other))
        self.coordinator.heartbeat(health(self.c,state='VERIFICATION_REQUIRED'))
        self.assertIsNone(self.coordinator.pull('worker-1')['command'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM worker_commands')[0],2)

    def test_04_account_pause_does_not_block_backend_performance_or_history(self):
        self.queue()
        self.coordinator.heartbeat(health(self.c,state='VERIFICATION_REQUIRED'))
        report=PerformanceReports(self.db,PerformanceLedger(self.db)).build('2026-09-30')
        self.assertEqual(report['summary']['night_articles'],0)
        self.assertFalse(report.get('coverage',{}).get('complete',False))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM messages')[0],0)

    def test_05_human_owned_blocks_all_native_and_clipboard_actions(self):
        self.queue()
        self.coordinator.request_takeover('account-1')
        self.assertEqual(self.coordinator.confirm_takeover('account-1')['takeover'],'OWNED')
        result=self.runtime.execute(self.c,approval(self.c),self.policy,
            control_provider=lambda:WorkerControl(takeover='OWNED',takeover_epoch=1,lease_live=True,version_current=True,authorization_current=True),
            health_provider=lambda:self.h,handler=self.handler)
        self.assertNotEqual(result.status,'SUCCEEDED_VERIFIED')
        self.assertEqual(self.calls,[])

    def test_06_changed_group_or_session_blocks_resume(self):
        self.queue()
        self.coordinator.request_takeover('account-1')
        self.coordinator.confirm_takeover('account-1')
        self.coordinator.request_resume('account-1')
        self.coordinator.heartbeat(health(self.c,observed_scope='scope-2'))
        with self.assertRaises(ValueError):self.coordinator.resume_account('account-1')
        self.assertEqual(self.coordinator.snapshot()['accounts'][0]['takeover'],'RESUME_CHECK')

    def test_07_verification_after_submit_never_resubmits(self):
        self.queue()
        def handler(c,g):
            def submit():
                self.calls.append(c.command_id)
                self.h=health(c,state='VERIFICATION_REQUIRED')
            g.call(submit,read_only=False)
            return g.verified(self.evidence())
        client=self.client(handler)
        client.run_once()
        self.assertEqual(client.journal.execute('SELECT status FROM execution_journal').fetchone()[0],'OUTCOME_UNKNOWN')
        client.run_once()
        self.assertEqual(self.calls,['cmd-1'])

    def test_08_page_change_or_budget_cannot_loop_refreshes(self):
        self.h=health(self.c,state='PAGE_CHANGED')
        result=self.runtime.execute(self.c,approval(self.c),self.policy,
            control_provider=lambda:WorkerControl(lease_live=True,version_current=True,authorization_current=True),
            health_provider=lambda:self.h,handler=self.handler)
        self.assertEqual(result.reason_code,'PAGE_CHANGED')
        self.assertEqual(self.calls,[])
        self.h=health(self.c)
        c=replace(self.c,command_id='cmd-refresh',idempotency_key='once-refresh')
        result=self.run_direct(c,lambda c,g:g.call(lambda:self.calls.append('refresh'),read_only=True,refresh=True))
        self.assertNotEqual(result.status,'SUCCEEDED_VERIFIED')
        self.assertNotIn('refresh',self.calls)

    def test_09_generation_releases_desktop_but_retains_session_occupancy(self):
        # An unrelated safe short operation can acquire the actual shared lock;
        # another task cannot borrow the still-bound generating session.
        control=lambda:WorkerControl(lease_live=True,version_current=True,authorization_current=True)
        during=[]
        def handler(c,g):
            g.call(lambda:self.calls.append('submitted'),read_only=False)
            with resource_lock(self.runtime.desktop_lock,timeout=.1):during.append('unrelated-short-task')
            g.wait(.01)
            return g.verified(self.evidence())
        result=self.runtime.execute(self.c,approval(self.c),self.policy,control_provider=control,health_provider=lambda:self.h,handler=handler)
        self.assertEqual(result.status,'SUCCEEDED_VERIFIED')
        other=replace(self.c,command_id='cmd-other',idempotency_key='once-other',task_id='task-other')
        blocked=self.runtime.execute(other,approval(other),self.policy,control_provider=control,health_provider=lambda:self.h,handler=self.handler)
        self.assertEqual(blocked.reason_code,'SESSION_ALREADY_BOUND')
        self.assertEqual(during,['unrelated-short-task'])
        self.assertEqual(self.calls,['submitted'])

    def test_10_disconnected_worker_cannot_start_new_write(self):
        self.queue()
        self.api.online=False
        self.assertEqual(self.client().run_once()['state'],'OFFLINE_OR_REJECTED')
        self.assertEqual(self.calls,[])

    def test_11_result_report_failure_reconnects_without_replaying(self):
        self.queue()
        client=self.client()
        self.api.lose_ack=True
        client.run_once()
        self.api.lose_ack=False
        client.run_once()
        client.run_once()
        self.assertEqual(self.calls,['cmd-1'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM worker_result_receipts')[0],1)

    def test_12_completed_task_is_not_executed_after_recovery(self):
        self.queue()
        client=self.client()
        client.run_once()
        for _ in range(3):client.run_once()
        self.assertEqual(self.calls,['cmd-1'])
        self.assertEqual(self.coordinator.command('worker-1','cmd-1')['status'],'SUCCEEDED_VERIFIED')

    def test_13_correction_during_takeover_invalidates_old_input(self):
        self.queue()
        self.coordinator.request_takeover('account-1')
        self.coordinator.confirm_takeover('account-1')
        self.coordinator.request_resume('account-1')
        self.current=False
        with self.assertRaises(ValueError):self.coordinator.resume_account('account-1')
        self.assertFalse(self.coordinator.command('worker-1','cmd-1')['current'])

    def test_14_old_approval_cannot_authorize_new_content(self):
        self.queue()
        changed=replace(self.c,command_id='cmd-new',idempotency_key='once-new',parameters=dict(self.c.parameters,input_sha256='c'*64))
        with self.assertRaises(ValueError):self.coordinator.issue(changed,approval(self.c))
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM worker_commands')[0],1)

    def test_15_online_but_locked_worker_is_not_gui_ready(self):
        self.queue()
        self.h=health(self.c,desktop_unlocked=False)
        self.client().run_once()
        snapshot=self.coordinator.snapshot()
        self.assertFalse(snapshot['workers'][0]['gui_ready'])
        self.assertEqual(snapshot['accounts'][0]['pause_reason'],'DESKTOP_UNAVAILABLE')
        self.assertEqual(self.calls,[])

    def test_16_duplicate_profile_control_is_explicitly_rejected(self):
        errors=[]
        with self.runtime.profiles.acquire('profile-1','account-1'):
            def compete():
                try:
                    with self.runtime.profiles.acquire('profile-1','account-1',timeout=.03):self.calls.append('bad')
                except TimeoutError:errors.append('PROFILE_BUSY')
            thread=threading.Thread(target=compete);thread.start();thread.join(1)
        self.assertEqual(errors,['PROFILE_BUSY'])
        self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):self.runtime.profiles.register('copy','account-2',self.runtime.profiles.root/'automation')

    def test_17_authentication_and_other_chat_content_never_enter_state_logs(self):
        secret='SECRET_COOKIE_TOKEN_QR_OTHER_CHAT'
        def handler(c,g):g.call(lambda:(_ for _ in ()).throw(RuntimeError(secret)),read_only=False)
        result=self.run_direct(handler=handler)
        self.assertEqual(result.status,'OUTCOME_UNKNOWN')
        text=''.join(p.read_text(encoding='utf-8') for p in self.runtime.state_root.glob('*.json'))
        self.assertNotIn(secret,text)
        self.assertNotIn(secret,json.dumps(minimal_state(self.h)))
        self.assertEqual(list(self.runtime.state_root.glob('*.png')),[])

    def test_18_stop_blocks_new_writes_and_does_not_report_interruption_success(self):
        self.queue()
        self.coordinator.set_stop(True)
        self.assertIsNone(self.coordinator.pull('worker-1')['command'])
        self.assertEqual(self.calls,[])
        self.coordinator.set_stop(False)
        stopped=False
        def control():return WorkerControl(stop=stopped,lease_live=True,version_current=True,authorization_current=True)
        def handler(c,g):
            def native():
                nonlocal stopped
                self.calls.append('issued');stopped=True
            g.call(native,read_only=False)
        result=self.run_direct(handler=handler,control=control)
        self.assertEqual(result.status,'OUTCOME_UNKNOWN')
        self.assertEqual(self.calls,['issued'])

    def test_19_model_unallowlisted_action_is_rejected_before_native(self):
        for action in ('POWERSHELL','SWITCH_ACCOUNT','SEND_FREE_TEXT','UPLOAD_FILE_PATH'):
            with self.assertRaises(ValueError):replace(self.c,action=action)
        with self.assertRaises(ValueError):replace(self.c,parameters=dict(self.c.parameters,recipient='other-chat'))
        self.assertEqual(self.calls,[])

    def test_20_failover_worker_cannot_replay_uncertain_account_actions(self):
        self.queue()
        lease=self.coordinator.pull('worker-1')
        self.coordinator.authorize('worker-1','cmd-1',lease['lease_id'],0)
        self.db.execute("UPDATE worker_commands SET lease_expires_at=? WHERE command_id='cmd-1'",((utc_now()-timedelta(seconds=1)).isoformat(),))
        self.coordinator.command('worker-1','cmd-1')
        self.coordinator.configure_worker('worker-2',self.policy)
        other=replace(self.c,worker_id='worker-2',command_id='cmd-standby',idempotency_key='standby-1')
        self.coordinator.issue(other,approval(other))
        self.coordinator.heartbeat(health(other))
        self.assertIsNone(self.coordinator.pull('worker-2')['command'])
        self.assertTrue(self.coordinator.snapshot()['accounts'][0]['quarantined'])
        with self.assertRaises(ValueError):self.coordinator.authorize('worker-2','cmd-1',lease['lease_id'],0)

    def _shared_delivery(self):
        from tests.test_shared_source_delivery_integration import SharedSourceDeliveryIntegrationTests
        fx=SharedSourceDeliveryIntegrationTests()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        fx.generate()
        fx.flow.stage_manual_answer(fx.row['id'],adapter=fx.adapter)
        return fx

    def test_21_verification_delay_preserves_original_question_and_actual_completion_time(self):
        fx=self._shared_delivery()
        evidence=fx.adapter.readback_outbox(fx.db,fx.row['id'])
        completed='2026-10-01T10:00:00+08:00'
        evidence['confirmed_at']=completed
        with fx.db.transaction():fx.flow._record_check(fx.row,evidence,simulated=False)
        fx.flow.verify_manual_delivery(fx.row['id'],adapter=fx.adapter)
        unit=fx.db.one('SELECT * FROM performance_units WHERE id=?',(fx.unit_id,))
        self.assertEqual(unit['category'],'NIGHT')
        self.assertEqual(fx.db.one('SELECT sent_at FROM outbox WHERE id=?',(fx.row['id'],))[0],completed)
        self.assertIn('23:10',unit['question_time'])

    def test_22_upload_draft_ack_and_retries_never_add_completed_performance(self):
        fx=self._shared_delivery()
        self.assertEqual(fx.db.one('SELECT confirmed_quantity FROM performance_units')[0],0)
        self.assertEqual(fx.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)
        self.assertGreater(fx.db.one("SELECT COUNT(*) FROM outbox WHERE purpose='ACK'")[0],0)
        self.assertEqual(fx.db.one('SELECT COUNT(*) FROM answers')[0],1)
        self.assertFalse(fx.adapter.stage_outbox(fx.db,fx.row['id'])['answer_sent'])
        self.assertEqual(fx.db.one('SELECT confirmed_quantity FROM performance_units')[0],0)

    def test_23_repeated_recovery_keeps_original_outbox_and_counting_idempotent(self):
        fx=self._shared_delivery()
        fx.flow.verify_manual_delivery(fx.row['id'],adapter=fx.adapter)
        before={t:fx.db.one('SELECT COUNT(*) FROM '+t)[0] for t in ('outbox','delivery_checks','performance_units','performance_events')}
        for _ in range(4):fx.flow.verify_manual_delivery(fx.row['id'],adapter=fx.adapter)
        self.assertEqual(before,{t:fx.db.one('SELECT COUNT(*) FROM '+t)[0] for t in before})
        self.assertEqual(fx.db.one('SELECT confirmed_quantity FROM performance_units')[0],1)

    def test_mock_pull_verification_pause_human_recheck_resume_full_chain(self):
        self.queue()
        def handler(c,g):
            def submit():
                self.calls.append('submitted-once')
                self.h=health(c,state='VERIFICATION_REQUIRED')
            g.call(submit,read_only=False)
            return g.verified(self.evidence())
        client=self.client(handler)
        client.run_once()
        self.assertEqual(self.coordinator.command('worker-1','cmd-1')['status'],'OUTCOME_UNKNOWN')
        self.coordinator.request_takeover('account-1')
        with self.assertRaises(ValueError):self.coordinator.confirm_takeover('account-1')
        # Explicit anonymous trusted native termination/result observation,
        # not a free browser checkbox or an assumption that the lease elapsed.
        self.coordinator.recovery_evidence_reader=lambda c:dict(verified=True,native_termination_verified=True,
            command_id=c.command_id,account_id=c.account_id,question_version=c.question_version,
            context_revision=c.context_revision,status='FAILED_AFTER_ACTION',observed_at=utc_now().isoformat())
        self.assertFalse(self.coordinator.verify('cmd-1')['replayed'])
        self.assertEqual(self.coordinator.confirm_takeover('account-1')['takeover'],'OWNED')
        self.h=health(self.c)
        self.coordinator.heartbeat(self.h)
        self.coordinator.request_resume('account-1')
        self.assertEqual(self.coordinator.resume_account('account-1')['takeover'],'AUTO_ACTIVE')
        client.run_once()
        self.assertEqual(self.calls,['submitted-once'])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM outbox')[0],0)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0],0)

    def test_locked_desktop_cannot_confirm_takeover_via_direct_server_method(self):
        self.queue()
        self.coordinator.heartbeat(health(self.c,desktop_unlocked=False))
        self.coordinator.request_takeover('account-1')
        with self.assertRaises(ValueError):self.coordinator.confirm_takeover('account-1')

    def test_human_recovery_stales_old_epoch_and_requires_new_explicit_command(self):
        self.queue()
        self.coordinator.request_takeover('account-1')
        self.coordinator.confirm_takeover('account-1')
        self.coordinator.request_resume('account-1')
        self.coordinator.resume_account('account-1')
        self.assertEqual(self.coordinator.command('worker-1','cmd-1')['status'],'STALE')
        self.assertIsNone(self.coordinator.pull('worker-1')['command'])
        self.assertFalse(self.coordinator.snapshot()['accounts'][0]['quarantined'])
        self.assertIsNone(self.coordinator.snapshot()['accounts'][0]['active_worker'])


if __name__=='__main__':unittest.main()
