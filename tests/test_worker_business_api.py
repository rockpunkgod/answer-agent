"""Actual loopback HTTP and default authority tests; all business data anonymous."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from helpdesk.demo_server import DemoHTTPServer
from helpdesk.storage import Store, encode
from helpdesk.worker_client import HTTPBusinessClient, WorkerClient
from helpdesk.worker_contracts import Action, WorkerPolicy, WorkerResult, utc_now
from helpdesk.worker_coordinator import WorkerCoordinator
from helpdesk.worker_runtime import WorkerRuntime
from tests.test_worker_contracts import command, approval, health
from tests import test_shared_source_delivery_integration as shared_fixture


class WorkerDefaultAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared_fixture.SharedSourceDeliveryIntegrationTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.generate()
        self.db=self.fx.db
        r=self.fx.row
        self.c=command(action=Action.STAGE_OUTBOX,task_id=r['turn_id'],outbox_id=r['id'],
            platform='wecom',target_scope=r['binding_id'],profile_id=None,session_id=None,
            parameters={'content_ref':r['id'],'body_sha256':sha256(r['body'].encode()).hexdigest()},
            question_version=r['question_version'],context_revision=r['context_revision'],execution_epoch=0)
        self.policy=WorkerPolicy(mode='ASSISTED',allowed_actions=[Action.STAGE_OUTBOX,Action.VERIFY_OUTBOX,Action.EXECUTE_APPROVED_OUTBOX],
            allowed_accounts=[self.c.account_id],allowed_scopes=[self.c.target_scope,'foreign-scope'])
        self.server=WorkerCoordinator(self.db)
        self.server.configure_worker('worker-1',self.policy)

    def lease(self):
        self.server.issue(self.c,approval(self.c))
        self.server.heartbeat(health(self.c))
        return self.server.pull('worker-1')

    def result(self,kind='DRAFT'):
        observed=utc_now().isoformat()
        return WorkerResult(1,self.c.command_id,self.c.task_id,self.c.outbox_id,self.c.worker_id,
            self.c.account_id,0,self.c.question_version,self.c.context_revision,'SUCCEEDED_VERIFIED','VERIFIED',
            True,False,'ANONYMOUS_DRAFT','OK',observed,
            [dict(evidence_ref='anonymous-native-proof',sha256='d'*64,kind=kind,observed_at=observed)])

    def test_existing_uuid_task_outbox_and_content_are_the_authority(self):
        package=self.lease()
        self.assertIsInstance(self.c.question_version,str)
        self.assertEqual(package['command']['outbox_id'],self.fx.row['id'])
        resource=self.server.resource('worker-1',self.c.command_id,package['lease_id'],self.c.outbox_id)
        import base64
        self.assertEqual(base64.b64decode(resource['content']).decode(),self.fx.row['body'])
        self.assertEqual(resource['sha256'],self.c.parameters['body_sha256'])
        for changed in (replace(self.c,target_scope='foreign-scope'),replace(self.c,task_id='different-turn'),
                        replace(self.c,parameters=dict(self.c.parameters,body_sha256='e'*64))):
            with self.assertRaises(ValueError):self.server.issue(changed,approval(changed))

    def test_production_default_no_verifier_never_marks_delivery_or_counts(self):
        package=self.lease()
        self.server.authorize('worker-1',self.c.command_id,package['lease_id'],0)
        result=self.result()
        outcome=self.server.result('worker-1',package['lease_id'],result)
        self.assertEqual(outcome['status'],'OUTCOME_UNKNOWN')
        self.assertEqual(self.db.one('SELECT state FROM outbox WHERE id=?',(self.c.outbox_id,))[0],'PENDING')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0],0)
        again=self.server.result('worker-1',package['lease_id'],result)
        self.assertEqual(again['status'],'OUTCOME_UNKNOWN')
        self.assertTrue(again['duplicate'])

    def test_changed_original_version_revokes_command_and_old_approval(self):
        self.lease()
        q=self.db.one('SELECT question_id FROM turns WHERE id=?',(self.c.task_id,))[0]
        self.db.execute('UPDATE questions SET context_revision=context_revision+1 WHERE id=?',(q,))
        self.assertFalse(self.server.command('worker-1',self.c.command_id)['current'])
        with self.assertRaises(ValueError):self.server.issue(replace(self.c,command_id='new-cmd',idempotency_key='new-once'),approval(self.c))

    def test_manual_checkbox_without_real_proof_is_rejected(self):
        self.lease()
        with self.assertRaises(ValueError):self.server.mark_manually_handled(self.c.command_id,'invented-proof')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)

    def test_actual_teacher_fixture_proof_import_is_bound_and_idempotent(self):
        # This test uses an explicitly injected anonymous native evidence reader,
        # not the production default or a statement supplied in HTTP payload.
        self.lease()
        self.fx.flow.stage_manual_answer(self.c.outbox_id,adapter=self.fx.adapter)
        evidence=self.fx.adapter.readback_outbox(self.db,self.c.outbox_id)
        evidence.update({k:self.fx.row[k] for k in ('question_version','context_revision','message_id','turn_id','run_id','answer_id')})
        self.server.manual_evidence_reader=lambda c,ref:dict(verified=True,evidence_ref=ref,delivery_evidence=evidence)
        self.assertEqual(self.server.mark_manually_handled(self.c.command_id,'bound-proof')['counting_status'],'CONFIRMED')
        before={t:self.db.one('SELECT COUNT(*) FROM '+t)[0] for t in ('outbox','delivery_checks','performance_events')}
        self.server.mark_manually_handled(self.c.command_id,'bound-proof')
        self.assertEqual(before,{t:self.db.one('SELECT COUNT(*) FROM '+t)[0] for t in before})
        self.assertEqual(self.db.one('SELECT confirmed_quantity FROM performance_units')[0],1)
        self.assertIsNone(self.server.pull('worker-1')['command'])

    def test_foreign_teacher_proof_cannot_change_delivery(self):
        self.lease()
        evidence=self.fx.adapter.readback_outbox(self.db,self.c.outbox_id)
        evidence.update({k:self.fx.row[k] for k in ('question_version','context_revision','message_id','turn_id','run_id','answer_id')})
        evidence['group_key']='foreign-group'
        self.server.manual_evidence_reader=lambda c,ref:dict(verified=True,evidence_ref=ref,delivery_evidence=evidence)
        with self.assertRaises(ValueError):self.server.mark_manually_handled(self.c.command_id,'foreign-proof')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)


class WorkerHTTPBusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='worker-http-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.c=command(action=Action.READ_MESSAGES,platform='wecom',profile_id=None,session_id=None,
            parameters={'cursor_ref':'approved-cursor','limit':20},question_version=0,context_revision=0,execution_epoch=0)
        policy=WorkerPolicy(allowed_actions=[Action.READ_MESSAGES],allowed_accounts=[self.c.account_id],allowed_scopes=[self.c.target_scope])
        self.policy_file=self.root/'policy.json'
        self.policy_file.write_text(encode({'worker_id':'worker-1','policy':policy.to_dict()}),encoding='utf-8')
        env=patch.dict('os.environ',{'HELPDESK_WORKER_TOKEN':'ANONYMOUS_TEST_TOKEN_DO_NOT_EXPORT'})
        env.start();self.addCleanup(env.stop)
        self.http=DemoHTTPServer(('127.0.0.1',0),self.root/'server-local.db',worker_boundary=True,worker_policy=self.policy_file)
        self.thread=Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.api=HTTPBusinessClient(self.base)
        store=Store(self.root/'server-local.db')
        try:WorkerCoordinator(store).issue(self.c,approval(self.c))
        finally:store.close()

    def stop(self):
        self.http.shutdown();self.http.server_close();self.thread.join(2)

    def request(self,path,value=None,*,bearer=False,csrf=False):
        headers={}
        if bearer:headers['Authorization']='Bearer ANONYMOUS_TEST_TOKEN_DO_NOT_EXPORT'
        if csrf:
            headers.update({'Origin':self.base,'X-CSRF-Token':self.http.csrf_token})
        data=None if value is None else json.dumps(value).encode()
        if data:headers['Content-Type']='application/json'
        try:
            with urlopen(Request(self.base+path,data=data,headers=headers),timeout=3) as response:
                return response.status,json.loads(response.read())
        except HTTPError as exc:return exc.code,json.loads(exc.read())

    def test_real_loopback_client_runtime_result_requires_server_verification(self):
        runtime=WorkerRuntime(self.root/'worker-local')
        calls=[]
        def handler(c,g):
            g.call(lambda:calls.append(c.command_id),read_only=True)
            return g.verified([dict(evidence_ref='anonymous-capture',sha256='c'*64,kind='MESSAGE_CAPTURE',observed_at=utc_now().isoformat())])
        client=WorkerClient(self.api,'worker-1',self.root/'worker-local'/'cache.db',runtime=runtime,health_provider=lambda:health(self.c),handler=handler)
        self.addCleanup(client.close)
        self.assertEqual(client.run_once()['state'],'PENDING_RECOVERY')
        self.assertEqual(client.run_once()['state'],'WAITING_HUMAN_OR_PAUSED')
        self.assertEqual(calls,['cmd-1'])
        self.assertEqual(self.api.command('worker-1','cmd-1')['status'],'OUTCOME_UNKNOWN')
        code,state=self.request('/api/worker/control')
        self.assertEqual(code,200)
        self.assertEqual(state['mode'],'OBSERVE_ONLY')
        self.assertNotIn('ANONYMOUS_TEST_TOKEN',json.dumps(state))

    def test_human_takeover_blocks_even_health_observation_before_pull(self):
        self.api.heartbeat(health(self.c))
        for action in ('request_takeover','confirm_takeover'):
            self.assertEqual(self.request('/api/worker/control',{'action':action,'account_id':'account-1'},csrf=True)[0],200)
        def forbidden():raise AssertionError('Human owns the desktop: no screenshot or native health observation')
        client=WorkerClient(self.api,'worker-1',self.root/'worker-local'/'cache.db',runtime=WorkerRuntime(self.root/'worker-local'),
            health_provider=forbidden,handler=lambda c,g:None)
        self.addCleanup(client.close)
        self.assertEqual(client.run_once()['state'],'WAITING_HUMAN_OR_PAUSED')

    def test_http_rejects_free_commands_wrong_node_tokens_and_csrf(self):
        self.assertEqual(self.request('/api/worker/pull',{'worker_id':'worker-1'})[0],403)
        self.assertEqual(self.request('/api/worker/pull',{'worker_id':'other-worker'},bearer=True)[0],409)
        self.assertEqual(self.request('/api/worker/execute',{'worker_id':'worker-1','shell':'anything'},bearer=True)[0],404)
        self.assertEqual(self.request('/api/worker/control',{'action':'stop','account_id':'account-1'})[0],403)
        self.assertEqual(self.request('/api/worker/control',{'action':'stop','account_id':'account-1'},csrf=True)[0],200)
        self.api.heartbeat(health(self.c))
        self.assertIsNone(self.api.pull('worker-1')['command'])


if __name__=='__main__':unittest.main()
