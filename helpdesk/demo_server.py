"""Loopback workbench with simulation default and explicitly configured real runs.

The browser sends a fixed action name, never a destination, filesystem path,
question ID, or free-form instruction. Each request opens its own Store.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tomllib
from threading import Event, Lock, Thread
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from .__main__ import CONTENTS, demo_question
from .domain import Intent, Option
from .service import Helpdesk, Incoming
from .storage import Store
from .mcp_test_delivery import MCPTestAnswerDesktop


PASSAGE = "He returned home to look after his mother."
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
NATIVE_ARCHIVE_ROOT = ROOT.parent / 'data/private/chat-text-records'
STUDENTS = {"alice": ("DEMO_ALICE", "Alice"), "bob": ("DEMO_BOB", "Bob")}
ALLOWED_ACTIONS = frozenset({
    "new_alice", "new_bob", "followup_alice", "subquestion_alice",
    "correction_alice", "reorder_alice", "dispute_alice", "unknown_alice", "generate",
    "approve", "dispatch", "dispatch_ack", "stop", "resume", "recover",
    "dispatch_unknown",
})


def _binding(service: Helpdesk, student: str) -> str:
    student_key, display_name = STUDENTS[student]
    return service.bind("SIMULATION_GROUP", student_key, display_name, verified=True)


def _original(store: Store, binding_id: str):
    row = store.one("""SELECT m.id, m.case_id, m.question_id FROM messages m
        WHERE m.binding_id=? AND m.intent='NEW' AND m.status='PROCESSED'
        ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1""", (binding_id,))
    if not row:
        raise ValueError("请先提交 Alice 的完整样题")
    return row


def _latest_turn(store: Store):
    row = store.one("""SELECT t.id FROM turns t JOIN messages m ON m.id=t.message_id
        JOIN bindings b ON b.id=m.binding_id WHERE b.group_key='SIMULATION_GROUP'
        ORDER BY t.created_at DESC, t.rowid DESC LIMIT 1""")
    if not row:
        raise ValueError("请先提交演示题目")
    return row["id"]


def _outbox(store: Store, *, purposes: tuple[str, ...], review: str | None = None):
    query = """SELECT o.id FROM outbox o JOIN bindings b ON b.id=o.binding_id
        WHERE b.group_key='SIMULATION_GROUP' AND o.purpose IN (""" + ",".join("?" for _ in purposes) + ") AND o.state='PENDING'"
    params: list[str] = list(purposes)
    if review:
        query += " AND o.review_status=?"
        params.append(review)
    query += " ORDER BY o.created_at DESC, o.rowid DESC LIMIT 1"
    row = store.one(query, params)
    if not row:
        raise ValueError("没有可执行的待发送记录")
    return row["id"]


def perform(store: Store, action: str, db_path: Path) -> dict:
    """Execute one preset demonstration action in a fresh Store connection."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError("不支持的演示动作")
    from .workflow import Workflow

    service = Helpdesk(store)
    workflow = Workflow(store)
    if action in ("new_alice", "new_bob"):
        student = action.removeprefix("new_")
        bid = _binding(service, student)
        outcome = service.ingest(Incoming(
            bid, "请讲第12题。原文：" + PASSAGE,
            Intent.NEW, source="demo-ui", verified_question=demo_question(),
            raw_material=PASSAGE, verified_material=PASSAGE,
        ))
        return asdict(outcome)
    if action in ("followup_alice", "subquestion_alice", "correction_alice", "reorder_alice", "dispute_alice"):
        bid = _binding(service, "alice")
        original = _original(store, bid)
        common = {"binding_id": bid, "source": "demo-ui", "quote_message_id": original["id"]}
        if action == "followup_alice":
            incoming = Incoming(text="为什么不选B？", intent=Intent.FOLLOWUP, **common)
        elif action == "subquestion_alice":
            incoming = Incoming(text="那第13题呢？", intent=Intent.SUBQUESTION,
                                verified_question=demo_question("13", "What did he do after returning home?"), **common)
        elif action == "correction_alice":
            incoming = Incoming(text="更正：题干应为否定句。", intent=Intent.CORRECTION,
                                verified_question=demo_question(stem="Why did he NOT return home?"), **common)
        elif action == "reorder_alice":
            reordered = replace(demo_question(), options=tuple(
                Option.confirmed(label, text, index, "demo-fixture")
                for index, (label, text) in enumerate(zip("ABCD", reversed(CONTENTS)))))
            incoming = Incoming(text="更正：选项顺序拍反了，正确原题中 A 是照顾母亲。", intent=Intent.CORRECTION,
                                verified_question=reordered, **common)
        else:
            incoming = Incoming(text="老师答案不对，请复核。", intent=Intent.DISPUTE, **common)
        return asdict(service.ingest(incoming))
    if action == "unknown_alice":
        bid = _binding(service, "alice")
        return asdict(service.ingest(Incoming(bid, "这道题好像不对，请看一下。", Intent.UNKNOWN, source="demo-ui")))
    if action == "generate":
        turn_id = _latest_turn(store)
        return workflow.generate(turn_id)
    if action == "approve":
        outbox_id = _outbox(store, purposes=("ANSWER", "CORRECTION"), review="UNREVIEWED")
        workflow.approve(outbox_id)
        return {"outbox_id": outbox_id, "state": "APPROVED"}
    if action == "dispatch_unknown":
        from .delivery import MockDesktop
        bid = _binding(service, "alice")
        outcome = service.ingest(Incoming(bid, "模拟发送未知：请人工检查这条消息。", Intent.UNKNOWN, source="demo-ui"))
        row = store.one("SELECT id FROM outbox WHERE message_id=? AND purpose='ACK'", (outcome.message_id,))
        if not row:
            raise ValueError("未创建确认收到消息")
        faulted = Workflow(store, desktop=MockDesktop(str(db_path) + ".transport.db", fault="unknown"))
        return {"message_id": outcome.message_id, "outbox_id": row["id"], "state": faulted.dispatch(row["id"])}
    if action in ("dispatch", "dispatch_ack"):
        if action == "dispatch_ack":
            outbox_id = _outbox(store, purposes=("ACK",))
        else:
            outbox_id = _outbox(store, purposes=("ANSWER", "CORRECTION"),
                                review="APPROVED" if workflow._answer_review_required() else None)
        return {"outbox_id": outbox_id, "state": workflow.dispatch(outbox_id)}
    if action == "stop":
        workflow.set_stop(True)
        return {"stop_requested": True}
    if action == "resume":
        workflow.set_stop(False)
        return {"stop_requested": False}
    return {"recovered": workflow.recover()}


REAL_ACTIONS = frozenset({'real_generate', 'real_approve', 'real_dispatch_test', 'stop', 'resume'})


def perform_operator(store, payload, config):
    from .operator_tasks import OperatorTasks
    if not isinstance(payload, dict) or not isinstance(payload.get('action'), str):
        raise ValueError('Invalid local test task request')
    tasks = OperatorTasks(store)
    action = payload['action']
    if action == 'create' and set(payload) == {'action', 'payload', 'request_id'}:
        if not isinstance(payload['payload'], dict) or payload['payload'].get('attachments', []) != []:
            raise ValueError('Browser requests cannot supply local attachment paths')
        return tasks.create_draft(payload['payload'], request_id=payload['request_id'])
    if action == 'review' and set(payload) == {'action', 'draft_id', 'revision', 'reviewer', 'source_evidence'}:
        if type(payload['revision']) is not int:
            raise ValueError('Invalid draft revision')
        draft = tasks.get_draft(payload['draft_id'])
        if draft['label'] == 'SOURCE_MESSAGE':
            if not config or config.get('auto_prepare_after_question_review') is not True:
                raise ValueError('原消息答疑准备尚未启用')
            from .source_question_tasks import SourceQuestionTasks
            tasks = SourceQuestionTasks(store)
        task = tasks.review(payload['draft_id'], expected_revision=payload['revision'],
                            reviewer=payload['reviewer'], source_evidence=payload['source_evidence'])
        if config and config.get('auto_prepare_after_question_review') is True:
            from .reviewed_question_queue import request_enqueue
            try:
                queued = request_enqueue(store, task['id'], config['manifest'])
                task = tasks.get_task(task['id'])
                task['auto_queue'] = queued
            except (ValueError, OSError) as exc:
                # The human review is already committed. An ACK or material
                # prerequisite must not turn it into a second review request.
                task = tasks.get_task(task['id'])
                task['auto_queue'] = {
                    'phase': 'WAITING_ACK' if str(exc) == 'ACK_REQUIRED' else 'NEEDS_ATTENTION',
                    'enqueued': False, 'source_review_required_again': False,
                }
        return task
    if action == 'freeze' and set(payload) == {'action', 'task_id'}:
        if not config:
            raise ValueError('Freeze requires an explicitly configured teaching manifest')
        if config.get('auto_prepare_after_question_review') is True:
            raise ValueError('题面确认后的准备由后台队列承接，无需再次冻结')
        task = tasks.get_task(payload['task_id'])
        directory = store.path
        base = Path(directory).resolve().parent / 'private' / 'operator-tasks' / task['id']
        return tasks.freeze(task['id'], teaching_manifest=config['manifest'],
                            preparation_path=base / 'prepared-session.json', evidence_dir=base / 'generation')
    raise ValueError('Unsupported local test task request')


def load_real_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    required = {'database', 'run_id', 'preparation', 'manifest'}
    if not isinstance(config, dict) or not required <= config.keys() or config.keys() - required - {'test_outbox_id', 'pin', 'auto_prepare_after_question_review'}:
        raise ValueError('Real config requires database, run_id, preparation and manifest')
    if ('auto_prepare_after_question_review' in config
            and type(config['auto_prepare_after_question_review']) is not bool):
        raise ValueError('auto_prepare_after_question_review must be a boolean')
    for key in required:
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f'Invalid real config field: {key}')
    for key in ('database', 'preparation', 'manifest', 'pin'):
        if key in config:
            value = Path(config[key])
            config[key] = value.resolve() if value.is_absolute() else (path.parent / value).resolve()
            if not config[key].is_file():
                raise ValueError(f'Real config file missing: {key}')
    if ('pin' in config) != ('test_outbox_id' in config):
        raise ValueError('Test delivery requires both pin and test_outbox_id')
    if 'pin' in config:
        if not isinstance(config['test_outbox_id'], str) or not config['test_outbox_id']:
            raise ValueError('Invalid test_outbox_id')
        pin = json.loads(config['pin'].read_text(encoding='utf-8'))
        if pin.get('outbox_id') != config['test_outbox_id']:
            raise ValueError('Pin and configured test outbox do not match')
        config['_pin'] = pin
    with _store(config['database']) as store:
        run = store.one('SELECT * FROM runs WHERE id=?', (config['run_id'],))
        if run is None:
            raise ValueError('Configured frozen run does not exist')
        from .mcp_generation import PreparedDeepSeekGenerator
        snapshot = json.loads(run['input_json'])
        if snapshot.get('simulated') is not False or snapshot.get('generation_adapter') != PreparedDeepSeekGenerator.identity:
            raise ValueError('Real workbench requires a frozen prepared DeepSeek run')
        preparation = json.loads(config['preparation'].read_text(encoding='utf-8'))
        if preparation.get('run_id') != config['run_id'] or preparation.get('operator_verified') is not True:
            raise ValueError('Preparation must be reviewed for the configured run')
    return config


def operator_generation_context(store, task_id, config):
    """Validate persisted input and reuse completed FAST source-review evidence."""
    from hashlib import sha256
    from .operator_tasks import OperatorTasks
    from .mcp_generation import PreparedDeepSeekGenerator, input_fingerprint
    from .workflow import Workflow
    if not config or Path(config['database']).resolve() != Path(store.path).resolve():
        raise ValueError('本地测试生成需要已配置的真实工作台数据库')
    if not isinstance(task_id, str) or not task_id.isalnum() or not 12 <= len(task_id) <= 64:
        raise ValueError('无效的本地测试任务编号')
    task = OperatorTasks(store).get_task(task_id)
    if task['label'] != 'OPERATOR_TEST' or not task['run_id']:
        raise ValueError('只能生成已冻结的本地测试任务')
    run = store.one('SELECT * FROM runs WHERE id=?', (task['run_id'],))
    message = store.one('SELECT source,binding_id FROM messages WHERE id=?', (task['message_id'],))
    binding = store.one('SELECT group_key,student_key FROM bindings WHERE id=?', (task['binding_id'],))
    if (not run or not message or message['source'] != 'OPERATOR_TEST'
            or message['binding_id'] != task['binding_id'] or not binding
            or not binding['group_key'].startswith('local-operator-test:')
            or not binding['student_key'].startswith('local-fixture:')):
        raise ValueError('任务不是隔离的本地测试来源')
    snapshot = json.loads(run['input_json'])
    identity = PreparedDeepSeekGenerator.identity
    marker = snapshot.get('operator_test', {})
    if (marker.get('label') != 'OPERATOR_TEST' or marker.get('task_id') != task_id
            or marker.get('draft_id') != task['draft_id']
            or marker.get('draft_revision') != task['draft_revision']
            or marker.get('formal_statistics_eligible') is not False
            or snapshot.get('generation_adapter') != identity or snapshot.get('simulated') is not False
            or snapshot.get('run_id') != run['id'] or snapshot.get('session_id') != run['session_id']
            or snapshot.get('case_id') != task['case_id']
            or run['turn_id'] != task['turn_id'] or run['question_id'] != task['question_id']
            or snapshot.get('question_id') != task['question_id']
            or (snapshot.get('question_version'), snapshot.get('context_revision')) !=
               (task['question_version'], task['context_revision'])
            or (run['question_version'], run['context_revision']) !=
               (task['question_version'], task['context_revision'])):
        raise ValueError('本地测试任务与冻结输入不一致')
    if (input_fingerprint(snapshot) != task['input_fingerprint']
            or input_fingerprint(Helpdesk(store).context(task['turn_id'])) != task['input_fingerprint']):
        raise ValueError('本地测试任务输入已变更，请重新审核')
    session = store.one('SELECT * FROM sessions WHERE id=?', (run['session_id'],))
    if not session or session['state'] != 'ACTIVE' or session['adapter'] != identity or session['case_id'] != task['case_id']:
        raise ValueError('本地测试任务的生成会话已失效')
    # Terminal runs are never submitted again, including after a server restart.
    if run['state'] != 'RUNNING':
        return task, run
    if not all(task.get(key) for key in ('preparation_path', 'manifest_path', 'evidence_dir')):
        raise ValueError('本地测试任务尚未保存会话准备路径')
    candidate = Path(task['preparation_path']).with_name('preparation-candidate.json')
    if not Path(task['preparation_path']).exists() and candidate.is_file():
        # Reuse the already committed source-clarity review. This does not
        # touch the desktop, submit a prompt or create another human review.
        from .automatic_preparation import complete_automatic_preparation
        complete_automatic_preparation(store, task_id, candidate)
    try:
        manifest = Path(task['manifest_path'])
        if sha256(manifest.read_bytes()).hexdigest() != task['manifest_sha256']:
            raise ValueError('冻结课程清单已变更')
        flow = Workflow(store, teaching_manifest=manifest)
        if {item['path']: item['sha256'] for item in flow._skills()} != {
                item['path']: item['sha256'] for item in snapshot['teaching_skills']}:
            raise ValueError('冻结课程文件已变更')
        generator = PreparedDeepSeekGenerator(None, task['preparation_path'], task['evidence_dir'])
        preparation, _ = generator._preparation(snapshot)
        if preparation.get('run_id') != task['run_id'] or preparation.get('operator_verified') is not True:
            raise ValueError('会话准备尚未由操作员审核')
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('本地测试会话准备尚未完成核验，请先完成准备审核') from None
    return task, run


class WorkbenchTestDesktop(MCPTestAnswerDesktop):
    """Bring the fixed WeCom application forward under Workflow's desktop lock.

    The inherited preflight then verifies the pinned chat and empty editor.
    Switching applications never selects a contact or changes the destination.
    """
    def preflight(self, message):
        self._call('App', mode='switch', name='企业微信')
        return super().preflight(message)


def perform_real(store, action, config, *, generator=None, transport_factory=None, desktop_factory=None):
    from .workflow import Workflow
    if action not in REAL_ACTIONS:
        raise ValueError('真实模式只允许已配置任务的固定动作')
    flow = Workflow(store)
    if action in ('stop', 'resume'):
        flow.set_stop(action == 'stop')
        return {'stop_requested': action == 'stop'}
    if action == 'real_generate':
        if generator is None:
            from tools.run_prepared_deepseek import run_existing
            generator = run_existing
        return generator(store, config['run_id'], config['preparation'], config['manifest'])
    if action == 'real_approve':
        row = store.one("SELECT * FROM outbox WHERE run_id=? AND purpose IN ('ANSWER','CORRECTION') ORDER BY rowid DESC LIMIT 1", (config['run_id'],))
        if not row or row['simulated'] != 0 or row['state'] != 'PENDING':
            raise ValueError('配置任务没有当前真实待审核答案')
        from .test_answer_queue import validate_source_answer
        validate_source_answer(store, row, approval=False)
        if row['review_status'] == 'APPROVED':
            validate_source_answer(store, row)
            return {'outbox_id': row['id'], 'state': 'APPROVED', 'reapproved': False}
        flow.approve(row['id'], reviewer='local_real_workbench_operator')
        return {'outbox_id': row['id'], 'state': 'APPROVED'}
    if not config.get('pin') or not config.get('test_outbox_id'):
        raise ValueError('尚未配置已核验测试副本与联系人凭据，发送不可用')
    pin = config['_pin']
    outbox_id = config['test_outbox_id']
    row = store.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
    if (pin.get('outbox_id') != outbox_id or not row or row['purpose'] != 'TEST_ANSWER'
            or row['simulated'] != 0 or row['run_id'] != config['run_id']):
        raise ValueError('只能发送配置任务的真实测试副本')
    if row['state'] != 'PENDING':
        return {'outbox_id': outbox_id, 'state': row['state'], 'resubmitted': False}
    if row['review_status'] != 'APPROVED':
        raise ValueError('测试副本尚未批准')
    from .test_answer_queue import validate_test_copy
    validate_test_copy(store, row)
    if transport_factory is None:
        from .mcp_transport import MCPProcess
        transport_factory = MCPProcess
    if desktop_factory is None:
        desktop_factory = WorkbenchTestDesktop
    with transport_factory() as transport:
        desktop = desktop_factory(transport, pin, ROOT.parent)
        result = Workflow(store, desktop=desktop).dispatch(outbox_id)
    return {'outbox_id': outbox_id, 'state': result, 'source_student_delivered': False}


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], db_path: Path, *, real_config=None, real_generator=None, transport_factory=None, desktop_factory=None, native_archive_root=None, collector_config=None, processing_mode="COMPATIBILITY", collector_source_factory=None, answer_review_root=None, worker_boundary=False, worker_policy=None, worker_token_env='HELPDESK_WORKER_TOKEN', performance_enabled=None, source_review_manifest=None, reference_lookup_config=None, automatic_delivery_config=None, automatic_desktop_factory=None, automatic_answer_config=None, automatic_answer_transport_factory=None, automatic_answer_navigator=None):
        if address[0] != "127.0.0.1":
            raise ValueError("演示服务只允许绑定 127.0.0.1")
        if processing_mode not in {"COMPATIBILITY", "ACK_ONLY"}:
            raise ValueError("Unknown workbench processing mode")
        if processing_mode == "ACK_ONLY" and real_config:
            raise ValueError("ACK_ONLY cannot enable a prepared teaching session")
        if performance_enabled is not None and type(performance_enabled) is not bool:
            raise ValueError("performance_enabled must be a boolean")
        self.processing_mode = processing_mode
        from .reference_lookup import LookupConfig, ReferenceLookup
        self.reference_lookup_error = None
        try:
            self.reference_lookup = ReferenceLookup(LookupConfig.load(reference_lookup_config)
                if reference_lookup_config else LookupConfig())
        except (ValueError, OSError):
            self.reference_lookup = ReferenceLookup(LookupConfig())
            self.reference_lookup_error = 'CONFIG_UNAVAILABLE_OR_INVALID'
        self.worker_boundary = bool(worker_boundary)
        self.worker_token_worker_id = None
        # Credentials stay in the process environment, not DB/logs/HTTP state.
        self.worker_api_token = os.environ.get(worker_token_env, '')
        if any(c in self.worker_api_token for c in ('\r','\n','\x00')):
            raise ValueError('Invalid Worker environment token')
        self.collector_config = tomllib.loads(Path(collector_config).resolve().read_text(encoding="utf-8")) if collector_config else None
        stage = (self.collector_config or {}).get("stage")
        if stage is not None and not isinstance(stage, dict):
            raise ValueError("stage must be a configuration table")
        if stage is not None and "source_review_manifest" in stage:
            configured_manifest = stage["source_review_manifest"]
            if not isinstance(configured_manifest, str) or not configured_manifest.strip():
                raise ValueError("source_review_manifest must be a nonempty path")
            if source_review_manifest is None:
                source_review_manifest = configured_manifest
        self.real_config = load_real_config(real_config) if real_config else None
        self.source_review_enabled = source_review_manifest is not None
        self.teaching_blocked_reason = None
        if self.source_review_enabled:
            if processing_mode != 'ACK_ONLY':
                raise ValueError('Source review manifest requires ACK_ONLY collection mode')
            from .teaching_bundle import TeachingBundleError, verify_bundle
            manifest = Path(source_review_manifest).resolve(strict=True)
            bundle = verify_bundle(manifest)
            if (bundle.get('answer_generation_allowed_by_course') is not True
                    and bundle.get('format_version') != 2):
                raise ValueError('Source review requires an approved answer-generation teaching bundle')
            self.operator_config = {'manifest': manifest, 'auto_prepare_after_question_review': True}
            try:
                verify_bundle(manifest, for_generation=True)
            except TeachingBundleError as error:
                self.teaching_blocked_reason = str(error)
                self.operator_config['auto_prepare_after_question_review'] = False
        else:
            self.operator_config = self.real_config
        self.question_auto_continue = bool(self.operator_config and
            self.operator_config.get('auto_prepare_after_question_review') is True)
        self.db_path = self.real_config['database'] if self.real_config else db_path.resolve()
        if self.processing_mode == "ACK_ONLY" and self.collector_config:
            configured = self.collector_config.get("collector", {}).get("business_database")
            if configured:
                self.db_path = Path(configured).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        from .automatic_delivery_runtime import AutomaticDeliveryRuntime
        self.automatic_delivery = AutomaticDeliveryRuntime(self.db_path, automatic_delivery_config,
            collector_config=self.collector_config, desktop_factory=automatic_desktop_factory)
        from .automatic_answer_runtime import AutomaticAnswerRuntime
        self.automatic_answer = AutomaticAnswerRuntime(self.db_path, automatic_answer_config,
            manifest=(self.operator_config or {}).get('manifest'), reference_config=reference_lookup_config,
            transport_factory=automatic_answer_transport_factory, navigator=automatic_answer_navigator)
        if self.automatic_answer.enabled and (not self.question_auto_continue or self.worker_boundary or self.real_config):
            raise ValueError('Local automatic answers require reviewed source tasks and the local desktop boundary')
        if self.worker_boundary and self.automatic_delivery.enabled:
            raise ValueError('Choose the existing Worker boundary or the local automatic sender, not both')
        if self.real_config and self.automatic_delivery.enabled:
            raise ValueError('Prepared test delivery and formal automatic group delivery use separate launch configurations')
        self.performance_available = performance_enabled if performance_enabled is not None else processing_mode != "ACK_ONLY"
        if stage is not None:
            if "performance_enabled" in stage:
                if type(stage["performance_enabled"]) is not bool:
                    raise ValueError("performance_enabled must be a boolean")
                self.performance_available = stage["performance_enabled"]
            if "require_ack_before_generation" in stage and type(stage["require_ack_before_generation"]) is not bool:
                raise ValueError("require_ack_before_generation must be a boolean")
            if "answer_review_required" in stage and type(stage["answer_review_required"]) is not bool:
                raise ValueError("answer_review_required must be a boolean")
            from .workflow import Workflow
            with _store(self.db_path) as store:
                workflow = Workflow(store)
                if "delivery" in stage or "name" in stage:
                    policy, name = workflow._delivery_policy_config()
                    if policy != stage.get("delivery") or name != stage.get("name"):
                        workflow.set_delivery_policy(stage.get("delivery"), stage.get("name"))
                if "require_ack_before_generation" in stage:
                    workflow.set_require_ack_before_generation(stage["require_ack_before_generation"],
                        actor="trusted_workbench_startup")
                if "answer_review_required" in stage:
                    workflow.set_answer_review_required(stage["answer_review_required"],
                        actor="trusted_workbench_startup")
        if self.question_auto_continue:
            from .workflow import Workflow
            with _store(self.db_path) as store:
                workflow = Workflow(store)
                if not self.automatic_delivery.enabled:
                    workflow.set_answer_review_required(False, actor="trusted_workbench_startup")
                workflow.set_require_source_clarity_review(True, actor="trusted_workbench_startup")
                if self.source_review_enabled:
                    workflow.set_require_ack_before_generation(True, actor="trusted_workbench_startup")
                policy, name = workflow._delivery_policy_config()
                manual = dict(policy or {})
                for purpose in ("ANSWER", "CORRECTION"):
                    if not self.source_review_enabled or manual.get(purpose) != "DISABLED":
                        manual[purpose] = "MANUAL"
                if policy != manual and not self.automatic_delivery.enabled:
                    workflow.set_delivery_policy(manual, name or "题面确认后排队，讲解人工发送")
        self.collector_supervisor = None
        if processing_mode == "ACK_ONLY":
            from .collector_supervisor import CollectorSupervisor
            self.collector_supervisor = CollectorSupervisor(self.collector_config, source_factory=collector_source_factory)
        self.real_dependencies = dict(generator=real_generator, transport_factory=transport_factory, desktop_factory=desktop_factory)
        self.job_lock = Lock()
        self.jobs = {}
        self.active_job = None
        self.csrf_token = secrets.token_urlsafe(32)
        self.native_archive_root = Path(native_archive_root or NATIVE_ARCHIVE_ROOT).resolve()
        self.answer_review_root = Path(answer_review_root).resolve(strict=True) if answer_review_root else None
        if self.answer_review_root is not None:
            from .answer_review_packets import list_review_packets
            list_review_packets(self.answer_review_root)
        self.native_export_lock = Lock()
        self.reviewed_queue_stop = Event()
        self.reviewed_queue_thread = None
        self.reviewed_queue_error = None
        self.answer_queue_thread = None
        self.answer_queue_error = None
        # Validate stage/paths before initializing server-owned execution tables.
        from .worker_coordinator import WorkerCoordinator
        with _store(self.db_path) as store:
            coordinator = WorkerCoordinator(store)
            if worker_policy is not None:
                value = json.loads(Path(worker_policy).read_text(encoding='utf-8'))
                if set(value) != {'worker_id','policy'}:
                    raise ValueError('Worker configuration requires a fixed identity and policy')
                coordinator.configure_worker(value['worker_id'],value['policy'])
                self.worker_token_worker_id=value['worker_id']
        super().__init__(address, DemoHandler)
        if self.question_auto_continue or self.automatic_delivery.enabled:
            self.reviewed_queue_thread = Thread(target=self._resume_reviewed_queue,
                name="reviewed-question-admission", daemon=True)
            self.reviewed_queue_thread.start()
        if self.automatic_answer.enabled:
            self.answer_queue_thread = Thread(target=self._execute_reviewed_queue,
                name="reviewed-question-execution", daemon=True)
            self.answer_queue_thread.start()

    def server_close(self):
        self.automatic_delivery.pause_requested.set()
        self.automatic_answer.control('pause')
        self.reviewed_queue_stop.set()
        if self.reviewed_queue_thread:
            self.reviewed_queue_thread.join(1)
        if self.answer_queue_thread:
            self.answer_queue_thread.join(1)
        if self.collector_supervisor:
            self.collector_supervisor.stop(wait_seconds=1)
        super().server_close()

    def _resume_reviewed_queue(self):
        """ACK/answer delivery is independent of teaching generation and admission."""
        from .reviewed_question_queue import resume_pending
        while not self.reviewed_queue_stop.is_set():
            try:
                if self.automatic_delivery.enabled:
                    self.automatic_delivery.tick()
                with _store(self.db_path) as store:
                    if self.question_auto_continue:
                        resume_pending(store)
                self.reviewed_queue_error = None
            except Exception as exc:
                self.reviewed_queue_error = type(exc).__name__
            self.reviewed_queue_stop.wait(1)

    def _execute_reviewed_queue(self):
        """Page preparation/generation never occupies the ACK admission loop."""
        while not self.reviewed_queue_stop.is_set():
            try:
                self.automatic_answer.tick()
                self.answer_queue_error = None
            except Exception as exc:
                self.answer_queue_error = type(exc).__name__
            self.reviewed_queue_stop.wait(1)

    def collector_snapshot(self):
        config = self.collector_config or {}
        settings = config.get("collector", {})
        archive = config.get("archive", {})
        source = config.get("source", {})
        native_import = source.get("kind") == "native_clipboard_archive"
        account_configured = bool(source.get("factory") and all(archive.get(key) is True
            for key in ("enabled", "admin_authorized", "member_scope_verified", "consent_verified"))
            and archive.get("evidence_reference") and archive.get("bridge_command"))
        metrics = {"health": "NEVER_SYNCED", "current_cursor": None,
                   "last_successful_sync_at": None, "messages_received_today": 0, "events_pending": 0}
        database = Path(settings.get("database", "data/messages.db")).resolve()
        if self.collector_config and database.is_file():
            from .collector_storage import CollectorStore
            from .collector_cli import status
            # Read existing data without schema creation or pretending to start a listener.
            collector = CollectorStore.__new__(CollectorStore)
            collector.path = database
            try:
                metrics = status(collector, source_name=source.get("name", "wecom_archive"),
                    stale_after_seconds=settings.get("stale_after_seconds", 300),
                    business_timezone=settings.get("business_timezone", "Asia/Shanghai"))
            except Exception:
                metrics = {"health": "FAILED", "error": "消息库状态不可读取，请检查采集配置与数据库"}
        with _store(self.db_path) as store:
            pending = store.one("SELECT COUNT(*) FROM outbox o JOIN messages m ON m.id=o.message_id WHERE o.purpose='ACK' AND o.state='PENDING' AND m.source LIKE 'collector:%'")[0]
            held = store.one("SELECT COUNT(*) FROM collector_answer_tasks WHERE state='ACK_HELD'")[0] if store.one("SELECT name FROM sqlite_master WHERE name='collector_answer_tasks'") else 0
        # Transport error text is not UI-safe: it may contain credentials.
        if metrics.get("last_error"):
            metrics["last_error"] = "SYNC_ERROR"
        if isinstance(metrics.get("state"), dict) and metrics["state"].get("last_error"):
            metrics["state"]["last_error"] = "SYNC_ERROR"
        control = self.collector_supervisor.snapshot() if self.collector_supervisor else None
        return {"configured": self.collector_config is not None,
            "processing_mode": "ACK_ONLY", "metrics": metrics, "pending_ack_count": pending,
            "held_ack_count": held, "account_status": "NOT_APPLICABLE" if native_import else "NEEDS_MANUAL_VERIFICATION" if account_configured else "NOT_CONFIGURED",
            "collection_kind": "NATIVE_CLIPBOARD_IMPORT" if native_import else "OFFICIAL_ARCHIVE",
            "desktop_listener_live_verified": False,
            "source_polling_started_by_workbench": bool(control and control["worker_alive"]),
            "account_live_verified": False, "listener_started_by_workbench": bool(not native_import and control and control["worker_alive"]),
            "control": control,
            "student_send_enabled": False, "teaching_enabled": False}


    def start_real_job(self, action):
        if self.worker_boundary and action not in {'stop','resume','recover'}:
            return 409, {'error':'当前使用专用Worker，后台不直接执行桌面动作', 'status':'WORKER_COMMAND_REQUIRED'}
        if self.processing_mode == "ACK_ONLY":
            raise ValueError("当前仅采集和排队收到，答疑与答案发送入口已关闭")
        if action not in REAL_ACTIONS:
            raise ValueError('真实模式不接受模拟或未配置动作')
        if action in ('stop', 'resume'):
            with _store(self.db_path) as store:
                return 200, {'result': perform_real(store, action, self.real_config)}
        if self.question_auto_continue:
            raise ValueError('题面确认后的步骤由Luna后台队列承接，讲解由人工发送')
        if action == 'real_dispatch_test' and not self.real_config.get('pin'):
            raise ValueError('尚未配置已核验测试副本与联系人凭据，发送不可用')
        with _store(self.db_path) as store:
            from .workflow import Workflow
            if Workflow(store)._stopped():
                return 409, {'error': '任务已停止，请先恢复运行'}
        with self.job_lock:
            if action in self.jobs:
                return 202, {'job': dict(self.jobs[action]), 'resubmitted': False}
            if self.active_job:
                return 409, {'error': '已有真实任务执行中，请等待完成'}
            self.jobs[action] = {'action': action, 'state': 'RUNNING'}
            self.active_job = action
            Thread(target=self._real_worker, args=(action,), daemon=True).start()
            return 202, {'job': dict(self.jobs[action])}

    def _real_worker(self, action):
        try:
            with _store(self.db_path) as store:
                result = perform_real(store, action, self.real_config, **self.real_dependencies)
            job = {'action': action, 'state': 'COMPLETED', 'result': result}
        except Exception as exc:
            job = {'action': action, 'state': 'FAILED', 'error': str(exc)}
        with self.job_lock:
            self.jobs[action] = job
            self.active_job = None

    def start_operator_generation(self, task_id):
        if self.worker_boundary:
            return 409, {'error':'当前使用专用Worker，生成需要绑定已授权执行命令', 'status':'WORKER_COMMAND_REQUIRED'}
        if self.processing_mode == "ACK_ONLY":
            raise ValueError("当前仅采集和排队收到，生成入口已关闭")
        if self.question_auto_continue:
            raise ValueError('题面确认后已交后台队列，无需再次点击生成')
        # Validation failures create no cached job: the same task can be prepared
        # and clicked again. The browser supplies only this opaque task identifier.
        with _store(self.db_path) as store:
            task, run = operator_generation_context(store, task_id, self.real_config)
            from .workflow import Workflow
            if Workflow(store)._stopped():
                return 409, {'error': '任务已停止，请先恢复运行'}
        key = 'operator_generate:' + task_id
        context = {'action': 'operator_generate', 'task_id': task_id,
                   'label': 'OPERATOR_TEST', 'run_id': task['run_id']}
        with self.job_lock:
            if key in self.jobs:
                return 202, {'job': dict(self.jobs[key]), 'resubmitted': False}
            if self.active_job:
                return 409, {'error': '已有真实任务执行中，请等待完成'}
            if run['state'] != 'RUNNING':
                result = {'existing_run': {'id': run['id'], 'state': run['state'], 'error': run['error']},
                          'resubmitted': False}
                self.jobs[key] = {**context, 'state': 'COMPLETED', 'result': result}
                return 202, {'job': dict(self.jobs[key]), 'resubmitted': False}
            self.jobs[key] = {**context, 'state': 'RUNNING'}
            self.active_job = key
            Thread(target=self._operator_generation_worker, args=(key, context), daemon=True).start()
            return 202, {'job': dict(self.jobs[key])}

    def _operator_generation_worker(self, key, context):
        try:
            from tools.run_prepared_deepseek import run_existing
            with _store(self.db_path) as store:
                task, _ = operator_generation_context(store, context['task_id'], self.real_config)
                result = run_existing(store, task['run_id'], Path(task['preparation_path']),
                    Path(task['manifest_path']), evidence_dir=Path(task['evidence_dir']),
                    transport_factory=self.real_dependencies['transport_factory'])
            job = {**context, 'state': 'COMPLETED', 'result': result}
        except Exception as exc:
            job = {**context, 'state': 'FAILED', 'error': str(exc)}
        with self.job_lock:
            self.jobs[key] = job
            if self.active_job == key:
                self.active_job = None

    def job_snapshot(self):
        with self.job_lock:
            return {key: dict(value) for key, value in self.jobs.items()}


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoHTTPServer

    def log_message(self, fmt: str, *args):
        # Avoid printing untrusted request text in the console.
        pass

    def _headers(self, status: int, content_type: str, length: int, *, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status: int, value):
        payload = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self._headers(status, "application/json; charset=utf-8", len(payload))
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A closed local browser is not a failed/retryable business action.
            return

    def _host_valid(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self._host_valid():
            self._json(403, {"error": "主机地址检查未通过"})
            return
        route = urlsplit(self.path).path
        if route == '/api/delivery/tasks':
            with _store(self.server.db_path) as store:
                result = self.server.automatic_delivery.snapshot(store)
            self._json(200, result)
            return
        if route == '/api/worker/control':
            from .worker_coordinator import WorkerCoordinator
            with _store(self.server.db_path) as store:
                result = WorkerCoordinator(store).snapshot()
            self._json(200,dict(result,csrf_token=self.server.csrf_token,
                worker_boundary=self.server.worker_boundary,worker_api_configured=bool(self.server.worker_api_token)))
            return
        if route == '/api/worker/status':
            if not self._worker_authenticated():return
            from .worker_coordinator import WorkerCoordinator
            try:
                params=parse_qs(urlsplit(self.path).query,strict_parsing=True)
                if (set(params)!={'worker_id'} or len(params['worker_id'])!=1
                        or params['worker_id'][0]!=self.server.worker_token_worker_id):raise ValueError('Bound node required')
                with _store(self.server.db_path) as store:
                    result=WorkerCoordinator(store).worker_state(params['worker_id'][0])
                self._json(200,result)
            except (ValueError,KeyError,TypeError):self._json(409,{'error':'WORKER_STATUS_REJECTED'})
            return
        if route in {'/api/worker/command','/api/worker/resource'}:
            if not self._worker_authenticated():
                return
            from .worker_coordinator import WorkerCoordinator
            try:
                params=parse_qs(urlsplit(self.path).query,strict_parsing=True)
                required={'command_id','worker_id'} if route.endswith('/command') else {'worker_id','command_id','lease_id','resource_ref'}
                if set(params)!=required or any(len(v)!=1 for v in params.values()) or params['worker_id'][0]!=self.server.worker_token_worker_id:
                    raise ValueError('Fixed worker command identifiers required')
                with _store(self.server.db_path) as store:
                    result=(WorkerCoordinator(store).command(params['worker_id'][0],params['command_id'][0]) if route.endswith('/command')
                        else WorkerCoordinator(store).resource(**{k:v[0] for k,v in params.items()}))
                self._json(200,result)
            except (ValueError,KeyError,TypeError):
                self._json(409,{'error':'WORKER_COMMAND_REJECTED'})
            return
        if route == '/api/answer-review-packets':
            from .answer_review_packets import list_review_packets
            try:
                self._json(200, list_review_packets(self.server.answer_review_root))
            except (OSError, ValueError, KeyError, TypeError):
                self._json(409, {'error': '待审讲解的来源或正文发生变化，请检查原始记录'})
            return
        if route == '/api/native-records/export':
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if (self.headers.get('Origin', expected_origin) != expected_origin or
                    not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.csrf_token)):
                self._json(403, {'error': '原文导出需要本机页面令牌'})
                return
            if urlsplit(self.path).query:
                self._json(400, {'error': '原文导出不接受路径或其他参数'})
                return
            if not self.server.native_export_lock.acquire(blocking=False):
                self._json(409, {'error': '原文导出正在进行，请稍后重试'})
                return
            try:
                from .native_export import export_native_records
                root = self.server.native_archive_root
                # Bound work before the exporter reads source files. Original
                # journals are also bounded, because provenance checks read them.
                folders = []
                for folder in root.iterdir():
                    folders.append(folder)
                    if len(folders) > 500:
                        raise ValueError('原文采集件过多，请使用本地导出工具')
                source_files = set()
                entries = 0
                source_size = 0
                for folder in folders:
                    if not folder.is_dir():
                        continue
                    if folder.resolve().parent != root:
                        raise ValueError('原文采集目录超出固定归档范围')
                    for source in folder.rglob('*'):
                        entries += 1
                        if entries > 4000:
                            raise ValueError('原文归档文件过多，请使用本地导出工具')
                        if source.is_file():
                            source_files.add(source)
                            source_size += source.stat().st_size
                            if source_size > 32 * 1024 * 1024:
                                raise ValueError('原文归档过大，请使用本地导出工具')
                    attempt = folder / 'clipboard-attempt.json'
                    if attempt.stat().st_size > 65536:
                        raise ValueError('原文采集凭据过大')
                    value = json.loads(attempt.read_bytes())
                    result = Path(value['result_path'])
                    source_files.update((result, result.parent / f"attempt-{value['attempt_id']}.json"))
                if len(source_files) > 4000 or sum(p.stat().st_size for p in source_files) > 32 * 1024 * 1024:
                    raise ValueError('原文导出超过本机下载大小限制，请使用本地导出工具')
                with TemporaryDirectory(prefix='helpdesk-native-export-') as staging:
                    exported = export_native_records(root, Path(staging) / 'export')
                    if not exported['collection_count']:
                        self._json(404, {'error': '尚无已核验的 English 群原文采集件可导出'})
                        return
                    payload = Path(exported['files']['原文与采集凭据.zip']['path']).read_bytes()
                self._headers(200, 'application/zip', len(payload),
                              attachment='native-records-partial.zip')
                self.wfile.write(payload)
            except (OSError, ValueError, KeyError, TypeError):
                self._json(409, {'error': '原文导出未完成：采集凭据待核验、文件不可用或超过下载限制，请核对本地原始归档'})
            finally:
                self.server.native_export_lock.release()
            return
        if route == "/api/operator-tasks":
            with _store(self.server.db_path) as store:
                from .reviewed_question_queue import list_admissions, list_queue
                queue_by_task = {row['task_id']: row for row in list_admissions(store)}
                for row in list_queue(store):
                    queue_by_task[row['task_id']] = {
                        **queue_by_task.get(row['task_id'], {}), **row, 'enqueued': True}
                if store.one("SELECT name FROM sqlite_master WHERE name='operator_drafts'"):
                    from .operator_tasks import OperatorTasks
                    tasks = OperatorTasks.read_only(store)
                    result = {'drafts': tasks.list_drafts(), 'tasks': tasks.list_tasks()}
                    if self.server.source_review_enabled:
                        result = {key: [row for row in rows if row['label'] == 'SOURCE_MESSAGE']
                                  for key, rows in result.items()}
                    for draft in result['drafts']:
                        if draft['label'] == 'SOURCE_MESSAGE':
                            from .source_question_tasks import draft_source_info
                            try:
                                draft['original_source'] = draft_source_info(store, draft['id'])
                                draft['source_valid'] = True
                            except (ValueError, OSError, KeyError, TypeError):
                                draft['source_valid'] = False
                    for task in result['tasks']:
                        task['preparation_reviewed'] = False
                        queued = queue_by_task.get(task['id'])
                        if queued:
                            task['auto_queue'] = queued
                            task['preparation_reviewed'] = queued['phase'] in ('ATTACHMENTS_READY', 'GENERATED')
                        elif self.server.question_auto_continue:
                            from .workflow import Workflow
                            try:
                                Workflow(store)._require_confirmed_ack(task['turn_id'], require_real=True)
                                phase = 'NEEDS_ATTENTION'
                            except ValueError:
                                phase = 'WAITING_ACK'
                            task['auto_queue'] = {'phase': phase, 'enqueued': False,
                                'source_review_required_again': False}
                        elif task['run_state'] == 'RUNNING':
                            try:
                                operator_generation_context(store, task['id'], self.server.real_config)
                                task['preparation_reviewed'] = True
                            except (ValueError, KeyError, TypeError, OSError):
                                pass
                else:
                    result = {'drafts': [], 'tasks': []}
            self._json(200, {**result, 'question_auto_continue': self.server.question_auto_continue,
                'label': 'MIXED_SOURCE' if any(d['label'] == 'SOURCE_MESSAGE' for d in result['drafts']) else 'OPERATOR_TEST',
                'formal_statistics_eligible': False})
            return
        if route == "/api/source-question-image":
            if self.server.processing_mode == 'ACK_ONLY' and not self.server.source_review_enabled:
                self._json(403, {'error': '当前未启用题面确认'})
                return
            try:
                from .source_question_tasks import source_image
                query = parse_qs(urlsplit(self.path).query)
                if set(query) != {'draft_id', 'index'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('Original source image request required')
                with _store(self.server.db_path) as store:
                    content_type, data = source_image(store, query['draft_id'][0], int(query['index'][0]))
            except (ValueError, OSError, KeyError, TypeError):
                self._json(404, {'error': '原题图片尚不能可靠读取，请检查题面来源'})
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)
            return
        if route == "/api/native-records":
            from .native_intake import list_staged_records
            config = self.server.collector_config or {}
            source = config.get("source", {})
            if source.get("kind") == "native_clipboard_archive":
                records = []
                path = Path(config.get("collector", {}).get("database", "data/native-messages.db")).resolve()
                if path.is_file():
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                        connection.row_factory = sqlite3.Row
                        rows = connection.execute("SELECT raw_content,raw_payload FROM messages WHERE source_name=? ORDER BY ingested_at,message_id LIMIT 250",
                            (source.get("name", "windows_native_staging"),)).fetchall()
                    for row in rows:
                        payload = json.loads(row["raw_payload"])
                        record = dict(payload["provenance"])
                        record.update(original_text=row["raw_content"], evidence=payload["evidence_paths"],
                            record_status="RAW_TEXT_AWAITING_MESSAGE_REVIEW", source_kind="WINDOWS_MCP_NATIVE_CLIPBOARD",
                            group_id=None, sender=None, original_message_time=None, reliable_timestamp=False,
                            formal_statistics_eligible=False, coverage_complete=False,
                            missing_metadata=payload["review_required"])
                        records.append(record)
            else:
                with _store(self.server.db_path) as store:
                    records = list_staged_records(store)
            self._json(200, {"records": records, "count": len(records),
                             "coverage_complete": False, "formal_statistics_eligible": False})
            return
        if route == '/api/manual-delivery-replies':
            from .manual_delivery import ManualDeliveries
            import sqlite3
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if (set(query) not in ({'original_outbox_id'}, {'turn_id'})
                    or any(len(values) != 1 or not 0 < len(values[0]) <= 128 for values in query.values())):
                self._json(400, {'error': '请选择一个已有的原答疑任务'})
                return
            try:
                with _store(self.server.db_path) as store:
                    result = ManualDeliveries(store).saved_replies(**{key:values[0] for key,values in query.items()})
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
                result = {'status': 'UNAVAILABLE', 'replies': [], 'continuous_listener': False, 'sends_messages': False,
                    'message': '该任务的原消息库、老师身份或来源证据尚不可核验，请使用人工填写；不能据此判断群里没有消息。'}
            self._json(200, result)
            return
        if route == '/api/manual-deliveries':
            from .manual_delivery import ManualDeliveries
            root = self.server.db_path.parent / 'delivery-attachments'
            with _store(self.server.db_path) as store:
                tasks = ManualDeliveries(store, attachment_root=root).list()
            files = [p.name for p in root.iterdir() if p.is_file()][:40] if root.is_dir() else []
            self._json(200, {'tasks': tasks, 'attachment_files': files, 'verification_method': 'MANUAL_ATTESTATION',
                             'sends_messages': False})
            return
        if route == '/api/reference-lookups':
            lookup = self.server.reference_lookup
            with _store(self.server.db_path) as store:
                reports = lookup.reports(store) if lookup.config.enabled else []
                candidates = lookup.candidates(store) if lookup.config.enabled else []
                questions = []
                if lookup.config.enabled:
                    for row in store.all('''SELECT q.id,q.current_version,q.context_revision,b.display_name,qv.payload,mv.verified_text
                        FROM questions q JOIN cases c ON c.id=q.case_id JOIN bindings b ON b.id=c.binding_id
                        JOIN question_versions qv ON qv.id=q.current_version
                        LEFT JOIN material_versions mv ON mv.id=qv.material_version ORDER BY q.rowid DESC LIMIT 100'''):
                        question = json.loads(row['payload'])
                        questions.append({'question_id': row['id'], 'question_version': row['current_version'],
                            'context_revision': row['context_revision'], 'label': row['display_name'] + ' · 第' + question['number'] + '题',
                            'student_question': question, 'student_material': row['verified_text']})
            self._json(200, {'enabled': lookup.config.enabled, 'shadow': lookup.config.shadow,
                'network_enabled': lookup.config.network_enabled, 'error': self.server.reference_lookup_error,
                'reports': reports, 'questions': questions, 'reference_candidates': candidates})
            return
        if route == "/api/performance":
            from datetime import datetime
            from .performance import PerformanceLedger
            from .performance_reports import PerformanceReports
            from .performance_rules import business_zone, question_business_date
            day = parse_qs(urlsplit(self.path).query).get("date", [question_business_date(datetime.now(business_zone())).isoformat()])[0]
            try:
                with _store(self.server.db_path) as store:
                    result = PerformanceReports(store, PerformanceLedger(store)).build(day)
                fields = ("day_composite_articles", "grammar_listening_actual_questions", "night_articles")
                complete = result.get("coverage", {}).get("complete") is True
                formal_totals = {key: result["summary"].get(key) if complete else None for key in fields}
                self._json(200, {"simulation": not bool(self.server.real_config) and self.server.processing_mode != "ACK_ONLY", "report": result,
                                 "formal_totals": formal_totals,
                                 "summary_scope": "CONFIRMED_LOCAL_SUBSET"})
            except ValueError:
                self._json(400, {"error": "请填写有效统计日期"})
            return
        if route == "/api/state":
            with _store(self.server.db_path) as store:
                from .workflow import Workflow
                dashboard = Workflow(store).dashboard()
                automatic_delivery = self.server.automatic_delivery.snapshot(store)
                automatic_answer = self.server.automatic_answer.snapshot(store)
            real = bool(self.server.real_config)
            ack_only = self.server.processing_mode == "ACK_ONLY"
            collector = self.server.collector_snapshot() if ack_only else None
            if ack_only:
                dashboard["messages"] = [row for row in dashboard.get("messages", []) if row.get("source", "").startswith("collector:")]
                message_ids = {row["id"] for row in dashboard["messages"]}
                dashboard["outbox"] = [row for row in dashboard.get("outbox", []) if row.get("purpose") == "ACK" and row.get("message_id") in message_ids]
                for field in ("questions", "answers", "runs", "reviews"):
                    dashboard[field] = []
            dashboard['simulation'] = not real and not ack_only
            self._json(200, {"application": "wecom-english-helpdesk", "simulation": not real and not ack_only, "mode": "ACK_ONLY" if ack_only else "REAL_PREPARED" if real else "SIMULATION",
                             "processing_mode": self.server.processing_mode, "collector": collector,
                             "performance_available": self.server.performance_available,
                             "source_review_enabled": self.server.source_review_enabled,
                             "teaching_blocked_reason": self.server.teaching_blocked_reason,
                             "question_auto_continue": self.server.question_auto_continue,
                             "automatic_delivery": automatic_delivery,
                             "automatic_answer": dict(automatic_answer,
                                 worker_alive=bool(self.server.answer_queue_thread and self.server.answer_queue_thread.is_alive()),
                                 worker_error_type=self.server.answer_queue_error),
                             "reviewed_queue": {"worker_alive": bool(self.server.reviewed_queue_thread
                                 and self.server.reviewed_queue_thread.is_alive()),
                                 "error_type": self.server.reviewed_queue_error},
                             "csrf_token": self.server.csrf_token, "dashboard": dashboard,
                             "jobs": self.server.job_snapshot(),
                             "test_delivery_available": real and not self.server.question_auto_continue and bool(self.server.real_config.get('pin')),
                             "allowed_actions": ["collector_start", "collector_stop", "resume", "stop"] if ack_only else ["resume", "stop"] if self.server.question_auto_continue or self.server.automatic_delivery.enabled else sorted(REAL_ACTIONS if real else ALLOWED_ACTIONS)})
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/worker-control.js": ("worker-control.js", "text/javascript; charset=utf-8"),
                 "/reference-lookup.js": ("reference-lookup.js", "text/javascript; charset=utf-8"),
                 "/manual-delivery.js": ("manual-delivery.js", "text/javascript; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        selected = files.get(route)
        if not selected:
            self._json(404, {"error": "页面不存在"})
            return
        data = (STATIC / selected[0]).read_bytes()
        self._headers(200, selected[1], len(data))
        self.wfile.write(data)

    def do_POST(self):
        if not self._host_valid():
            self._json(403, {"error": "主机地址检查未通过"})
            return
        route = urlsplit(self.path).path
        if route.startswith('/api/worker/'):
            self._worker_post(route)
            return
        if route not in ("/api/action", "/api/operator-tasks", '/api/reference-lookups', '/api/manual-deliveries', '/api/delivery/control'):
            self._json(404, {"error": "接口不存在"})
            return
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Origin") != expected_origin:
            self._json(403, {"error": "来源检查未通过"})
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf_token):
            self._json(403, {"error": "页面令牌无效"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "仅接受 JSON"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            maximum = 131072 if route == '/api/manual-deliveries' else 65536 if route in ('/api/operator-tasks', '/api/reference-lookups') else 1024
            if not 0 < size <= maximum:
                raise ValueError("请求大小无效")
            payload = json.loads(self.rfile.read(size))
            if route == '/api/delivery/control':
                if (not isinstance(payload, dict) or set(payload) not in ({'action'}, {'action', 'outbox_id'})
                        or payload.get('action') not in ('pause', 'resume', 'inspect', 'approve')
                        or ('outbox_id' in payload and payload['action'] not in ('inspect', 'approve'))):
                    raise ValueError('只接受暂停、恢复、审核或核验已有发送任务')
                with _store(self.server.db_path) as store:
                    if payload['action'] in ('pause', 'resume'):
                        self.server.automatic_answer.control(payload['action'])
                    result = self.server.automatic_delivery.control(store, payload['action'], outbox_id=payload.get('outbox_id'))
                self._json(200, {'result': result})
                return
            if route == '/api/manual-deliveries':
                from .manual_delivery import ManualDeliveries
                fields = {'original_outbox_id','question_version','context_revision','reviewer','verification_evidence',
                          'delivered_at','content','part_number','total_parts','attachments'}
                direct_fields = fields - {'original_outbox_id'} | {'turn_id'}
                source_fields = fields - {'delivered_at','content','attachments'} | {'collector_message_id','source_evidence_sha256','source_verified'}
                direct_source_fields = source_fields - {'original_outbox_id'} | {'turn_id'}
                identity = 'turn_id' if isinstance(payload, dict) and 'turn_id' in payload else 'original_outbox_id'
                if (not isinstance(payload, dict) or set(payload) not in (fields,direct_fields,source_fields,direct_source_fields)
                        or any(not isinstance(payload.get(k), str) or not 0 < len(payload[k]) <= 128
                               for k in (identity,'question_version'))):
                    raise ValueError('交付登记只接受已有任务、版本及人工核验记录')
                with _store(self.server.db_path) as store:
                    registry = ManualDeliveries(store, attachment_root=self.server.db_path.parent / 'delivery-attachments')
                    if 'collector_message_id' in payload:
                        result = registry.register_saved_reply(**payload)
                    else:
                        result = registry.register_for_turn(**payload) if identity == 'turn_id' else registry.register(**payload)
                self._json(200, {'result': result})
                return
            if route == '/api/reference-lookups':
                lookup = self.server.reference_lookup
                if not lookup.config.enabled:
                    self._json(409, {'error': '原题检索未启用，原答疑流程继续使用'})
                    return
                if not isinstance(payload, dict):
                    raise ValueError('原题核对请求格式无效')
                with _store(self.server.db_path) as store:
                    review_fields = {'action', 'candidate_id', 'question_version', 'context_revision', 'decision', 'reviewer', 'reason'}
                    if payload.get('action') == 'review_candidate':
                        if (set(payload) != review_fields or type(payload.get('context_revision')) is not int
                                or any(not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 128
                                       for key in ('candidate_id', 'question_version'))):
                            raise ValueError('候选审核仅接受已有候选、当前版本、核对人和具体依据')
                        result = lookup.review_candidate(store, payload['candidate_id'], question_version=payload['question_version'],
                            context_revision=payload['context_revision'], decision=payload['decision'],
                            reviewer=payload['reviewer'], reason=payload['reason'])
                        self._json(200, {'result': result})
                        return
                    if payload.get('action') == 'apply' and set(payload) == {'action', 'lookup_key', 'reviewer', 'reason'}:
                        comparison = lookup.apply(store, payload['lookup_key'], reviewer=payload['reviewer'], reason=payload['reason'])
                        self._json(200, {'result': {'reference_only': True, 'reason': comparison.reason}})
                        return
                    fields = {'action', 'question_id', 'question_version', 'context_revision', 'trigger', 'candidate_urls'}
                    if set(payload) not in (fields, fields | {'retry'}) or payload['action'] != 'lookup':
                        raise ValueError('原题核对只接受已有题目、版本、触发原因和候选网页')
                    if (type(payload['context_revision']) is not int or type(payload.get('retry', False)) is not bool
                            or any(not isinstance(payload[key], str) or len(payload[key]) > 128 for key in ('question_id', 'question_version', 'trigger'))
                            or not isinstance(payload['candidate_urls'], list)
                            or len(payload['candidate_urls']) > 6
                            or any(not isinstance(url, str) or len(url) > 3000 for url in payload['candidate_urls'])):
                        raise ValueError('原题核对请求超出范围')
                    result = lookup.run_for_question(store, payload['question_id'], payload['question_version'], payload['context_revision'],
                        trigger=payload['trigger'], candidate_urls=payload['candidate_urls'], retry=payload.get('retry', False))
                self._json(200, {'result': result})
                return
            if self.server.processing_mode == "ACK_ONLY":
                source_review = (route == '/api/operator-tasks' and self.server.source_review_enabled
                                 and isinstance(payload, dict) and payload.get('action') == 'review')
                if not source_review and (route == "/api/operator-tasks" or not isinstance(payload, dict) or payload.get("action") not in {"stop", "resume", "collector_start", "collector_stop"}):
                    self._json(403, {"error": "当前仅采集和排队收到，答疑、冻结、审核及答案发送入口已关闭", "processing_mode": "ACK_ONLY"})
                    return
            if route == '/api/operator-tasks':
                if isinstance(payload, dict) and payload.get('action') == 'generate':
                    if set(payload) != {'action', 'task_id'}:
                        raise ValueError('本地测试生成只接受任务编号')
                    status, result = self.server.start_operator_generation(payload['task_id'])
                    self._json(status, result)
                    return
                with _store(self.server.db_path) as store:
                    if self.server.source_review_enabled:
                        if (not isinstance(payload.get('draft_id'), str) or
                                not store.one("SELECT name FROM sqlite_master WHERE name='operator_drafts'") or
                                not store.one("SELECT id FROM operator_drafts WHERE id=? AND label='SOURCE_MESSAGE'",
                                              (payload['draft_id'],))):
                            self._json(403, {'error': '只允许确认已有可信原消息的题面'})
                            return
                    result = perform_operator(store, payload, self.server.operator_config)
                self._json(200, {'result': result})
                return
            if not isinstance(payload, dict) or set(payload) != {"action"} or not isinstance(payload["action"], str):
                raise ValueError("只允许预设演示动作")
            if payload['action'] in ('stop', 'resume'):
                self.server.automatic_answer.control('pause' if payload['action'] == 'stop' else 'resume')
            if self.server.automatic_delivery.enabled and payload['action'] in ('stop', 'resume'):
                with _store(self.server.db_path) as store:
                    result = self.server.automatic_delivery.control(store,
                        'pause' if payload['action'] == 'stop' else 'resume')
                self._json(200, {'result': result})
                return
            if self.server.automatic_delivery.enabled and payload['action'] not in ('collector_start', 'collector_stop'):
                self._json(403, {'error': '自动发送只处理已有可信任务；请使用收到与答案发送入口'})
                return
            if self.server.processing_mode == "ACK_ONLY" and payload["action"] in {"collector_start", "collector_stop"}:
                supervisor = self.server.collector_supervisor
                result = supervisor.start() if payload["action"] == "collector_start" else supervisor.stop()
                response = {"result": result}
                if result["state"] == "BLOCKED":
                    response["error"] = "采集未启动：授权或消息源配置仍待完成，请查看采集状态"
                self._json(409 if result["state"] == "BLOCKED" else 200, response)
                return
            if self.server.real_config:
                status, result = self.server.start_real_job(payload['action'])
                self._json(status, result)
                return
            with _store(self.server.db_path) as store:
                result = perform(store, payload["action"], self.server.db_path)
        except (ValueError, KeyError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:
            self._json(500, {"error": "演示动作失败，请查看本地日志"})
            return
        self._json(200, {"result": result})

    def _worker_authenticated(self):
        if not self.server.worker_api_token or not self.server.worker_token_worker_id:
            self._json(503,{'error':'WORKER_API_NOT_CONFIGURED'})
            return False
        if self.headers.get('Origin') is not None or not secrets.compare_digest(
                self.headers.get('Authorization',''), 'Bearer '+self.server.worker_api_token):
            self._json(403,{'error':'WORKER_AUTHENTICATION_REQUIRED'})
            return False
        return True

    def _worker_post(self, route):
        from .worker_coordinator import WorkerCoordinator
        from .worker_contracts import WorkerHealth,WorkerResult,strict_keys
        control_route=route=='/api/worker/control'
        if control_route:
            if (self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}' or
                    not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf_token)):
                self._json(403,{'error':'本机页面令牌或来源无效'})
                return
        elif not self._worker_authenticated():
            return
        if self.headers.get_content_type()!='application/json':
            self._json(415,{'error':'JSON_REQUIRED'})
            return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:
                raise ValueError('Bounded request required')
            value=json.loads(self.rfile.read(size))
            if not control_route and (not isinstance(value,dict) or value.get('worker_id')!=self.server.worker_token_worker_id):
                raise ValueError('Token does not authorize this Worker identity')
            with _store(self.server.db_path) as store:
                coordinator=WorkerCoordinator(store)
                if control_route:
                    if not isinstance(value,dict):raise ValueError('Control action required')
                    action=value.get('action')
                    if action in {'stop','request_takeover','confirm_takeover','request_resume','resume'}:
                        strict_keys(value,{'action','account_id'})
                        result={'stop':lambda:coordinator.set_stop(True,value['account_id']),
                            'request_takeover':lambda:coordinator.request_takeover(value['account_id']),
                            'confirm_takeover':lambda:coordinator.confirm_takeover(value['account_id']),
                            'request_resume':lambda:coordinator.request_resume(value['account_id']),
                            'resume':lambda:coordinator.resume_account(value['account_id'])}[action]()
                    elif action in {'cancel','verify'}:
                        strict_keys(value,{'action','command_id'})
                        result=coordinator.cancel(value['command_id']) if action=='cancel' else coordinator.verify(value['command_id'])
                    elif action=='mark_manually_handled':
                        strict_keys(value,{'action','command_id','evidence_ref'})
                        result=coordinator.mark_manually_handled(value['command_id'],value['evidence_ref'])
                    else:raise ValueError('Preset control action required')
                    self._json(200,{'result':result})
                    return
                if route=='/api/worker/heartbeat':
                    result=coordinator.heartbeat(WorkerHealth.from_dict(value))
                elif route=='/api/worker/pull':
                    strict_keys(value,{'worker_id'})
                    result=coordinator.pull(value['worker_id'])
                elif route=='/api/worker/authorize':
                    strict_keys(value,{'worker_id','command_id','lease_id','execution_epoch'})
                    result=coordinator.authorize(**value)
                elif route=='/api/worker/result':
                    strict_keys(value,{'worker_id','lease_id','result'})
                    result=coordinator.result(value['worker_id'],value['lease_id'],WorkerResult.from_dict(value['result']))
                else:
                    self._json(404,{'error':'BUSINESS_ACTION_NOT_ALLOWED'})
                    return
                self._json(200,result)
        except (ValueError,KeyError,TypeError):
            self._json(409,{'error':'执行或恢复条件尚未核验；保留当前任务，不自动重试'})
        except Exception:
            self._json(500,{'error':'WORKER_REQUEST_FAILED'})


class _store:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.store = Store(self.path)
        return self.store

    def __exit__(self, *_):
        self.store.close()


def main():
    parser = argparse.ArgumentParser(description="仅本机可访问的英语答疑工作台")
    parser.add_argument("--db", type=Path, default=Path("data/demo-ui.db"))
    parser.add_argument("--real-config", type=Path, help="本地已审核真实任务配置 JSON")
    parser.add_argument("--collector-config", type=Path, help="本地消息采集 TOML 配置，仅读取状态")
    parser.add_argument("--auto-start-collector", action="store_true", help="服务启动时同时启动已配置的增量原文导入/采集")
    parser.add_argument("--answer-review-root", type=Path, help="本地已保存真实回复的只读待审目录")
    parser.add_argument("--enable-performance", action="store_true", default=None,
                        help="打开本地日报入口；已有stage显式开关优先，不启用答疑或发送")
    parser.add_argument("--source-review-manifest", type=Path,
                        help="本人已审核的教学清单；只开放原消息题面确认和持久排队，不需要旧题生成记录")
    parser.add_argument('--reference-lookup-config', type=Path, help='可选原题核对TOML；默认关闭，不开启发送权限')
    parser.add_argument('--automatic-delivery-config', type=Path, help='本人核验的本地群发送JSON；复用现有Outbox和阶段发送策略')
    parser.add_argument('--automatic-answer-config', type=Path, help='可选本地Luna网页执行JSON；只推进已确认原题，默认关闭')
    parser.add_argument("--processing-mode", choices=("COMPATIBILITY", "ACK_ONLY"), default="COMPATIBILITY")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--worker-boundary',action='store_true',help='后台禁止直接操作桌面，采用固定Worker业务接口')
    parser.add_argument('--worker-policy',type=Path,help='固定Worker身份与动作/账号/范围白名单JSON')
    parser.add_argument('--worker-token-env',default='HELPDESK_WORKER_TOKEN',help='Worker认证令牌环境变量名，值不写入配置')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.auto_start_collector and (not args.collector_config or args.processing_mode != 'ACK_ONLY'):
        parser.error('--auto-start-collector requires --collector-config and ACK_ONLY')
    args.db.parent.mkdir(parents=True, exist_ok=True)
    server = DemoHTTPServer(("127.0.0.1", args.port), args.db, real_config=args.real_config,
        collector_config=args.collector_config, processing_mode=args.processing_mode,
        answer_review_root=args.answer_review_root,worker_boundary=args.worker_boundary,
        worker_policy=args.worker_policy,worker_token_env=args.worker_token_env,
        performance_enabled=args.enable_performance, source_review_manifest=args.source_review_manifest,
        reference_lookup_config=args.reference_lookup_config, automatic_delivery_config=args.automatic_delivery_config,
        automatic_answer_config=args.automatic_answer_config)
    print(f"{'收到与答案发送任务' if server.automatic_delivery.enabled else '消息采集 · 仅排队收到' if server.processing_mode == 'ACK_ONLY' else '真实已准备任务' if server.real_config else '模拟演示'}：http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        if args.auto_start_collector:
            server.collector_supervisor.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
