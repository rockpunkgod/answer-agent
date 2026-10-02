"""Persistent generation/review/outbox workflow for the demo.

No model gets a send tool. The transport receives an immutable destination resolved
from the message's binding, after version and approval checks under a desktop lock.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Protocol

from .delivery import BoundMessage, MockDesktop, PreflightFailure, RetryablePreflightFailure, NotSubmitted
from .domain import Intent, new_id, normalize
from .locking import resource_lock
from .service import Helpdesk
from .storage import Store, encode, now
from .teaching_bundle import verify_bundle
from .test_answer_queue import validate_source_answer, validate_test_copy, test_copy_reconcile_bound


class GenerationAdapter(Protocol):
    """A generator returns a result dict; it has no delivery capability."""
    identity: str
    simulated: bool

    def generate(self, snapshot: dict) -> dict: ...


class DemoGenerationAdapter:
    identity = "MOCK_DEEPSEEK"
    simulated = True

    def generate(self, snapshot: dict) -> dict:
        selected = next((o for o in snapshot["student_question"]["options"] if o["verified_text"] == "To look after his mother."), None)
        result = {"simulated": True, "complete": True, "uploads_confirmed": not snapshot["attachments"],
                  "session_id": snapshot["session_id"], "correct_option_id": selected["id"] if selected else ""}
        if not selected:
            result.update(text="[模拟答复] 当前题不属于内置演示样题，请人工处理。", error="FIXTURE_UNSUPPORTED")
        elif re.search(r"\b(?:NOT|EXCEPT)\b", snapshot["student_question"]["verified_stem"], re.I):
            result.update(text="[模拟答复] 更正后的否定题不能沿用原演示答案，请重新核验。", error="RECHECK_REQUIRED")
        else:
            q = snapshot["student_question"]
            answer = f"[模拟答复，非真实 DeepSeek] 你这份第{q['number']}题选{selected['label']}，即 {selected['verified_text']}\n"
            if snapshot["intent"] == Intent.FOLLOWUP:
                b = next(o for o in q["options"] if o["label"] == "B")
                answer += f"你问的 B 在当前版本是 {b['verified_text']}。请对照原文目的，不要套用参考题的字母。"
            else:
                answer += "这是预设流程演示。正式讲解必须由 DeepSeek 按核验后的题目与教学 Skills 生成，再经审核。"
            result["text"] = answer
        return result


class Workflow:
    def __init__(self, store: Store, desktop=None, teaching_paths=(), *, teaching_manifest=None, generation_adapter: GenerationAdapter | None = None):
        self.db = store
        self.app = Helpdesk(store)
        self.desktop = desktop or MockDesktop(store.path + ".transport.db")
        self.generation_adapter = generation_adapter if generation_adapter is not None else DemoGenerationAdapter()
        if (not isinstance(getattr(self.generation_adapter, "identity", None), str)
                or not self.generation_adapter.identity.strip()
                or type(getattr(self.generation_adapter, "simulated", None)) is not bool
                or not callable(getattr(self.generation_adapter, "generate", None))):
            raise ValueError("Generation adapter requires identity, simulated, and generate(snapshot)")
        from .mcp_group_delivery import MCPGroupDesktop
        # Only the locally reviewed concrete group adapter may send formal text.
        if (self.desktop.simulated is not True and getattr(self.desktop, "test_only", False) is not True
                and not isinstance(self.desktop, MCPGroupDesktop)):
            raise ValueError("This demo cannot enable a real desktop sender")
        if teaching_manifest is not None and teaching_paths:
            raise ValueError("Choose a teaching manifest or explicit paths, not both")
        self.teaching_manifest = Path(teaching_manifest).resolve() if teaching_manifest is not None else None
        if not self.generation_adapter.simulated and self.teaching_manifest is None:
            raise ValueError("Real generation requires a verified teaching manifest")
        self.teaching_paths = tuple(Path(p).resolve() for p in teaching_paths) or (Path(__file__).parent / "teaching" / "demo-teaching.md",)

    def _event(self, event, *, outbox=None, run=None, details=None):
        row = self.db.one("SELECT * FROM outbox WHERE id=?", (outbox,)) if outbox else None
        rr = self.db.one("SELECT r.*,t.case_id FROM runs r JOIN turns t ON t.id=r.turn_id WHERE r.id=?", (run,)) if run else None
        self.db.execute("INSERT INTO audit(case_id,question_id,turn_id,run_id,outbox_id,event,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (row["case_id"] if row else rr["case_id"] if rr else None, rr["question_id"] if rr else None,
             row["turn_id"] if row else rr["turn_id"] if rr else None, run, outbox, event, encode(details or {}), now()))

    def _stopped(self):
        return self.db.one("SELECT value FROM settings WHERE key='stop_requested'")[0] == "true"

    def _manual_send_required(self):
        row = self.db.one("SELECT value FROM settings WHERE key='manual_send_required'")
        return bool(row and row[0] == "true")

    def _answer_review_required(self):
        row = self.db.one("SELECT value FROM settings WHERE key='answer_review_required'")
        # Existing databases and malformed values keep the human review gate.
        return not row or row[0] != "false"

    def set_answer_review_required(self, required: bool, *, actor="local_operator"):
        if type(required) is not bool:
            raise ValueError("Answer review policy must be boolean")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("Answer review policy requires an audit actor")
        value = "true" if required else "false"
        with self.db.transaction():
            old = self.db.one("SELECT value FROM settings WHERE key='answer_review_required'")
            if old and old[0] == value:
                return
            self.db.execute("""INSERT INTO settings(key,value) VALUES('answer_review_required',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (value,))
            self._event("ANSWER_REVIEW_POLICY_CHANGED", details={
                "old_value": old[0] if old else None, "answer_review_required": required,
                "actor": actor.strip()})

    def _delivery_policy_config(self):
        policy_row = self.db.one("SELECT value FROM settings WHERE key='delivery_policy'")
        if not policy_row:
            return None, None
        stage_row = self.db.one("SELECT value FROM settings WHERE key='delivery_stage_name'")
        try:
            policy = json.loads(policy_row[0])
        except (TypeError, json.JSONDecodeError):
            policy = {}
        if type(policy) is not dict:
            policy = {}
        return policy, stage_row[0] if stage_row else None

    def _delivery_mode(self, row):
        policy, _ = self._delivery_policy_config()
        if policy is not None:
            mode = policy.get(row["purpose"], "MANUAL")
            return mode if mode in ("AUTO", "MANUAL", "DISABLED") else "MANUAL"
        return "MANUAL" if self._manual_send_required() else "AUTO"

    def _delivery_block_reason(self, row):
        return {"MANUAL": "MANUAL_SEND_REQUIRED", "DISABLED": "DELIVERY_DISABLED"}.get(
            self._delivery_mode(row))

    def set_stop(self, stop: bool):
        if type(stop) is not bool:
            raise ValueError("Stop switch must be boolean")
        with self.db.transaction():
            self.db.execute("UPDATE settings SET value=? WHERE key='stop_requested'", ("true" if stop else "false",))
            self._event("STOP_CHANGED", details={"stopped": stop})

    def set_manual_send(self, required: bool):
        if type(required) is not bool:
            raise ValueError("Manual send policy must be boolean")
        value = "true" if required else "false"
        with self.db.transaction():
            self.db.execute("""INSERT INTO settings(key,value) VALUES('manual_send_required',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (value,))
            self._event("MANUAL_SEND_POLICY_CHANGED", details={"manual_send_required": required})

    def _ack_before_generation_required(self):
        row = self.db.one("SELECT value FROM settings WHERE key='require_ack_before_generation'")
        return bool(row and row[0] != "false")

    def set_require_ack_before_generation(self, required: bool, *, actor="local_operator"):
        if type(required) is not bool:
            raise ValueError("ACK-before-generation policy must be boolean")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("ACK-before-generation policy requires an audit actor")
        value = "true" if required else "false"
        with self.db.transaction():
            old = self.db.one("SELECT value FROM settings WHERE key='require_ack_before_generation'")
            if old and old[0] == value:
                return
            self.db.execute("""INSERT INTO settings(key,value) VALUES('require_ack_before_generation',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (value,))
            self._event("ACK_BEFORE_GENERATION_POLICY_CHANGED", details={
                "old_value": old[0] if old else None, "required": required, "actor": actor.strip()})

    def set_require_source_clarity_review(self, required: bool, *, actor="local_operator"):
        if type(required) is not bool or not isinstance(actor, str) or not actor.strip():
            raise ValueError("Source clarity policy requires a boolean and audit actor")
        value = "true" if required else "false"
        with self.db.transaction():
            old = self.db.one("SELECT value FROM settings WHERE key='require_source_clarity_review'")
            if old and old[0] == value:
                return
            self.db.execute("""INSERT INTO settings(key,value) VALUES('require_source_clarity_review',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (value,))
            self._event("SOURCE_CLARITY_POLICY_CHANGED", details={"required": required, "actor": actor.strip()})

    def _source_clarity_marker(self, turn_id):
        required = self.db.one("SELECT value FROM settings WHERE key='require_source_clarity_review'")
        message = self.db.one("SELECT m.source FROM turns t JOIN messages m ON m.id=t.message_id WHERE t.id=?", (turn_id,))
        if not required or required[0] != 'true' or not message or not message[0].startswith('collector:'):
            return None
        if not self.db.one("SELECT name FROM sqlite_master WHERE name='operator_tasks'"):
            raise ValueError('SOURCE_CLARITY_REQUIRED')
        task = self.db.one("SELECT id FROM operator_tasks WHERE turn_id=? AND label='SOURCE_MESSAGE'", (turn_id,))
        if not task:
            raise ValueError('SOURCE_CLARITY_REQUIRED')
        from .source_question_tasks import validate_reviewed_source_task
        return validate_reviewed_source_task(self.db, task[0])[-1]

    def _require_confirmed_ack(self, turn_id, *, require_real=False):
        if not self._ack_before_generation_required():
            return
        real_required = require_real or self.generation_adapter.simulated is False
        ack = self.db.one("""SELECT o.id FROM turns t JOIN messages m ON m.id=t.message_id
            JOIN bindings b ON b.id=m.binding_id JOIN outbox o ON o.message_id=m.id
            WHERE t.id=? AND o.binding_id=m.binding_id AND b.verified=1
            AND o.purpose='ACK' AND o.state='SENT_UI_CONFIRMED'
            AND (?=0 OR o.simulated=0) LIMIT 1""", (turn_id, int(real_required)))
        if not ack:
            raise ValueError("ACK_REQUIRED")

    def set_delivery_policy(self, policy: dict, stage_name: str):
        allowed_purposes = {
            "ACK", "ANSWER", "CORRECTION", "CLARIFICATION", "REQUEST_IMAGE", "PROGRESS", "TEST_ANSWER"
        }
        if type(policy) is not dict:
            raise ValueError("Delivery policy must be a dictionary")
        if any(type(purpose) is not str or purpose not in allowed_purposes for purpose in policy):
            raise ValueError("Delivery policy contains an unsupported purpose")
        if any(type(mode) is not str or mode not in ("AUTO", "MANUAL", "DISABLED")
               for mode in policy.values()):
            raise ValueError("Delivery policy modes must be AUTO, MANUAL, or DISABLED")
        if type(stage_name) is not str or not stage_name.strip():
            raise ValueError("Delivery stage name must be a non-empty string")
        normalized = {purpose: policy[purpose] for purpose in sorted(policy)}
        with self.db.transaction():
            old_policy, old_stage = self._delivery_policy_config()
            self.db.execute("""INSERT INTO settings(key,value) VALUES('delivery_policy',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (encode(normalized),))
            self.db.execute("""INSERT INTO settings(key,value) VALUES('delivery_stage_name',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (stage_name.strip(),))
            self._event("DELIVERY_POLICY_CHANGED", details={
                "old_policy": old_policy, "new_policy": normalized,
                "old_stage_name": old_stage, "new_stage_name": stage_name.strip(),
            })

    def _skills(self):
        entries = []
        manifest = verify_bundle(self.teaching_manifest,
            for_generation=not self.generation_adapter.simulated) if self.teaching_manifest else None
        if manifest is not None and manifest['answer_generation_allowed_by_course'] is not True:
            raise ValueError("Teaching policy does not authorize answer generation")
        paths = [Path(p) for p in manifest['workflow_teaching_paths']] if manifest else self.teaching_paths
        expected = {item['snapshot_path']: item['snapshot_sha256'] for item in manifest['files']} if manifest else {}
        for path in paths:
            if path.suffix.lower() != ".md" or not path.is_file() or path.stat().st_size > 1_000_000:
                raise ValueError("Invalid teaching file in operator allowlist")
            content = path.read_bytes()
            if manifest and sha256(content).hexdigest() != expected[str(path)]:
                raise ValueError("Teaching snapshot changed after verification")
            entries.append({"name": path.name, "path": str(path), "sha256": sha256(content).hexdigest(),
                            "content": content.decode("utf-8"),
                            "source": "verified_teaching_manifest" if manifest else "operator_allowlist",
                            **({"manifest_path": str(self.teaching_manifest.resolve()),
                                "reviewed_policy_id": manifest['reviewed_policy_id'],
                                "question_type": manifest['question_type'],
                                **({'required_task_checks': manifest['required_task_checks']}
                                   if manifest.get('format_version') == 3 else {})} if manifest else {})})
        return entries

    def start(self, turn_id):
        with self.db.transaction():
            if self._stopped():
                raise ValueError("STOPPED")
            if self.db.one("SELECT id FROM outbox WHERE turn_id=? AND state='CANCELLED' AND last_error='MANUALLY_DELIVERED'", (turn_id,)):
                raise ValueError('MANUALLY_DELIVERED')
            self._require_confirmed_ack(turn_id)
            snapshot = self.app.context(turn_id)
            source_review = self._source_clarity_marker(turn_id)
            if source_review is not None:
                snapshot['source_clarity_review'] = source_review
            adapter = self.generation_adapter
            if not adapter.simulated and self.teaching_manifest is None:
                raise ValueError("Real generation requires a verified teaching manifest")
            skills = self._skills()
            if not adapter.simulated and (not skills or any(s["source"] != "verified_teaching_manifest" for s in skills)):
                raise ValueError("Real generation requires a verified teaching manifest")
            active = self.db.one("SELECT id FROM runs WHERE question_id=? AND state='RUNNING'", (snapshot["question_id"],))
            if active:
                raise ValueError("A generation is already running for this question")
            binding_id = self.db.one("SELECT binding_id FROM cases WHERE id=?", (snapshot["case_id"],))[0]
            session = self.db.one("""SELECT s.* FROM sessions s JOIN session_owners o ON o.session_id=s.id
                WHERE s.case_id=? AND o.binding_id=? AND o.question_id=? AND s.state='ACTIVE'
                ORDER BY s.rowid DESC LIMIT 1""", (snapshot["case_id"], binding_id, snapshot["question_id"]))
            changed_problem = False
            if session and snapshot["intent"] == Intent.CORRECTION:
                previous = self.db.one("SELECT input_json FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session["id"],))
                if previous:
                    prior = json.loads(previous[0])
                    def problem_content(context):
                        q = context['student_question']
                        return (normalize(q.get('verified_stem') or ''), normalize(context.get('student_material') or ''),
                                q.get('kind'), q.get('visual_evidence'),
                                sorted(normalize(o.get('verified_text') or '') for o in q['options']))
                    changed_problem = problem_content(prior) != problem_content(snapshot)
            if session and (changed_problem or session["adapter"] != adapter.identity):
                self.db.execute("UPDATE sessions SET state='REPLACED' WHERE id=?", (session["id"],))
                session = None
            if not session:
                sid = new_id()
                self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, snapshot["case_id"], adapter.identity, "ACTIVE", now()))
                self.db.execute("INSERT INTO session_owners VALUES(?,?,?,?)", (sid, binding_id, snapshot["question_id"], now()))
            else:
                sid = session["id"]
            snapshot["session_id"] = sid
            snapshot["binding_id"] = binding_id
            snapshot["session_store_path"] = str(Path(self.db.path).resolve())
            snapshot["teaching_skills"] = skills
            snapshot["generation_adapter"] = adapter.identity
            snapshot["simulated"] = adapter.simulated
            snapshot["simulation"] = adapter.simulated
            snapshot["run_id"] = rid = new_id()
            if any(s.get('required_task_checks') for s in skills):
                from .lesson_checks import check_lesson
                snapshot['teaching_source_check'] = check_lesson(snapshot)
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, turn_id, snapshot["question_id"], snapshot["question_version"], snapshot["context_revision"],
                             sid, encode(snapshot), "RUNNING", None, now(), None))
            if snapshot.get('teaching_source_check'):
                self._event('TEACHING_SOURCE_CHECKED', run=rid, details=snapshot['teaching_source_check'])
            self._event("GENERATION_STARTED", run=rid, details={"adapter": adapter.identity, "simulated": adapter.simulated,
                "skills": [{"name": s["name"], "sha256": s["sha256"]} for s in skills]})
            return rid

    def _human(self, message_id, reason):
        exists = self.db.one("SELECT id FROM human_tasks WHERE message_id=? AND reason=? AND state='OPEN'", (message_id, reason))
        if not exists:
            self.db.execute("INSERT INTO human_tasks(id,message_id,reason,created_at) VALUES(?,?,?,?)", (new_id(), message_id, reason, now()))

    def finish(self, run_id, result: dict):
        with self.db.transaction():
            run = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
            if not run:
                raise ValueError("Unknown run")
            prior = self.db.one("SELECT * FROM outbox WHERE run_id=? AND purpose IN ('ANSWER','CORRECTION')", (run_id,))
            if prior:
                return {"answer_id": prior["answer_id"], "outbox_id": prior["id"], "state": prior["state"]}
            saved = self.db.one("SELECT a.id,a.state FROM answers a JOIN answer_evidence e ON e.answer_id=a.id WHERE e.run_id=?", (run_id,))
            if saved:
                return {"answer_id": saved["id"], "outbox_id": None, "state": saved["state"], "reason": run["error"]}
            if run["state"] not in ("RUNNING", "STALE"):
                return {"state": run["state"], "reason": run["error"], "answer_id": None, "outbox_id": None}
            snapshot = json.loads(run["input_json"])
            simulated = snapshot.get("simulated", True)
            adapter_identity = snapshot.get("generation_adapter", "MOCK_DEEPSEEK")
            session = self.db.one("""SELECT s.*,o.question_id AS owned_question,o.binding_id,
                c.binding_id AS case_binding FROM sessions s
                LEFT JOIN session_owners o ON o.session_id=s.id JOIN cases c ON c.id=s.case_id
                WHERE s.id=?""", (run["session_id"],))
            current = self.db.one("SELECT * FROM questions WHERE id=?", (run["question_id"],))
            stale = run["state"] == "STALE" or (current["current_version"], current["context_revision"]) != (run["question_version"], run["context_revision"])
            error = result.get("error")
            from .reference_resolution import validate_reference_snapshot
            try:
                validate_reference_snapshot(self.db, snapshot)
            except ValueError:
                error = error or 'REFERENCE_CONFIRMATION_CHANGED'
            if result.get("complete") is not True or not str(result.get("text", "")).strip():
                error = error or "INCOMPLETE_OUTPUT"
            if result.get("uploads_confirmed") is not True:
                error = error or "UPLOAD_UNCONFIRMED"
            if result.get("session_id") != run["session_id"]:
                error = error or "SESSION_MISMATCH"
            if (not session or session['case_id'] != snapshot['case_id']
                    or session['owned_question'] != run['question_id']
                    or session['binding_id'] != session['case_binding']
                    or snapshot.get('session_id') != run['session_id']):
                error = error or "SESSION_OWNERSHIP_MISMATCH"
            if (result.get("simulated") is not simulated
                    or result.get("adapter") not in ((None, adapter_identity) if simulated else (adapter_identity,))
                    or not session or session["adapter"] != adapter_identity):
                error = error or "ADAPTER_MODE_MISMATCH"
            if not simulated:
                expected = {s["path"]: s["sha256"] for s in snapshot["teaching_skills"]}
                if result.get("run_id") != run_id:
                    error = error or "RUN_MISMATCH"
                if not isinstance(result.get("web_session_evidence"), str) or not result["web_session_evidence"].strip():
                    error = error or "WEB_SESSION_EVIDENCE_MISSING"
                if (not expected or len(expected) != len(snapshot["teaching_skills"])
                        or result.get("uploaded_teaching_hashes") != expected):
                    error = error or "TEACHING_UPLOAD_MISMATCH"
            option = next((o for o in snapshot["student_question"]["options"] if o["id"] == result.get("correct_option_id")), None)
            if not option:
                error = error or "OPTION_VERSION_MISMATCH"
            else:
                # A claimed answer letter must match the selected content. Follow-up mentions
                # (e.g. '不选B') are not affirmative answer claims.
                claims = re.findall(r"(?<!不)选\s*([A-D])", str(result.get("text", "")))
                if any(letter != option["label"] for letter in claims):
                    error = error or "ANSWER_LABEL_MISMATCH"
            if not stale:
                try:
                    current_context = self.app.context(run["turn_id"])
                    if adapter_identity == 'WINDOWS_MCP_PREPARED_DEEPSEEK' and not simulated:
                        from .mcp_generation import input_fingerprint
                        if input_fingerprint(current_context) != input_fingerprint(snapshot):
                            error = error or 'GENERATION_CONTEXT_CHANGED'
                except ValueError:
                    error = error or "UNRESOLVED_INPUT"
            text = str(result.get("text", ""))
            if simulated and "[模拟答复，非真实 DeepSeek]" not in text:
                text = "[模拟答复，非真实 DeepSeek] " + text
            previous = snapshot.get("sent_history", [])
            correction = bool(previous and (previous[-1]["question_version"] != run["question_version"] or snapshot["intent"] == Intent.DISPUTE))
            if simulated and correction and not stale and not error:
                text = "更正/复核说明：以下按本轮题目重新核验，之前回答仅作历史记录。\n" + text
            if (not stale and not error and not simulated
                    and any(s.get('required_task_checks') for s in snapshot.get('teaching_skills', []))):
                from .question_matching import validate_receipt
                try:
                    validate_receipt(self.db, snapshot)
                except (ValueError, OSError):
                    error = 'QUESTION_MATCH_CHECK_FAILED'
                    self._event('QUESTION_MATCH_RECHECK_FAILED', run=run_id, details={'reason': error})
            if not stale and not error and any(s.get('required_task_checks') for s in snapshot.get('teaching_skills', [])):
                from .lesson_checks import check_lesson
                try:
                    checked = check_lesson(snapshot, draft=text)
                    self._event('TEACHING_DRAFT_CHECKED', run=run_id, details=checked)
                    if checked['status'] != 'NO_AUTOMATIC_FLAGS':
                        error = 'ANSWER_DRAFT_CHECK_REQUIRES_REVIEW'
                except (ValueError, OSError) as exc:
                    from .lesson_checks import LessonCheckError
                    error = 'ANSWER_DRAFT_CHECK_FAILED'
                    self._event('TEACHING_DRAFT_CHECK_FAILED', run=run_id,
                                details={'reason': error, 'check_error': str(exc) if isinstance(exc, LessonCheckError) else type(exc).__name__})
            state = "STALE" if stale else "REJECTED" if error else "GENERATED"
            revision = self.db.one("SELECT COALESCE(MAX(answer_revision),0)+1 FROM answers WHERE question_id=?", (run["question_id"],))[0]
            aid = new_id()
            self.db.execute("INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (aid, run["turn_id"], run["question_id"], run["question_version"], run["context_revision"], revision, text,
                             state, adapter_identity, now()))
            self.db.execute("INSERT INTO answer_evidence VALUES(?,?,?,?,?,?,?)",
                            (aid, run_id, str(result.get("correct_option_id", "")), int(result.get("complete") is True),
                             int(result.get("uploads_confirmed") is True), str(result.get("session_id", "")), int(simulated)))
            self.db.execute("UPDATE runs SET state=?,error=?,completed_at=? WHERE id=?", (state, error, now(), run_id))
            oid = None
            turn = self.db.one("SELECT * FROM turns WHERE id=?", (run["turn_id"],))
            if state == "GENERATED":
                binding = self.db.one("SELECT binding_id FROM messages WHERE id=?", (turn["message_id"],))[0]
                oid = new_id()
                self.db.execute("""INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
                    context_revision,idempotency_key,state,created_at,answer_id,run_id,simulated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (oid, turn["message_id"], turn["case_id"], turn["id"], binding, "CORRECTION" if correction else "ANSWER", text,
                     run["question_version"], run["context_revision"], "answer:" + aid, "PENDING", now(), aid, run_id, int(simulated)))
                self.db.execute("UPDATE questions SET status='AWAITING_REVIEW' WHERE id=?", (run["question_id"],))
                if not self._answer_review_required():
                    row = self.db.one("SELECT * FROM outbox WHERE id=?", (oid,))
                    try:
                        self._validate(row, approval=False, transport=False)
                    except ValueError as exc:
                        state, error = "STALE", str(exc)
                        self.db.execute("UPDATE outbox SET state='STALE',review_status='INVALIDATED',last_error=? WHERE id=?", (error, oid))
                        self.db.execute("UPDATE answers SET state='STALE' WHERE id=?", (aid,))
                        self.db.execute("UPDATE runs SET state='STALE',error=? WHERE id=?", (error, run_id))
                        self.db.execute("UPDATE questions SET status='REVIEW' WHERE id=?", (run["question_id"],))
                        self._human(turn["message_id"], "ANSWER_CHECK_FAILED:" + error)
                    else:
                        self.db.execute("UPDATE outbox SET review_status='NOT_REQUIRED' WHERE id=?", (oid,))
                        if self._delivery_mode(row) == "MANUAL":
                            self.db.execute("UPDATE questions SET status='AWAITING_MANUAL_SEND' WHERE id=?", (run["question_id"],))
                        else:
                            self.db.execute("UPDATE questions SET status='READY' WHERE id=?", (run["question_id"],))
                        self._event("ANSWER_REVIEW_NOT_REQUIRED", outbox=oid, run=run_id,
                                    details={"answer_review_required": False, "delivery_mode": self._delivery_mode(row)})
            elif state == "REJECTED":
                self._human(turn["message_id"], error)
                self.db.execute("UPDATE questions SET status='REVIEW' WHERE id=?", (run["question_id"],))
            self._event("GENERATION_FINISHED", run=run_id, outbox=oid, details={"state": state, "reason": error,
                "answer_revision": revision, "adapter": adapter_identity, "simulated": simulated})
            return {"answer_id": aid, "outbox_id": oid, "state": state, "reason": error}

    def generate(self, turn_id):
        # Repeated UI requests for the same frozen turn are retries, not a new answer revision.
        self._require_confirmed_ack(turn_id)
        current = self.app.context(turn_id)
        existing = self.db.one("""SELECT o.* FROM outbox o JOIN runs r ON r.id=o.run_id
            WHERE r.turn_id=? AND r.question_version=? AND r.context_revision=? AND r.state='GENERATED'
            AND o.purpose IN ('ANSWER','CORRECTION')
            ORDER BY o.rowid DESC LIMIT 1""", (turn_id, current["question_version"], current["context_revision"]))
        if existing:
            return {"answer_id": existing["answer_id"], "outbox_id": existing["id"], "state": existing["state"]}
        prior = self.db.one("""SELECT * FROM runs WHERE turn_id=? AND question_version=? AND context_revision=?
            ORDER BY rowid DESC LIMIT 1""", (turn_id, current["question_version"], current["context_revision"]))
        if prior:
            return {"answer_id": None, "outbox_id": None, "state": prior["state"], "reason": prior["error"]}
        rid = self.start(turn_id)
        snapshot = json.loads(self.db.one("SELECT input_json FROM runs WHERE id=?", (rid,))[0])
        self._require_confirmed_ack(turn_id)
        try:
            result = self.generation_adapter.generate(snapshot)
            if not isinstance(result, dict):
                raise TypeError("Generation adapter must return a result dict")
        except Exception:
            # The external call may have committed remotely. Never invoke it again on retry.
            with self.db.transaction():
                self.db.execute("UPDATE runs SET state='REJECTED',error='GENERATION_UNCERTAIN',completed_at=? WHERE id=?", (now(), rid))
                turn = self.db.one("SELECT message_id FROM turns WHERE id=?", (turn_id,))
                self._human(turn["message_id"], "GENERATION_UNCERTAIN")
                self.db.execute("UPDATE questions SET status='REVIEW' WHERE id=?", (snapshot["question_id"],))
                self._event("GENERATION_FINISHED", run=rid, details={"state": "REJECTED", "reason": "GENERATION_UNCERTAIN",
                    "adapter": snapshot["generation_adapter"], "simulated": snapshot["simulated"]})
            return {"answer_id": None, "outbox_id": None, "state": "REJECTED", "reason": "GENERATION_UNCERTAIN"}
        return self.finish(rid, result)

    def _bound(self, row):
        source = self.db.one("SELECT binding_id FROM messages WHERE id=?", (row["message_id"],))
        binding = self.db.one("SELECT * FROM bindings WHERE id=?", (source[0],)) if source else None
        if not binding or not binding["verified"] or binding["id"] != row["binding_id"]:
            raise ValueError("RECIPIENT_BINDING_MISMATCH")
        if row["case_id"]:
            case = self.db.one("SELECT binding_id FROM cases WHERE id=?", (row["case_id"],))
            if not case or case[0] != binding["id"]:
                raise ValueError("CASE_BINDING_MISMATCH")
        return BoundMessage(row["id"], binding["id"], binding["group_key"], binding["student_key"], row["body"])

    def _validate(self, row, *, approval=True, transport=True):
        if row["purpose"] == "TEST_ANSWER":
            if not transport or self.desktop.simulated is not False or getattr(self.desktop, "test_only", False) is not True or getattr(self.desktop, "test_answer_transport", False) is not True:
                raise ValueError("TEST_ANSWER_TRANSPORT_DISABLED")
            bound = validate_test_copy(self.db, row)
            self.desktop.authorize(bound)
            return bound
        bound = self._bound(row)
        if transport and row['purpose'] in ('ANSWER', 'CORRECTION'):
            from .delivery_batches import part_bound
            bound = part_bound(self.db, row, bound)
        if transport and self.desktop.simulated:
            if row["simulated"] != 1:
                raise ValueError("REAL_SEND_DISABLED")
        elif transport and self.desktop.simulated is False:
            from .mcp_group_delivery import MCPGroupDesktop
            if isinstance(self.desktop, MCPGroupDesktop):
                if row['simulated'] != 0 or row['purpose'] not in ('ACK', 'ANSWER', 'CORRECTION'):
                    raise ValueError('ACTUAL_GROUP_DELIVERY_REQUIRED')
                receipt = self.db.one('SELECT source FROM messages WHERE id=?', (row['message_id'],))
                if not receipt or not receipt['source'].startswith('collector:'):
                    raise ValueError('ACTUAL_STUDENT_SOURCE_REQUIRED')
                if row['purpose'] != 'ACK':
                    if (not self.db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_tasks'")
                            or not self.db.one("SELECT id FROM operator_tasks WHERE turn_id=? AND label='SOURCE_MESSAGE'", (row['turn_id'],))):
                        raise ValueError('REVIEWED_ORIGINAL_STUDENT_TASK_REQUIRED')
                self.desktop.authorize(bound)
            elif (row["simulated"] != 0 or row["purpose"] != "PROGRESS" or row["answer_id"] or row["review_status"] != "OPERATOR_AUTHORIZED"):
                # The optional smoke-test bridge remains limited to operator probes.
                raise ValueError("ONLY_OPERATOR_TEST_PROBES_ALLOWED")
        if row["purpose"] in ("ANSWER", "CORRECTION"):
            validate_source_answer(self.db, row, approval=approval and self._answer_review_required())
        elif row["purpose"] not in ("ACK", "CLARIFICATION", "REQUEST_IMAGE", "PROGRESS"):
            raise ValueError("UNSUPPORTED_PURPOSE")
        return bound

    def approve(self, outbox_id, reviewer="local_demo_operator"):
        with self.db.transaction():
            row = self.db.one("SELECT * FROM outbox WHERE id=?", (outbox_id,))
            if not row or row["state"] != "PENDING" or row["purpose"] not in ("ANSWER", "CORRECTION"):
                raise ValueError("Only pending answers can be approved")
            bound = self._validate(row, approval=False, transport=False)
            answer = self.db.one("SELECT answer_revision FROM answers WHERE id=?", (row["answer_id"],))[0]
            self.db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)",
                (new_id(), row["id"], row["question_version"], row["context_revision"], answer, bound.body_hash, reviewer, "APPROVED", now()))
            self.db.execute("UPDATE outbox SET review_status='APPROVED' WHERE id=?", (row["id"],))
            self._event("ANSWER_APPROVED", outbox=row["id"], run=row["run_id"])

    def _record_check(self, row, evidence, *, simulated=None, complete_turn=True):
        expected_simulation = self.desktop.simulated if simulated is None else simulated
        if type(expected_simulation) is not bool:
            raise ValueError('Delivery simulation mode must be explicit')
        from .delivery_batches import read_plan, record_part
        if read_plan(self.db, row):
            partial, aggregate = record_part(self, row, evidence, expected_simulation)
            if aggregate is None:
                return partial
            evidence = aggregate
        confirmed = (evidence.get("confirmed") is True and evidence.get("simulated") is expected_simulation
                     and evidence.get("body_hash") == sha256(row["body"].encode()).hexdigest())
        if row["purpose"] == "TEST_ANSWER":
            target = self.db.one("SELECT target_platform,target_key FROM test_answer_copies WHERE test_outbox_id=?", (row["id"],))
            confirmed = bool(confirmed and target
                and evidence.get("target_platform") == target["target_platform"]
                and evidence.get("target_key") == target["target_key"]
                and evidence.get("source_student_delivered") is False)
        state = "SENT_UI_CONFIRMED" if confirmed else "SEND_UNKNOWN"
        self.db.execute("UPDATE outbox SET state=?,sent_at=?,last_error=? WHERE id=?",
            (state, evidence.get("confirmed_at", now()) if confirmed else None, None if confirmed else "NO_UNAMBIGUOUS_UI_EVIDENCE", row["id"]))
        self.db.execute("INSERT INTO delivery_checks VALUES(?,?,?,?,?)", (new_id(), row["id"], state, encode(evidence), now()))
        if not confirmed:
            prefix = "TEST_SEND_UNKNOWN:" if row["purpose"] == "TEST_ANSWER" else "SEND_UNKNOWN:"
            self._human(row["message_id"], prefix + row["id"])
        if confirmed and complete_turn and row["turn_id"] and row["purpose"] in ("ANSWER", "CORRECTION"):
            question = self.db.one("SELECT question_id FROM turns WHERE id=?", (row["turn_id"],))[0]
            current = self.db.one("SELECT current_version,context_revision FROM questions WHERE id=?", (question,))
            if tuple(current) != (row["question_version"], row["context_revision"]):
                self._human(row["message_id"], "DELIVERED_OLD_VERSION_RECHECK:" + row["id"])
            self.db.execute("UPDATE questions SET status='WAITING_FOLLOWUP' WHERE id=? AND current_version=? AND context_revision=?",
                (question, row["question_version"], row["context_revision"]))
        self._event(state, outbox=row["id"], run=row["run_id"], details={"simulated": expected_simulation})
        return state

    def stage_manual_answer(self, outbox_id, *, adapter, semantic_decision_id=None):
        """Trusted desktop actor stages this exact Outbox; the human finally sends.

        The adapter owns the desktop lock and rechecks the immutable authority
        before each gesture. Never hold a business write transaction over UI work.
        """
        from .manual_group_draft import ManualGroupDraft
        if not isinstance(adapter, ManualGroupDraft):
            raise ValueError('Trusted manual group draft adapter required')
        with self.db.transaction():
            row = self.db.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
            from .delivery_batches import START_EVENT
            if self.db.one('SELECT 1 FROM audit WHERE outbox_id=? AND event=?', (outbox_id, START_EVENT)):
                raise ValueError('DELIVERY_BATCH_STARTED_USE_PART_RECONCILIATION')
            if not row or row['purpose'] not in ('ANSWER', 'CORRECTION') or row['simulated'] != 0:
                raise ValueError('Real original answer Outbox required')
            if self._stopped():
                return {'status': 'STOPPED', 'answer_sent': False}
            if self._delivery_mode(row) != 'MANUAL':
                return {'status': 'MANUAL_FINAL_SEND_STAGE_REQUIRED', 'answer_sent': False}
            if row['state'] != 'PENDING':
                return {'status': row['state'], 'answer_sent': row['state'] == 'SENT_UI_CONFIRMED', 'replayed': False}
            self._validate(row, transport=False)
            frozen = json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?', (row['run_id'],))[0])
            fixed_decision = frozen.get('source_clarity_review', {}).get('semantic_decision_id')
            if semantic_decision_id is not None and semantic_decision_id != fixed_decision:
                raise ValueError('Manual draft must use the frozen shared semantic decision')
            semantic_decision_id = fixed_decision
            old = self.db.one("SELECT id FROM audit WHERE event='MANUAL_DRAFT_REQUESTED' AND outbox_id=?", (outbox_id,))
            if not old:
                self._event('MANUAL_DRAFT_REQUESTED', outbox=outbox_id, run=row['run_id'],
                            details={'semantic_decision_id': semantic_decision_id,
                                     'body_hash': sha256(row['body'].encode()).hexdigest(),
                                     'binding_id': row['binding_id'], 'question_version': row['question_version'],
                                     'context_revision': row['context_revision'], 'action': 'STAGE_OUTBOX'})
        try:
            result = adapter.stage_outbox(self.db, outbox_id, semantic_decision_id=semantic_decision_id)
        except BaseException:
            with self.db.transaction():
                self._event('MANUAL_DRAFT_OUTCOME_UNCONFIRMED', outbox=outbox_id, run=row['run_id'],
                            details={'automatic_retry_allowed': False})
            raise
        if result.get('status') == 'DRAFT_VERIFIED_AWAITING_HUMAN_SEND':
            with self.db.transaction():
                self._event('MANUAL_DRAFT_VERIFIED', outbox=outbox_id, run=row['run_id'],
                            details={'journal': result['journal'], 'body_hash': sha256(row['body'].encode()).hexdigest(),
                                     'semantic_decision_id': semantic_decision_id, 'answer_sent': False})
        return result

    def _project_verified_manual_counting(self, row, *, original=None):
        """Resume deterministic counting from the frozen shared decision, never a UI draft."""
        from .collector_storage import CollectorStore
        from .performance import MATERIAL_TYPES, PerformanceLedger
        from .semantic_decisions import SharedSemanticDecisions
        source = original if original is not None else row
        run = self.db.one('SELECT input_json FROM runs WHERE id=?', (source['run_id'],))
        if not run:
            from .source_question_tasks import SEMANTIC_BOUND_EVENT
            bindings = self.db.all('SELECT details FROM audit WHERE event=? AND turn_id=?',
                                   (SEMANTIC_BOUND_EVENT, source['turn_id']))
            if len(bindings) != 1:
                return 'NO_SHARED_COUNTING_RELATION'
            relation = json.loads(bindings[0]['details'])
            marker = {'semantic_decision_id': relation.get('decision_id'), 'counting_unit_id': relation.get('unit_id'),
                      'draft_id': relation.get('draft_id')}
        else:
            frozen = json.loads(run[0])
            marker = frozen.get('source_clarity_review', {})
        decision_id, unit_id = marker.get('semantic_decision_id'), marker.get('counting_unit_id')
        if not decision_id or not unit_id:
            return 'NO_SHARED_COUNTING_RELATION'
        link = self.db.one('SELECT * FROM source_question_drafts WHERE draft_id=?', (marker.get('draft_id'),))
        if not link:
            return 'SOURCE_COUNTING_LINK_MISSING'
        collector = CollectorStore.__new__(CollectorStore)
        collector.path = link['collector_path']
        shared = SharedSemanticDecisions(self.db, collector, **json.loads(link['sender_filters']))
        try:
            origin = shared.answer_input(decision_id)
            if (origin['message_id'] != row['message_id'] or origin['turn_id'] != row['turn_id']
                    or origin['question_version'] != row['question_version']
                    or origin['context_revision'] != row['context_revision']
                    or origin['question_type'] not in MATERIAL_TYPES
                    or shared.counting_unit(decision_id) != unit_id):
                return 'SHARED_COUNTING_RELATION_CHANGED'
            ledger = PerformanceLedger(self.db)
            unit = ledger._row(unit_id)
            if (unit['status'] == 'CONFIRMED' and unit['completion_outbox_id'] == row['id']
                    and ledger.delivery_eligibility(unit)['eligible']):
                return 'CONFIRMED'
            ledger.record_delivery(unit_id, row['id'])
            # One frozen verified Question is actually answered by this Outbox.
            # Material/night units use their configured piece rule; no message
            # counts or unapproved daytime conversions are substituted.
            ledger.confirm(unit_id, reviewer='verified_manual_delivery',
                           evidence='Shared decision and verified original Outbox ' + row['id'],
                           actual_question_count=1)
            return 'CONFIRMED'
        except ValueError:
            return 'COUNTING_RECHECK_REQUIRED'

    def verify_manual_delivery(self, outbox_id, *, adapter):
        """Observe a human send and persist its real receipt; never sends or pastes.

        Stop/changed policy may block new drafts, but cannot erase an actual
        historical delivery. Stale-version delivery is recorded for rechecking.
        """
        from .manual_group_draft import ManualGroupDraft
        if not isinstance(adapter, ManualGroupDraft):
            raise ValueError('Trusted manual group draft observer required')
        row = self.db.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
        if (not row or row['purpose'] not in ('ANSWER', 'CORRECTION') or row['simulated'] != 0
                or row['state'] not in ('PENDING', 'SENDING', 'SEND_UNKNOWN', 'SENT_UI_CONFIRMED', 'STALE')):
            raise ValueError('Original manual answer Outbox required')
        self._bound(row)
        from .delivery_batches import START_EVENT
        if self.db.one('SELECT 1 FROM audit WHERE outbox_id=? AND event=?', (outbox_id, START_EVENT)):
            raise ValueError('DELIVERY_BATCH_STARTED_USE_PART_RECONCILIATION')
        if row['state'] != 'SENT_UI_CONFIRMED':
            if not self.db.one("SELECT id FROM audit WHERE event='MANUAL_DRAFT_REQUESTED' AND outbox_id=?",
                               (outbox_id,)):
                raise ValueError('Manual draft authorization checkpoint required before observing delivery')
            evidence = adapter.readback_outbox(self.db, outbox_id)
            if evidence.get('confirmed') is True:
                bound = self._bound(row)
                if (evidence.get('outbox_id') != row['id'] or evidence.get('binding_id') != row['binding_id']
                        or evidence.get('group_key') != bound.group_key or evidence.get('student_key') != bound.student_key
                        or evidence.get('sender_role') != 'TEACHER' or not evidence.get('message_locator')
                        or not evidence.get('confirmed_at') or not evidence.get('evidence_paths')):
                    raise ValueError('Manual receipt must match the original teacher delivery')
            with self.db.transaction():
                current = self.db.one('SELECT * FROM outbox WHERE id=?', (outbox_id,))
                if any(current[key] != row[key] for key in ('binding_id', 'body', 'run_id', 'answer_id', 'turn_id',
                                                           'message_id', 'question_version', 'context_revision')):
                    raise ValueError('Original Outbox changed during receipt observation')
                if current['state'] != 'SENT_UI_CONFIRMED':
                    state = self._record_check(current, evidence, simulated=False)
                else:
                    state = current['state']
        else:
            state = row['state']
        if state == 'SENT_UI_CONFIRMED':
            return {'state': state, 'counting_status': self._project_verified_manual_counting(row)}
        return {'state': state, 'counting_status': 'NOT_DELIVERED'}

    def dispatch(self, outbox_id=None):
        # Only short preflight/send/readback operations own this lock. Generation never does.
        with resource_lock(self.desktop.lock_path):
            with self.db.transaction():
                if self._stopped():
                    return "STOPPED"
                if outbox_id:
                    row = self.db.one("SELECT * FROM outbox WHERE id=?", (outbox_id,))
                else:
                    row = self.db.one("""SELECT * FROM outbox WHERE state='PENDING'
                        AND (purpose NOT IN ('ANSWER','CORRECTION') OR review_status='APPROVED' OR ?=0)
                        ORDER BY CASE purpose WHEN 'ACK' THEN 0 WHEN 'CLARIFICATION' THEN 1 WHEN 'REQUEST_IMAGE' THEN 1 ELSE 2 END,created_at,rowid LIMIT 1""", (int(self._answer_review_required()),))
                if not row:
                    return "EMPTY"
                if row["state"] != "PENDING":
                    return row["state"]
                blocked = self._delivery_block_reason(row)
                if blocked:
                    return blocked
                if row["purpose"] == "TEST_ANSWER" and not (self.desktop.simulated is False and getattr(self.desktop, "test_only", False) is True and getattr(self.desktop, "test_answer_transport", False) is True):
                    return "TEST_ANSWER_TRANSPORT_DISABLED"
                try:
                    if row['purpose'] in ('ANSWER', 'CORRECTION'):
                        self._validate(row, transport=False)
                        from .delivery_batches import ensure_plan
                        ensure_plan(self, row)
                    bound = self._validate(row)
                except (ValueError, PreflightFailure) as exc:
                    if str(exc) == "MANUAL_REVIEW_REQUIRED":
                        return "AWAITING_REVIEW"
                    self.db.execute("UPDATE outbox SET state='STALE',review_status='INVALIDATED',last_error=? WHERE id=?", (str(exc), row["id"]))
                    if row["purpose"] != "TEST_ANSWER":
                        self._human(row["message_id"], "SEND_CHECK_FAILED:" + str(exc))
                    return "STALE"
            # UI reads must not block original-message ingestion or an operator pause.
            try:
                self.desktop.preflight(bound)
            except PreflightFailure as exc:
                with self.db.transaction():
                    self.db.execute("UPDATE outbox SET state='FAILED',last_error=? WHERE id=?", (str(exc), row["id"]))
                    retryable = isinstance(exc, RetryablePreflightFailure)
                    self._event('SEND_PREFLIGHT_FAILED', outbox=row['id'], run=row['run_id'],
                                details={'reason': str(exc), 'retryable': retryable, 'submission_attempted': False})
                    if row["purpose"] != "TEST_ANSWER" and not retryable:
                        self._human(row["message_id"], str(exc))
                return "FAILED"
            with self.db.transaction():
                current = self.db.one('SELECT * FROM outbox WHERE id=?', (row['id'],))
                if current['state'] != 'PENDING':
                    return current['state']
                self.db.execute("UPDATE outbox SET state='SENDING' WHERE id=?", (row["id"],))
                from .delivery_batches import start_part
                start_part(self, row)
                self._event("SEND_STARTED", outbox=row["id"], run=row["run_id"])
            # Commit SENDING before the side effect. A crash from here onwards is never retried.
            with self.db.transaction():
                current = self.db.one("SELECT * FROM outbox WHERE id=?", (row["id"],))
                blocked = self._delivery_block_reason(current)
                if blocked:
                    self.db.execute("UPDATE outbox SET state='PENDING',last_error=? WHERE id=?", (blocked, row["id"]))
                    self._event("DELIVERY_POLICY_BLOCKED", outbox=row["id"], run=row["run_id"],
                                details={"phase": "pre_send", "reason": blocked,
                                         "mode": self._delivery_mode(current)})
                    return blocked
                try:
                    if self._stopped():
                        self.db.execute("UPDATE outbox SET state='CANCELLED',last_error='STOPPED_BEFORE_SEND' WHERE id=?", (row["id"],))
                        return "CANCELLED"
                    self._validate(current)
                except (ValueError, PreflightFailure):
                    self.db.execute("UPDATE outbox SET state='STALE',review_status='INVALIDATED' WHERE id=?", (row["id"],))
                    return "STALE"
            # SENDING is durable, while the database remains available during UI work.
            try:
                evidence = self.desktop.send(bound)
            except NotSubmitted as exc:
                with self.db.transaction():
                    evidence = {"submission_attempted": False, "draft_may_remain": True,
                                "simulated": self.desktop.simulated, "reason": str(exc)}
                    self.db.execute("UPDATE outbox SET state='FAILED',last_error=? WHERE id=?", (str(exc), row["id"]))
                    self.db.execute("INSERT INTO delivery_checks VALUES(?,?,?,?,?)", (new_id(), row["id"], "FAILED", encode(evidence), now()))
                    if row["purpose"] != "TEST_ANSWER":
                        self._human(row["message_id"], "UNSENT_DRAFT_REVIEW:" + row["id"])
                    self._event("SEND_NOT_SUBMITTED", outbox=row["id"], run=row["run_id"], details=evidence)
                return "FAILED"
            except Exception:
                # Side-effect exceptions are not retryable; raw exception may contain private data.
                evidence = {"confirmed": False, "simulated": self.desktop.simulated, "reason": "SEND_EXCEPTION"}
            with self.db.transaction():
                state = self._record_check(current, evidence)
            if state == 'SENT_UI_CONFIRMED' and current['purpose'] in ('ANSWER', 'CORRECTION') and self.desktop.simulated is False:
                counting = self._project_verified_manual_counting(current)
                with self.db.transaction():
                    self._event('VERIFIED_DELIVERY_COUNTING', outbox=row['id'], run=row['run_id'], details={'state': counting})
            return state

    def recover(self):
        recovered = []
        with resource_lock(self.desktop.lock_path):
            for row in self.db.all("SELECT * FROM outbox WHERE state='SENDING'"):
                with self.db.transaction():
                    try:
                        bound = test_copy_reconcile_bound(self.db, row) if row["purpose"] == "TEST_ANSWER" else self._bound(row)
                        from .delivery_batches import part_bound
                        bound = part_bound(self.db, row, bound, reconcile=True)
                        evidence = self.desktop.reconcile(bound)
                    except Exception:
                        evidence = {"confirmed": False, "simulated": self.desktop.simulated, "reason": "RECOVERY_CHECK_FAILED"}
                    state = self._record_check(row, evidence)
                    recovered.append({"outbox_id": row["id"], "state": state})
            if self.desktop.simulated is False:
                for result in recovered:
                    if result['state'] == 'SENT_UI_CONFIRMED':
                        row = self.db.one('SELECT * FROM outbox WHERE id=?', (result['outbox_id'],))
                        if row['purpose'] in ('ANSWER', 'CORRECTION'):
                            self._project_verified_manual_counting(row)
        return recovered

    def inspect_unknown(self, outbox_id):
        """Explicit operator-requested, read-only check of a previously uncertain send."""
        with resource_lock(self.desktop.lock_path):
            row = self.db.one("SELECT * FROM outbox WHERE id=?", (outbox_id,))
            if not row or row["state"] not in ("SENDING", "SEND_UNKNOWN"):
                raise ValueError("Only uncertain sends can be inspected")
            try:
                bound = test_copy_reconcile_bound(self.db, row) if row["purpose"] == "TEST_ANSWER" else self._bound(row)
                from .delivery_batches import part_bound
                bound = part_bound(self.db, row, bound, reconcile=True)
                evidence = self.desktop.reconcile(bound)
            except Exception:
                evidence = {"confirmed": False, "simulated": self.desktop.simulated, "reason": "RECOVERY_CHECK_FAILED"}
            with self.db.transaction():
                state = self._record_check(row, evidence)
                if state in ("SENT_UI_CONFIRMED", "PART_DELIVERED", "STALE"):
                    prefix = "TEST_SEND_UNKNOWN:" if row["purpose"] == "TEST_ANSWER" else "SEND_UNKNOWN:"
                    self.db.execute("UPDATE human_tasks SET state='RESOLVED' WHERE message_id=? AND reason=?",
                        (row["message_id"], prefix + row["id"]))
            if state == 'SENT_UI_CONFIRMED' and row['purpose'] in ('ANSWER', 'CORRECTION') and self.desktop.simulated is False:
                self._project_verified_manual_counting(row)
            return state

    def dashboard(self):
        health = self.app.health()
        health["mode"] = "SIMULATED_DEMO"
        health["stopped"] = self._stopped()
        health["manual_send_required"] = self._manual_send_required()
        health["answer_review_required"] = self._answer_review_required()
        policy, stage_name = self._delivery_policy_config()
        health["delivery_policy"] = policy
        health["delivery_stage_name"] = stage_name
        from .message_sla import message_sla
        health["sla"] = message_sla(self.db)
        return {"simulation": True, "health": health, "outbox_by_state": health["outbox_by_state"], **{table: [dict(r) for r in self.db.all(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 250")]
                                    for table in ("messages", "questions", "answers", "outbox", "reviews", "runs", "human_tasks", "bindings")}}
