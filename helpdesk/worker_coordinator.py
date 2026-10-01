"""Server-owned execution metadata in the existing local business database.

No desktop code runs here. Leases do not revoke an already issued GUI gesture;
expired writes and pending native calls quarantine the account. Public clients
can pull/report existing commands, never create arbitrary desktop commands.
"""
from dataclasses import replace
import base64
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4

from .storage import encode, now
from .worker_contracts import (Action, BROWSER_ACTIONS, ExecutionStatus, HealthState,
    OUTBOX_ACTIONS, READ_ACTIONS, SideEffect, TakeoverState, WorkerAuthorization,
    WorkerCommand, WorkerControl, WorkerHealth, WorkerPolicy, WorkerResult,
    WRITE_ACTIONS, instant, reference, utc_now)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS worker_control_meta(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO worker_control_meta VALUES(1);
CREATE TABLE IF NOT EXISTS worker_nodes(
 worker_id TEXT PRIMARY KEY, policy_json TEXT NOT NULL, health_json TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS worker_accounts(
 account_id TEXT PRIMARY KEY, stop INTEGER NOT NULL DEFAULT 0,
 takeover TEXT NOT NULL DEFAULT 'AUTO_ACTIVE', takeover_epoch INTEGER NOT NULL DEFAULT 0,
 pause_reason TEXT, quarantined INTEGER NOT NULL DEFAULT 0, active_worker TEXT);
CREATE TABLE IF NOT EXISTS worker_commands(
 command_id TEXT PRIMARY KEY, command_json TEXT NOT NULL, authorization_json TEXT NOT NULL,
 account_id TEXT NOT NULL, worker_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'NOT_STARTED', lease_id TEXT, lease_expires_at TEXT,
 action_started INTEGER NOT NULL DEFAULT 0, native_call_pending INTEGER NOT NULL DEFAULT 0,
 reason_code TEXT NOT NULL DEFAULT 'QUEUED', checkpoint TEXT NOT NULL DEFAULT 'NOT_STARTED',
 updated_at TEXT NOT NULL, UNIQUE(account_id,idempotency_key));
CREATE TABLE IF NOT EXISTS worker_result_receipts(
 result_hash TEXT PRIMARY KEY, command_id TEXT NOT NULL REFERENCES worker_commands(command_id),
 result_json TEXT NOT NULL, verified INTEGER NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS worker_session_occupancy(
 account_id TEXT NOT NULL, profile_id TEXT NOT NULL, session_id TEXT NOT NULL,
 task_id TEXT NOT NULL, command_id TEXT NOT NULL, PRIMARY KEY(account_id,profile_id,session_id));
CREATE TABLE IF NOT EXISTS worker_resources(
 resource_ref TEXT NOT NULL, command_id TEXT NOT NULL REFERENCES worker_commands(command_id),
 kind TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
 PRIMARY KEY(command_id,resource_ref));
'''


class WorkerCoordinator:
    def __init__(self, store, *, evidence_verifier=None, manual_evidence_reader=None, recovery_evidence_reader=None, task_validator=None, clock=utc_now):
        self.db, self.clock = store, clock
        self.evidence_verifier = evidence_verifier
        self.manual_evidence_reader = manual_evidence_reader
        self.recovery_evidence_reader = recovery_evidence_reader
        self.task_validator = task_validator
        if not self.db.one("SELECT name FROM sqlite_master WHERE name='worker_control_meta'"):
            self.db.connection.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
        if self.db.one('SELECT version FROM worker_control_meta')[0] != 1:
            raise ValueError('Unsupported execution metadata schema')

    def _account(self, account_id):
        reference(account_id)
        row = self.db.one('SELECT * FROM worker_accounts WHERE account_id=?', (account_id,))
        if not row:
            raise ValueError('Account is not registered')
        return row

    def _node(self, worker_id):
        reference(worker_id)
        row = self.db.one('SELECT * FROM worker_nodes WHERE worker_id=?', (worker_id,))
        if not row:
            raise ValueError('Worker is not registered')
        return row

    def _row(self, command_id):
        row = self.db.one('SELECT * FROM worker_commands WHERE command_id=?', (command_id,))
        if not row:
            raise ValueError('Existing command required')
        return row

    def _command(self, row):
        return WorkerCommand.from_dict(json.loads(row['command_json']))

    def _global_stop(self):
        row = self.db.one("SELECT value FROM settings WHERE key='stop_requested'")
        return bool(row and json.loads(row[0]))

    def configure_worker(self, worker_id, policy):
        """Trusted local configuration only; not a model/public HTTP operation."""
        reference(worker_id)
        policy = policy if isinstance(policy, WorkerPolicy) else WorkerPolicy.from_dict(policy)
        with self.db.transaction():
            self.db.execute('INSERT INTO worker_nodes(worker_id,policy_json) VALUES(?,?) '
                'ON CONFLICT(worker_id) DO UPDATE SET policy_json=excluded.policy_json', (worker_id,encode(policy.to_dict())))
            for account in policy.allowed_accounts:
                self.db.execute('INSERT OR IGNORE INTO worker_accounts(account_id) VALUES(?)', (account,))

    def _current(self, command):
        if self.task_validator is not None:
            return bool(self.task_validator(command))
        if command.action in OUTBOX_ACTIONS:
            row = self.db.one('SELECT * FROM outbox WHERE id=?', (command.outbox_id,))
            if (not row or row['simulated'] != 0 or row['binding_id'] != command.target_scope
                    or row['question_version'] != command.question_version or row['context_revision'] != command.context_revision
                    or sha256(row['body'].encode()).hexdigest() != command.parameters['body_sha256']
                    or row['turn_id'] != command.task_id):
                return False
            if command.action == Action.EXECUTE_APPROVED_OUTBOX and row['purpose'] != command.parameters['purpose']:
                return False
            if command.action == Action.VERIFY_OUTBOX:
                original = self.db.one('SELECT command_json FROM worker_commands WHERE command_id=?',
                                       (command.parameters['execution_command_id'],))
                if not original or json.loads(original[0])['outbox_id'] != command.outbox_id:
                    return False
                return True  # Actual old delivery may be observed, never resent.
            if row['state'] != 'PENDING':
                own = self.db.one('SELECT status FROM worker_commands WHERE command_id=?',(command.command_id,))
                if not (command.action==Action.EXECUTE_APPROVED_OUTBOX and row['state']=='SENDING' and own and own[0]=='RUNNING'):
                    return False
            from .workflow import Workflow
            flow = Workflow.__new__(Workflow)
            flow.db = self.db
            try:
                flow._validate(row, transport=False)
                if command.action == Action.STAGE_OUTBOX and flow._delivery_mode(row) != 'MANUAL':
                    return False
                if command.action == Action.EXECUTE_APPROVED_OUTBOX and flow._delivery_mode(row) != 'AUTO':
                    return False
            except (ValueError, KeyError):
                return False
            return True
        if command.action in BROWSER_ACTIONS:
            run = self.db.one('SELECT r.*,q.current_version,q.context_revision AS current_context '
                'FROM runs r JOIN questions q ON q.id=r.question_id WHERE r.id=?', (command.task_id,))
            if not run:
                return False
            return bool(run['question_version'] == command.question_version == run['current_version']
                and run['context_revision'] == command.context_revision == run['current_context']
                and run['session_id'] == command.session_id and run['state'] in ('RUNNING','GENERATED'))
        # Collection commands are issued by trusted configured collectors and
        # carry no question version. They cannot mutate delivery/performance.
        return command.question_version == 0 and command.context_revision == 0

    def issue(self, command, authorization):
        """Only server workflow code calls this; no arbitrary command POST."""
        command = command if isinstance(command, WorkerCommand) else WorkerCommand.from_dict(command)
        authorization = authorization if isinstance(authorization, WorkerAuthorization) else WorkerAuthorization.from_dict(authorization)
        policy = WorkerPolicy.from_dict(json.loads(self._node(command.worker_id)['policy_json']))
        policy.validate(command)
        authorization.validate(command, self.clock())
        self._account(command.account_id)
        if command.execution_epoch != self._account(command.account_id)['takeover_epoch']:
            raise ValueError('Execution epoch predates takeover/recovery')
        if not self._current(command):
            raise ValueError('Existing task/Outbox version or approved content is stale')
        with self.db.transaction():
            old = self.db.one('SELECT * FROM worker_commands WHERE account_id=? AND idempotency_key=?',
                              (command.account_id,command.idempotency_key))
            if old:
                if old['command_json'] != encode(command.to_dict()) or old['authorization_json'] != encode(authorization.to_dict()):
                    raise ValueError('Idempotency key cannot bind changed command')
                return self._command(old).to_dict()
            self.db.execute('INSERT INTO worker_commands(command_id,command_json,authorization_json,account_id,worker_id,idempotency_key,updated_at) '
                'VALUES(?,?,?,?,?,?,?)',(command.command_id,encode(command.to_dict()),encode(authorization.to_dict()),
                command.account_id,command.worker_id,command.idempotency_key,now()))
        return command.to_dict()

    def heartbeat(self, health):
        health = health if isinstance(health, WorkerHealth) else WorkerHealth.from_dict(health)
        node = self._node(health.worker_id)
        policy = WorkerPolicy.from_dict(json.loads(node['policy_json']))
        if health.account_id not in policy.allowed_accounts:
            raise ValueError('Heartbeat account outside registered scope')
        age = (self.clock()-instant(health.observed_at)).total_seconds()
        if not -5 <= age <= 30:
            raise ValueError('Stale heartbeat observation')
        with self.db.transaction():
            self.db.execute('UPDATE worker_nodes SET health_json=?,last_seen=? WHERE worker_id=?',
                            (encode(health.to_dict()),now(),health.worker_id))
            reason = health.state
            if not health.interactive_desktop or not health.desktop_unlocked:
                reason = HealthState.DESKTOP_UNAVAILABLE
            if reason != HealthState.HEALTHY or health.native_call_pending:
                self.db.execute('UPDATE worker_accounts SET pause_reason=?,quarantined=MAX(quarantined,?) WHERE account_id=?',
                    (str(reason if reason != HealthState.HEALTHY else HealthState.UNKNOWN),int(health.native_call_pending),health.account_id))
                if health.native_call_pending:
                    self.db.execute("UPDATE worker_commands SET status='OUTCOME_UNKNOWN',native_call_pending=1,reason_code='NATIVE_CALL_PENDING' "
                        "WHERE account_id=? AND status='RUNNING'",(health.account_id,))
        # A healthy heartbeat alone never resumes a paused account.
        return {'accepted':True,'gui_ready':health.gui_ready,'paused':bool(self._account(health.account_id)['pause_reason'])}

    def _expire(self):
        with self.db.transaction():
            rows = self.db.all("SELECT * FROM worker_commands WHERE lease_id IS NOT NULL AND status IN ('RUNNING','NOT_STARTED')")
            for row in rows:
                if instant(row['lease_expires_at']) > self.clock():
                    continue
                command = self._command(row)
                uncertain = command.action in WRITE_ACTIONS or row['action_started'] or row['native_call_pending']
                self.db.execute('UPDATE worker_commands SET status=?,reason_code=?,updated_at=? WHERE command_id=?',
                    ('OUTCOME_UNKNOWN' if uncertain else 'CANCELLED','LEASE_EXPIRED',now(),command.command_id))
                if uncertain:
                    self.db.execute("UPDATE worker_accounts SET quarantined=1,pause_reason='UNKNOWN' WHERE account_id=?",(command.account_id,))
                    self._unknown_outbox(command)

    def _unknown_outbox(self, command):
        if command.action == Action.EXECUTE_APPROVED_OUTBOX:
            self.db.execute("UPDATE outbox SET state='SEND_UNKNOWN',last_error='WORKER_OUTCOME_UNKNOWN' WHERE id=? AND state IN ('PENDING','SENDING')",
                            (command.outbox_id,))

    def _control(self, command, row, *, recovery=False):
        account = self._account(command.account_id)
        lease_live = bool(row['lease_id'] and instant(row['lease_expires_at']) > self.clock())
        auth_current = False
        try:
            WorkerAuthorization.from_dict(json.loads(row['authorization_json'])).validate(command,self.clock())
            WorkerPolicy.from_dict(json.loads(self._node(command.worker_id)['policy_json'])).validate(command)
            auth_current = True
        except ValueError:
            pass
        recovering = recovery and account['takeover'] == 'RESUME_CHECK' and command.action in {Action.CHECK_SESSION,Action.VERIFY_OUTBOX}
        return WorkerControl(stop=(self._global_stop() or bool(account['stop'])) and not recovering,
            takeover=account['takeover'],takeover_epoch=account['takeover_epoch'],
            paused_reason=None if recovering else account['pause_reason'],
            resources_quarantined=bool(account['quarantined']) and not recovering,
            lease_live=lease_live,version_current=self._current(command),authorization_current=auth_current,
            manually_handled=row['status']=='SUCCEEDED_VERIFIED')

    def pull(self, worker_id):
        self._node(worker_id)
        self._expire()
        if self._global_stop():
            return {'command':None}
        with self.db.transaction():
            rows=self.db.all("SELECT * FROM worker_commands WHERE worker_id=? AND status='NOT_STARTED' AND lease_id IS NULL ORDER BY updated_at,command_id",(worker_id,))
            for row in rows:
                c=self._command(row)
                a=self._account(c.account_id)
                if c.execution_epoch!=a['takeover_epoch']:
                    self.db.execute("UPDATE worker_commands SET status='STALE',reason_code='TAKEOVER_EPOCH_CHANGED' WHERE command_id=?",(c.command_id,))
                    continue
                if a['stop'] or a['takeover']!='AUTO_ACTIVE' or a['pause_reason'] or a['quarantined']:
                    continue
                try:
                    c.assert_live(self.clock())
                    p=WorkerPolicy.from_dict(json.loads(self._node(worker_id)['policy_json']))
                    p.validate(c)
                    auth=WorkerAuthorization.from_dict(json.loads(row['authorization_json']))
                    auth.validate(c,self.clock())
                    if not self._current(c):
                        raise ValueError('Stale task')
                    h=WorkerHealth.from_dict(json.loads(self._node(worker_id)['health_json'] or '{}'))
                    h.validate(c,self.clock())
                except ValueError:
                    self.db.execute("UPDATE worker_commands SET status='STALE',reason_code='PRECONDITION_CHANGED' WHERE command_id=?",(c.command_id,))
                    continue
                if c.action in WRITE_ACTIONS and a['active_worker'] not in (None,worker_id):
                    continue
                if c.session_id:
                    occupied=self.db.one('SELECT * FROM worker_session_occupancy WHERE account_id=? AND profile_id=? AND session_id=?',
                                         (c.account_id,c.profile_id,c.session_id))
                    if occupied and occupied['task_id'] != c.task_id:
                        continue
                    self.db.execute('INSERT OR IGNORE INTO worker_session_occupancy VALUES(?,?,?,?,?)',
                                    (c.account_id,c.profile_id,c.session_id,c.task_id,c.command_id))
                lease=uuid4().hex
                end=min(instant(c.expires_at), self.clock()+timedelta(seconds=c.budget.total_seconds))
                self.db.execute('UPDATE worker_commands SET lease_id=?,lease_expires_at=?,updated_at=? WHERE command_id=?',
                                (lease,end.isoformat(),now(),c.command_id))
                if c.action in WRITE_ACTIONS:
                    self.db.execute('UPDATE worker_accounts SET active_worker=? WHERE account_id=?',(worker_id,c.account_id))
                return {'command':c.to_dict(),'authorization':auth.to_dict(),'policy':p.to_dict(),'lease_id':lease}
        return {'command':None}

    def authorize(self, worker_id, command_id, lease_id, execution_epoch):
        self._expire()
        with self.db.transaction():
            row=self._row(command_id)
            c=self._command(row)
            if (worker_id!=c.worker_id or lease_id!=row['lease_id'] or execution_epoch!=c.execution_epoch):
                raise ValueError('Lease/worker/fencing identity mismatch')
            control=self._control(c,row,recovery=True)
            allowed_takeover = control.takeover=='AUTO_ACTIVE' or control.takeover=='RESUME_CHECK' and c.action in {Action.CHECK_SESSION,Action.VERIFY_OUTBOX}
            if (not control.stop and not control.resources_quarantined and not control.paused_reason and allowed_takeover
                    and control.lease_live and control.version_current and control.authorization_current
                    and row['status'] in ('NOT_STARTED','RUNNING')):
                h=WorkerHealth.from_dict(json.loads(self._node(worker_id)['health_json'] or '{}'))
                h.validate(c,self.clock())
                self.db.execute("UPDATE worker_commands SET status='RUNNING',action_started=1,checkpoint='AUTHORIZED',updated_at=? WHERE command_id=?",
                                (now(),command_id))
                if c.action==Action.EXECUTE_APPROVED_OUTBOX:
                    self.db.execute("UPDATE outbox SET state='SENDING' WHERE id=? AND state='PENDING'",(c.outbox_id,))
            else:
                control=replace(control,lease_live=False if row['status'] not in ('NOT_STARTED','RUNNING') else control.lease_live)
        return {'control':control.to_dict()}

    def command(self, worker_id, command_id):
        self._expire()
        row=self._row(command_id)
        c=self._command(row)
        if worker_id!=c.worker_id:
            raise ValueError('Command belongs to another worker')
        return {'status':row['status'],'current':self._current(c)}

    def worker_state(self,worker_id):
        """Metadata-only permission for health acquisition, never a GUI read."""
        p=WorkerPolicy.from_dict(json.loads(self._node(worker_id)['policy_json']))
        accounts=[self._account(a) for a in p.allowed_accounts]
        allowed=bool(accounts) and not self._global_stop() and all(
            not a['stop'] and not a['quarantined'] and a['takeover'] in {'AUTO_ACTIVE','RESUME_CHECK'} for a in accounts)
        return {'worker_id':worker_id,'desktop_reads_allowed':allowed,
                'recovery_only':any(a['takeover']=='RESUME_CHECK' for a in accounts)}

    def result(self, worker_id, lease_id, result):
        result=result if isinstance(result,WorkerResult) else WorkerResult.from_dict(result)
        row=self._row(result.command_id)
        c=self._command(row)
        result.validate(c)
        if worker_id!=c.worker_id or lease_id!=row['lease_id']:
            raise ValueError('Result does not belong to original lease')
        result_hash=sha256(encode(result.to_dict()).encode()).hexdigest()
        old=self.db.one('SELECT * FROM worker_result_receipts WHERE result_hash=?',(result_hash,))
        if old:
            if old['verified'] and c.action in {Action.VERIFY_OUTBOX,Action.EXECUTE_APPROVED_OUTBOX}:
                self._project_counting(c)
            return {'accepted':True,'command_id':c.command_id,'duplicate':True,'status':self._row(c.command_id)['status']}
        proof = None
        if result.status=='SUCCEEDED_VERIFIED' and self.evidence_verifier:
            proof=self.evidence_verifier(c,result)
        verified=isinstance(proof,dict) and proof.get('verified') is True
        status=result.status
        if status=='SUCCEEDED_VERIFIED' and not verified:
            status=ExecutionStatus.OUTCOME_UNKNOWN
        if c.action in WRITE_ACTIONS and result.action_started and not verified and result.side_effect!='NO_ACTION':
            status=ExecutionStatus.OUTCOME_UNKNOWN
        # Immutable original facts can be reported after stop/expiry; they
        # cannot release another command or silently make a new version ready.
        with self.db.transaction():
            latest=self._row(c.command_id)
            if latest['status']=='SUCCEEDED_VERIFIED' and status!='SUCCEEDED_VERIFIED':
                status=ExecutionStatus.SUCCEEDED_VERIFIED
            self.db.execute('INSERT INTO worker_result_receipts VALUES(?,?,?,?,?)',
                            (result_hash,c.command_id,encode(result.to_dict()),int(verified),now()))
            self.db.execute('UPDATE worker_commands SET status=?,action_started=?,native_call_pending=?,reason_code=?,checkpoint=?,updated_at=? WHERE command_id=?',
                (str(status),int(result.action_started),int(result.native_call_pending),result.reason_code if verified or result.status!='SUCCEEDED_VERIFIED' else 'SERVER_VERIFICATION_REQUIRED',
                 result.checkpoint,now(),c.command_id))
            if result.native_call_pending or status=='OUTCOME_UNKNOWN' or status=='WAITING_HUMAN' and result.action_started:
                self.db.execute("UPDATE worker_accounts SET quarantined=1,pause_reason=COALESCE(pause_reason,'UNKNOWN') WHERE account_id=?",(c.account_id,))
                self._unknown_outbox(c)
            if verified and c.action in {Action.VERIFY_OUTBOX,Action.EXECUTE_APPROVED_OUTBOX}:
                self._import_delivery(c,proof)
            if verified and c.action==Action.READ_BOUND_GENERATION:
                self.db.execute('DELETE FROM worker_session_occupancy WHERE account_id=? AND profile_id=? AND session_id=? AND task_id=?',
                                (c.account_id,c.profile_id,c.session_id,c.task_id))
        if verified and c.action in {Action.VERIFY_OUTBOX,Action.EXECUTE_APPROVED_OUTBOX}:
            self._project_counting(c)
        return {'accepted':True,'command_id':c.command_id,'duplicate':False,'status':str(status)}

    def _import_delivery(self, c, proof):
        # Verification implementation is server-owned. A remote result cannot
        # provide free recipient/body/timestamp to this method.
        evidence=proof.get('delivery_evidence')
        if not isinstance(evidence,dict) or evidence.get('confirmed') is not True or evidence.get('simulated') is not False:
            raise ValueError('Actual server-verified delivery evidence required')
        row=self.db.one('SELECT * FROM outbox WHERE id=?',(c.outbox_id,))
        if not row or evidence.get('outbox_id')!=row['id'] or evidence.get('binding_id')!=row['binding_id']:
            raise ValueError('Delivery evidence refers to another Outbox')
        if (evidence.get('body_hash')!=sha256(row['body'].encode()).hexdigest() or evidence.get('sender_role')!='TEACHER'
                or not evidence.get('message_locator') or not evidence.get('evidence_paths')):
            raise ValueError('Full original teacher message proof required')
        binding=self.db.one('SELECT group_key,student_key FROM bindings WHERE id=?',(row['binding_id'],))
        if not binding or evidence.get('group_key')!=binding[0] or evidence.get('student_key')!=binding[1]:
            raise ValueError('Original group/student identity proof required')
        completed=instant(evidence.get('confirmed_at'))
        if completed>self.clock()+timedelta(seconds=5):
            raise ValueError('Actual delivery time cannot be in the future')
        if any(evidence.get(key)!=row[key] for key in ('question_version','context_revision','message_id','turn_id','run_id','answer_id')):
            raise ValueError('Teacher delivery must retain original task and version binding')
        if row['state']=='SENT_UI_CONFIRMED':
            return
        from .workflow import Workflow
        flow=Workflow.__new__(Workflow)
        flow.db=self.db
        flow._record_check(row,evidence,simulated=False)
        # Counting is a separate resumable existing projection, never a Worker
        # quantity. It requires the original frozen shared semantic marker.

    def _project_counting(self,c):
        row=self.db.one('SELECT * FROM outbox WHERE id=?',(c.outbox_id,))
        if row and row['purpose'] in ('ANSWER','CORRECTION') and row['state']=='SENT_UI_CONFIRMED':
            from .workflow import Workflow
            flow=Workflow.__new__(Workflow)
            flow.db=self.db
            return flow._project_verified_manual_counting(row)
        return 'NOT_ANSWER_DELIVERY'

    def set_stop(self, stopped, account_id=None):
        if type(stopped) is not bool:
            raise ValueError('Stop boolean required')
        with self.db.transaction():
            if account_id:
                self._account(account_id)
                self.db.execute('UPDATE worker_accounts SET stop=? WHERE account_id=?',(int(stopped),account_id))
            else:
                self.db.execute("UPDATE settings SET value=? WHERE key='stop_requested'",(encode(stopped),))
        return {'stop':stopped}

    def request_takeover(self, account_id):
        self._account(account_id)
        with self.db.transaction():
            self.db.execute("UPDATE worker_accounts SET takeover='REQUESTED',takeover_epoch=takeover_epoch+1 WHERE account_id=? AND takeover='AUTO_ACTIVE'",(account_id,))
        return {'takeover':self._account(account_id)['takeover']}

    def confirm_takeover(self, account_id):
        a=self._account(account_id)
        if a['takeover'] not in {'REQUESTED','QUIESCING'}:
            raise ValueError('Request takeover first')
        busy=self.db.one("SELECT command_id FROM worker_commands WHERE account_id=? AND (status='RUNNING' OR native_call_pending=1)",(account_id,))
        if busy or a['quarantined']:
            with self.db.transaction():
                self.db.execute("UPDATE worker_accounts SET takeover='QUIESCING' WHERE account_id=?",(account_id,))
            raise ValueError('Native action may still run; takeover is not safe yet')
        # Login/verification can require human ownership, but a locked or
        # disconnected interactive desktop cannot be confirmed usable.
        nodes=self.db.all('SELECT health_json FROM worker_nodes WHERE health_json IS NOT NULL')
        available=False
        for node in nodes:
            h=WorkerHealth.from_dict(json.loads(node[0]))
            available=available or (h.account_id==account_id and h.connected and h.interactive_desktop and h.desktop_unlocked
                and not h.native_call_pending and 0<=(self.clock()-instant(h.observed_at)).total_seconds()<=30)
        if not available:
            raise ValueError('Fresh available interactive desktop observation required')
        # Leased but not authorized tasks cannot start after the request.
        with self.db.transaction():
            self.db.execute("UPDATE worker_accounts SET takeover='OWNED' WHERE account_id=?",(account_id,))
        return {'takeover':'OWNED','automation_quiescent':True}

    def request_resume(self, account_id):
        a=self._account(account_id)
        if a['takeover']!='OWNED':
            raise ValueError('Human takeover is not owned')
        with self.db.transaction():
            self.db.execute("UPDATE worker_accounts SET takeover='RESUME_CHECK' WHERE account_id=?",(account_id,))
        return {'takeover':'RESUME_CHECK','new_external_writes_allowed':False}

    def _resume_preconditions(self,account_id):
        a=self._account(account_id)
        if a['takeover']!='RESUME_CHECK' or a['quarantined']:
            raise ValueError('Read-only recovery/quarantine verification required')
        active=self.db.all("SELECT * FROM worker_commands WHERE account_id=? AND status NOT IN ('SUCCEEDED_VERIFIED','FAILED_BEFORE_ACTION','FAILED_AFTER_ACTION','STALE','CANCELLED')",(account_id,))
        for row in active:
            c=self._command(row)
            h=self.db.one('SELECT health_json FROM worker_nodes WHERE worker_id=?',(c.worker_id,))
            if row['status'] in {'RUNNING','OUTCOME_UNKNOWN','WAITING_HUMAN'}:
                raise ValueError('Original action outcome must be verified before resume')
            WorkerHealth.from_dict(json.loads(h[0] or '{}')).validate(c,self.clock())
            if not self._current(c):
                raise ValueError('Task version or original group/session changed')
            WorkerAuthorization.from_dict(json.loads(row['authorization_json'])).validate(c,self.clock())
        if not active:
            nodes=self.db.all('SELECT health_json FROM worker_nodes WHERE health_json IS NOT NULL')
            healthy=False
            for node in nodes:
                h=WorkerHealth.from_dict(json.loads(node[0]))
                healthy=healthy or h.account_id==account_id and h.gui_ready and 0 <= (self.clock()-instant(h.observed_at)).total_seconds()<=30
            if not healthy:
                raise ValueError('Fresh unlocked interactive account observation required')

    def resume_account(self, account_id):
        self._resume_preconditions(account_id)
        with self.db.transaction():
            self.db.execute("UPDATE worker_accounts SET takeover='AUTO_ACTIVE',pause_reason=NULL,stop=0 WHERE account_id=?",(account_id,))
            self.db.execute("UPDATE worker_commands SET status='STALE',reason_code='TAKEOVER_EPOCH_CHANGED' WHERE account_id=? AND status='NOT_STARTED' AND action_started=0",(account_id,))
            # Explicit human recovery may release a quiescent send-node owner;
            # a timeout or healthy heartbeat never does so automatically.
            self.db.execute('UPDATE worker_accounts SET active_worker=NULL WHERE account_id=?',(account_id,))
        return {'takeover':'AUTO_ACTIVE'}

    def register_resource(self,command_id,resource_ref,kind,content):
        """Trusted server planner publishes only explicit approved task bytes.

        Not an HTTP upload API and never scans a directory or accepts a worker
        path. Referenced files must already pass the existing source/course
        manifest checks before the planner calls this method.
        """
        c=self._command(self._row(command_id))
        reference(resource_ref)
        refs={v for k,v in c.parameters.items() if k.endswith('_ref')}
        refs.update(c.parameters.get('attachment_refs',()))
        if resource_ref not in refs or kind not in {'INPUT','ATTACHMENT','OUTBOX_CONTENT'}:
            raise ValueError('Only approved command resources may be registered')
        if not isinstance(content,bytes) or not 0<len(content)<=25_000_000:
            raise ValueError('Bounded resource bytes required')
        content_hash=sha256(content).hexdigest()
        if kind=='INPUT' and content_hash!=c.parameters.get('input_sha256'):
            raise ValueError('Frozen input hash changed')
        if kind=='OUTBOX_CONTENT' and content_hash!=c.parameters.get('body_sha256'):
            raise ValueError('Approved Outbox content changed')
        with self.db.transaction():
            old=self.db.one('SELECT sha256,kind FROM worker_resources WHERE command_id=? AND resource_ref=?',(command_id,resource_ref))
            if old and (old[0]!=content_hash or old[1]!=kind):
                raise ValueError('Published task resource is immutable')
            self.db.execute('INSERT OR IGNORE INTO worker_resources VALUES(?,?,?,?,?)',(resource_ref,command_id,kind,content_hash,content))

    def resource(self,worker_id,command_id,lease_id,resource_ref):
        row=self._row(command_id)
        c=self._command(row)
        if c.worker_id!=worker_id or row['lease_id']!=lease_id or not lease_id:
            raise ValueError('Resource belongs to another worker/lease')
        WorkerAuthorization.from_dict(json.loads(row['authorization_json'])).validate(c,self.clock())
        WorkerPolicy.from_dict(json.loads(self._node(worker_id)['policy_json'])).validate(c)
        if not self._current(c) or self._account(c.account_id)['takeover']!='AUTO_ACTIVE':
            raise ValueError('Task resource is stale or in human takeover')
        if c.outbox_id and resource_ref==c.parameters.get('content_ref'):
            original=self.db.one('SELECT body FROM outbox WHERE id=?',(c.outbox_id,))
            content=original[0].encode()
            kind='OUTBOX_CONTENT'
            content_hash=sha256(content).hexdigest()
            if content_hash!=c.parameters['body_sha256']:raise ValueError('Outbox content changed')
        else:
            value=self.db.one('SELECT * FROM worker_resources WHERE command_id=? AND resource_ref=?',(command_id,resource_ref))
            if not value:raise ValueError('Approved task resource not available')
            content,kind,content_hash=value['content'],value['kind'],value['sha256']
        return {'resource_ref':resource_ref,'kind':kind,'sha256':content_hash,'encoding':'base64',
                'content':base64.b64encode(content).decode('ascii')}

    def cancel(self, command_id):
        row=self._row(command_id)
        if row['action_started'] or row['status'] in {'RUNNING','OUTCOME_UNKNOWN','SUCCEEDED_VERIFIED'}:
            raise ValueError('Cancellation cannot undo an already issued action')
        with self.db.transaction():
            self.db.execute("UPDATE worker_commands SET status='CANCELLED',reason_code='OPERATOR_CANCELLED' WHERE command_id=?",(command_id,))
        return {'status':'CANCELLED'}

    def verify(self, command_id):
        row=self._row(command_id)
        c=self._command(row)
        if not row['lease_id']:
            raise ValueError('Existing leased observation required')
        if self.recovery_evidence_reader is not None:
            proof=self.recovery_evidence_reader(c)
            if (not isinstance(proof,dict) or proof.get('verified') is not True
                    or proof.get('native_termination_verified') is not True
                    or proof.get('command_id')!=c.command_id or proof.get('account_id')!=c.account_id
                    or proof.get('question_version')!=c.question_version or proof.get('context_revision')!=c.context_revision
                    or proof.get('status') not in {'SUCCEEDED_VERIFIED','FAILED_BEFORE_ACTION','FAILED_AFTER_ACTION'}):
                raise ValueError('Bound native termination and actual outcome proof required')
            observed=instant(proof.get('observed_at'))
            if not 0 <= (self.clock()-observed).total_seconds()<=30:
                raise ValueError('Fresh recovery evidence required')
            with self.db.transaction():
                if c.action in OUTBOX_ACTIONS and proof['status']=='SUCCEEDED_VERIFIED':
                    self._import_delivery(c,proof)
                self.db.execute('UPDATE worker_commands SET status=?,native_call_pending=0,checkpoint=?,reason_code=? WHERE command_id=?',
                    (proof['status'],'OUTCOME_RECONCILED','TRUSTED_READ_ONLY_VERIFICATION',command_id))
                self._clear_quarantine_if_reconciled(c.account_id)
            if c.action in OUTBOX_ACTIONS and proof['status']=='SUCCEEDED_VERIFIED':self._project_counting(c)
            return {'status':proof['status'],'replayed':False}
        # The workbench never replaces a trusted observer with a checkbox.
        return {'status':'WAITING_HUMAN','verification_required':True,'command_id':command_id,
                'reason_code':'BOUND_READ_ONLY_OBSERVER_REQUIRED'}

    def mark_manually_handled(self, command_id, evidence_ref):
        reference(evidence_ref)
        row=self._row(command_id)
        c=self._command(row)
        if c.action not in OUTBOX_ACTIONS or not self.manual_evidence_reader:
            raise ValueError('Trusted actual message evidence import required; a completion button is insufficient')
        proof=self.manual_evidence_reader(c,evidence_ref)
        if not isinstance(proof,dict) or proof.get('verified') is not True or proof.get('evidence_ref')!=evidence_ref:
            raise ValueError('Bound actual message proof required')
        if row['native_call_pending'] and proof.get('native_termination_verified') is not True:
            raise ValueError('Native action termination must be verified first')
        with self.db.transaction():
            self._import_delivery(c,proof)
            self.db.execute("UPDATE worker_commands SET status='SUCCEEDED_VERIFIED',native_call_pending=0,reason_code='HUMAN_DELIVERY_VERIFIED',checkpoint='ACTUAL_MESSAGE_LINKED' WHERE command_id=?",(command_id,))
            self._clear_quarantine_if_reconciled(c.account_id)
        return {'status':'SUCCEEDED_VERIFIED','counting_status':self._project_counting(c)}

    def _clear_quarantine_if_reconciled(self,account_id):
        if not self.db.one("SELECT command_id FROM worker_commands WHERE account_id=? AND (native_call_pending=1 OR status IN ('RUNNING','OUTCOME_UNKNOWN','WAITING_HUMAN'))",(account_id,)):
            self.db.execute('UPDATE worker_accounts SET quarantined=0 WHERE account_id=?',(account_id,))

    def snapshot(self):
        workers=[]
        modes=[]
        for n in self.db.all('SELECT * FROM worker_nodes ORDER BY worker_id'):
            p=WorkerPolicy.from_dict(json.loads(n['policy_json']))
            modes.append(str(p.mode))
            h=WorkerHealth.from_dict(json.loads(n['health_json'])) if n['health_json'] else None
            workers.append({'worker_id':n['worker_id'],'account_id':h.account_id if h else None,
                'health':h.to_dict() if h else None,'gui_ready':bool(h and h.gui_ready and 0 <= (self.clock()-instant(h.observed_at)).total_seconds()<=30)})
        accounts=[]
        for a in self.db.all('SELECT * FROM worker_accounts ORDER BY account_id'):
            actions=['stop']
            if a['takeover']=='AUTO_ACTIVE': actions.append('request_takeover')
            if a['takeover'] in ('REQUESTED','QUIESCING') and not a['quarantined'] and not self.db.one(
                    "SELECT command_id FROM worker_commands WHERE account_id=? AND (status='RUNNING' OR native_call_pending=1)",(a['account_id'],)):
                actions.append('confirm_takeover')
            if a['takeover']=='OWNED': actions.append('request_resume')
            if a['takeover']=='RESUME_CHECK' and not a['quarantined']:
                try:
                    self._resume_preconditions(a['account_id'])
                    actions.append('resume')
                except ValueError:pass
            accounts.append({'account_id':a['account_id'],'stop':bool(a['stop']) or self._global_stop(),
                'takeover':a['takeover'],'takeover_epoch':a['takeover_epoch'],'pause_reason':a['pause_reason'],
                'quarantined':bool(a['quarantined']),'active_worker':a['active_worker'],'available_actions':actions})
        commands=[]
        for row in self.db.all('SELECT * FROM worker_commands ORDER BY updated_at DESC LIMIT 100'):
            c=self._command(row)
            actions=['cancel'] if not row['action_started'] and row['status']=='NOT_STARTED' else []
            if row['lease_id'] and row['status'] in ('OUTCOME_UNKNOWN','WAITING_HUMAN','RUNNING'):
                actions.append('verify')
            commands.append({'command_id':c.command_id,'task_id':c.task_id,'outbox_id':c.outbox_id,
                'account_id':c.account_id,'action':str(c.action),'status':row['status'],'reason_code':row['reason_code'],
                'available_actions':actions})
        return {'mode':modes[0] if modes and len(set(modes))==1 else 'OBSERVE_ONLY' if not modes else 'PER_WORKER',
                'workers':workers,'accounts':accounts,'commands':commands,'external_tool_endpoint':False,
                'global_stop':self._global_stop()}
