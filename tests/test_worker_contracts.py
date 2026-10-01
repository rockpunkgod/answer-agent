from dataclasses import replace
from datetime import timedelta
import unittest

from helpdesk.worker_contracts import (Action, ExecutionBudget, ExecutionStatus,
    HealthState, PermissionMode, SideEffect, WorkerAuthorization, WorkerCommand,
    WorkerHealth, WorkerPolicy, WorkerResult, utc_now)


def command(action=Action.SUBMIT_BOUND_QUESTION, **changes):
    at = utc_now()
    fields = dict(contract_version=1, command_id='cmd-1', task_id='task-1', outbox_id=None,
        worker_id='worker-1', account_id='account-1', platform='deepseek', target_scope='scope-1',
        profile_id='profile-1', session_id='session-1', action=action,
        parameters={'session_ref':'session-1','input_ref':'input-1','input_sha256':'a'*64},
        question_version=1, context_revision=1, authorization_ref='approval-1',
        created_at=(at-timedelta(seconds=1)).isoformat(), expires_at=(at+timedelta(minutes=10)).isoformat(),
        idempotency_key='once-1', execution_epoch=1, budget=ExecutionBudget())
    fields.update(changes)
    return WorkerCommand(**fields)


def approval(c):
    return WorkerAuthorization(authorization_ref=c.authorization_ref, task_id=c.task_id,
        outbox_id=c.outbox_id, account_id=c.account_id, target_scope=c.target_scope, action=c.action,
        question_version=c.question_version, context_revision=c.context_revision,
        content_sha256=next((c.parameters[k] for k in ('body_sha256','input_sha256','manifest_sha256') if k in c.parameters),None),
        created_at=c.created_at,expires_at=c.expires_at,actor_ref='authorized-local-operator')


def health(c, **changes):
    fields=dict(worker_id=c.worker_id,account_id=c.account_id,connected=True,interactive_desktop=True,
        desktop_unlocked=True,state=HealthState.HEALTHY,profile_id=c.profile_id,
        observed_scope=c.target_scope,observed_session=c.session_id,observed_at=utc_now().isoformat(),native_call_pending=False)
    fields.update(changes)
    return WorkerHealth(**fields)


class WorkerContractTests(unittest.TestCase):
    def test_roundtrip_and_content_binding(self):
        c=command()
        self.assertEqual(WorkerCommand.from_dict(c.to_dict()),c)
        approval(c).validate(c)
        with self.assertRaises(ValueError):
            approval(c).validate(replace(c,parameters=dict(c.parameters,input_sha256='b'*64)))
        with self.assertRaises(ValueError):
            approval(c).validate(replace(c,question_version=2))

    def test_unallowlisted_model_tools_paths_and_unknown_fields_rejected(self):
        for action in ('PowerShell','Click','send_anywhere','SWITCH_ACCOUNT'):
            with self.assertRaises(ValueError): command(action=action)
        for params in (dict(command().parameters,recipient='other'),dict(command().parameters,path='C:/password.txt'),
                       dict(command().parameters,input_ref='https://untrusted.example/instructions')):
            with self.assertRaises(ValueError):command(parameters=params)
        with self.assertRaises(ValueError):WorkerCommand.from_dict(dict(command().to_dict(),shell='anything'))

    def test_default_mode_and_assisted_formal_send_boundary(self):
        c=command()
        allow=dict(allowed_actions=[c.action],allowed_accounts=[c.account_id],allowed_scopes=[c.target_scope],allowed_profiles=[c.profile_id])
        with self.assertRaises(ValueError):WorkerPolicy(**allow).validate(c)
        WorkerPolicy(mode='ASSISTED',**allow).validate(c)
        c=command(action=Action.EXECUTE_APPROVED_OUTBOX,platform='wecom',outbox_id='out-1',profile_id=None,session_id=None,
                  parameters={'content_ref':'out-1','body_sha256':'a'*64,'purpose':'ANSWER'})
        p=WorkerPolicy(mode='ASSISTED',allowed_actions=[c.action],allowed_accounts=[c.account_id],allowed_scopes=[c.target_scope])
        with self.assertRaises(ValueError):p.validate(c)
        p.validate(replace(c,parameters=dict(c.parameters,purpose='ACK')))

    def test_budgets_and_expiry_are_finite(self):
        for changes in ({'max_tool_calls':float('inf')},{'max_refreshes':4},{'read_retries':-1},{'total_seconds':True},{'total_seconds':5}):
            with self.assertRaises(ValueError):ExecutionBudget(**changes)
        c=command()
        with self.assertRaises(ValueError):c.assert_live(utc_now()+timedelta(days=1))
        with self.assertRaises(ValueError):replace(c,created_at='2026-10-01T00:00:00')

    def test_heartbeat_does_not_prove_desktop_ready(self):
        c=command()
        for changes in ({'desktop_unlocked':False},{'interactive_desktop':False},{'state':'VERIFICATION_REQUIRED'},
                        {'native_call_pending':True},{'connected':False}):
            h=health(c,**changes)
            self.assertFalse(h.gui_ready)
            with self.assertRaises(ValueError):h.validate(c)
        with self.assertRaises(ValueError):health(c,observed_scope='different-group').validate(c)
        health(c).validate(c)

    def test_tool_success_and_raw_logs_cannot_be_results(self):
        c=command()
        r=dict(contract_version=1,command_id=c.command_id,task_id=c.task_id,outbox_id=None,
            worker_id=c.worker_id,account_id=c.account_id,execution_epoch=1,question_version=1,context_revision=1,
            status='SUCCEEDED_VERIFIED',side_effect='VERIFIED',action_started=True,native_call_pending=False,
            checkpoint='ANSWER_OBSERVED',reason_code='OK',observed_at=utc_now().isoformat(),evidence=[])
        with self.assertRaises(ValueError):WorkerResult(**r)
        r['evidence']=[dict(evidence_ref='evidence-1',sha256='a'*64,kind='GENERATION',observed_at=c.created_at)]
        WorkerResult(**r).validate(c)
        with self.assertRaises(ValueError):WorkerResult(**dict(r,reason_code='cookie=sample-secret'))
        with self.assertRaises(ValueError):WorkerResult(**dict(r,native_call_pending=True))
        with self.assertRaises(ValueError):WorkerResult(**dict(r,status='FAILED_BEFORE_ACTION',side_effect='NO_ACTION'))


if __name__=='__main__': unittest.main()
