"""Durable collector event -> case resolver queue, with no model or sending.

Task resolution is a separate business operation. History resolution disables
all outbox creation inside Helpdesk, including ACK and clarification messages.
"""
from dataclasses import replace, asdict, fields
from hashlib import sha256
import json
import re
from pathlib import Path
from .service import Helpdesk, Incoming, Outcome
from .storage import encode, now
from .message_sources import NormalizedMessage


class HistoryHelpdesk(Helpdesk):
    def _outbox(self, *args, **kwargs):
        self._audit("BACKFILL_REPLY_SUPPRESSED", details={"message_id": args[0], "purpose": args[2]})
        return None


class CollectorDispatcher:
    def __init__(self, collector_store, business_store, *, processing_mode="ACK_ONLY",
                 question_detector=None, self_sender_ids=(), teacher_sender_ids=()):
        if processing_mode not in {"ACK_ONLY", "CASE_RESOLUTION"}:
            raise ValueError("processing_mode must be ACK_ONLY or CASE_RESOLUTION")
        self.collector = collector_store
        self.business = business_store
        self.processing_mode = processing_mode
        self.question_detector = question_detector or self._question_request
        self.self_sender_ids = frozenset(self_sender_ids)
        self.teacher_sender_ids = frozenset(teacher_sender_ids)
        with self.business.transaction():
            self.business.execute('''CREATE TABLE IF NOT EXISTS collector_answer_tasks(
                id TEXT PRIMARY KEY, collector_message_id TEXT NOT NULL UNIQUE,
                mode TEXT NOT NULL, state TEXT NOT NULL, incoming_json TEXT,
                reason TEXT, business_message_id TEXT, created_at TEXT NOT NULL,
                resolved_at TEXT, decision_json TEXT)''')

    @staticmethod
    def _question_request(message):
        """Conservative provisional rule; verified student images count as requests."""
        if message.message_type == "image":
            return True
        if message.message_type != "text":
            return False
        text = message.normalized_text or message.raw_content
        return bool(re.search(r"(?:请问|求解|求讲解|帮我(?:看|讲|解)|怎么(?:做|解|选|理解)|为什么|为何|哪(?:个|项).*对|这(?:道)?题.*(?:不会|不懂)|老师.*(?:讲|解答|看一下))", text)
                    or re.search(r"(?:这(?:道)?题|第\s*\d+\s*(?:题|问)|选项).*(?:是否|能否|能不能|可不可以|能选|可以选|对吗|正确吗)", text, re.S))

    @staticmethod
    def source_attachments(message, *, promotion=False):
        """Retain collector metadata; expose only a locally verified original media file."""
        def value(key):
            return message[key] if hasattr(message, "keys") else getattr(message, key)
        if not value("media_id") and not value("local_media_path"):
            return []
        item = {"media_id": value("media_id"), "local_path": value("local_media_path"),
                "collector_message_id" if promotion else "message_id": value("message_id")}
        path, digest = value("local_media_path"), value("media_hash")
        if path and digest:
            file = Path(path)
            try:
                if file.is_file() and file.stat().st_size <= 25_000_000 and sha256(file.read_bytes()).hexdigest() == digest:
                    item.update(path=str(file.resolve()), sha256=digest,
                                provenance="collector original media: " + value("message_id"))
            except OSError:
                pass  # ACK does not imply that the question image is ready to teach.
        return [item]

    def _enqueue_ack_only(self, message, mode, auto_reply_allowed):
        identity = message.source_message_id or message.message_id
        task_id = sha256((message.source_type + ":" + identity).encode()).hexdigest()
        binding = self.business.one("SELECT * FROM bindings WHERE group_key=? AND student_key=? AND verified=1",
                                    (message.room_id, message.sender_id))
        raw = message.raw_payload if isinstance(message.raw_payload, dict) else {}
        role = str(raw.get("sender_role", raw.get("role", ""))).lower()
        reason = None
        if str(mode) != "LIVE":
            reason = "BACKFILL_ACK_FORBIDDEN"
        elif message.sender_id in self.self_sender_ids or raw.get("is_self") is True:
            reason = "SELF_MESSAGE_ACK_FORBIDDEN"
        elif message.sender_id in self.teacher_sender_ids or role in {"teacher", "staff", "老师", "教师"}:
            reason = "TEACHER_MESSAGE_ACK_FORBIDDEN"
        elif not binding or message.source_confidence != "high" or (message.source_type == "windows_gui" and raw.get("identity_verified") is not True):
            reason = "STUDENT_IDENTITY_REQUIRES_REVIEW"
        elif message.time_confidence != "high" or not message.sent_at_local:
            reason = "ORIGINAL_TIME_REQUIRES_REVIEW_ACK_HELD"
        elif not auto_reply_allowed or message.parse_status != "parsed":
            reason = "EVENT_POLICY_OR_CONFLICT_FORBIDS_ACK"
        elif self.question_detector(message) is not True:
            reason = "NO_VERIFIED_QUESTION_REQUEST"
        state = "ACK_QUEUED" if reason is None else "ACK_HELD"
        with self.business.transaction():
            existing_task = self.business.one("SELECT id FROM collector_answer_tasks WHERE id=?", (task_id,))
            if existing_task:
                return task_id
            mid = None
            if reason is None:
                source = "collector:" + message.source_type
                existing = self.business.one("SELECT id FROM messages WHERE binding_id=? AND source=? AND platform_id=?",
                                             (binding["id"], source, identity))
                mid = existing["id"] if existing else sha256(("ack-message:" + message.source_type + ":" + identity).encode()).hexdigest()
                attachments = self.source_attachments(message, promotion=True)
                evidence = {"source": "operator_verified_original" if message.source_type == "windows_gui" else "wecom_original",
                    "message_locator": identity, "evidence": {"collector_message_id": message.message_id,
                    "sent_at_raw": message.sent_at_raw}}
                if not existing:
                    self.business.execute('''INSERT INTO messages(id,binding_id,source,platform_id,observed_at,
                        raw_text,attachments,fingerprint,intent,status,created_at,source_sent_at,source_time_evidence)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (mid, binding["id"], source, identity, message.ingested_at,
                        message.normalized_text or message.raw_content, encode(attachments),
                        sha256(encode([message.raw_content, attachments]).encode()).hexdigest(),
                        "UNKNOWN", "ACK_ONLY", now(), message.sent_at_local, encode(evidence)))
                if not self.business.one("SELECT id FROM outbox WHERE idempotency_key=?", (mid + ":ACK",)):
                    Helpdesk(self.business)._outbox(mid, binding["id"], "ACK", "收到")
                self.business.execute("INSERT INTO audit(event,details,created_at) VALUES(?,?,?)",
                    ("COLLECTOR_ACK_ONLY_QUEUED", encode({"collector_message_id": message.message_id, "business_message_id": mid}), now()))
            self.business.execute('''INSERT INTO collector_answer_tasks
                (id,collector_message_id,mode,state,reason,business_message_id,created_at,decision_json)
                VALUES(?,?,?,?,?,?,?,?)''', (task_id, message.message_id, str(mode), state, reason, mid, now(),
                encode({"processing_mode": "ACK_ONLY", "question_detector": "injected" if self.question_detector != self._question_request else "conservative_rules", "reason": reason})))
        return task_id

    def enqueue(self, message, mode, *, auto_reply_allowed=True):
        if self.processing_mode == "ACK_ONLY":
            return self._enqueue_ack_only(message, mode, auto_reply_allowed)
        identity = message.source_message_id or message.message_id
        task_id = sha256((message.source_type + ":" + identity).encode()).hexdigest()
        trusted = (message.source_confidence == "high" and message.time_confidence == "high"
                   and bool(message.sent_at_local) and message.parse_status == "parsed")
        if message.source_type == "windows_gui":
            trusted = trusted and message.raw_payload.get("identity_verified") is True
        binding = self.business.one("SELECT * FROM bindings WHERE group_key=? AND student_key=? AND verified=1",
                                    (message.room_id, message.sender_id))
        trusted = trusted and bool(binding)
        if str(mode) == "LIVE" and not auto_reply_allowed:
            trusted = False
        state = ("PENDING_RESOLUTION" if str(mode) == "LIVE" else "HISTORY_ONLY") if trusted else "NEEDS_REVIEW"
        incoming = None
        if trusted:
            incoming = {"binding_id": binding["id"], "text": message.normalized_text or message.raw_content,
                "source": "collector:" + message.source_type, "platform_id": identity,
                "observed_at": message.ingested_at,
                "attachments": self.source_attachments(message), "source_sent_at": message.sent_at_local,
                "source_time_evidence": {"source": "operator_verified_original" if message.source_type == "windows_gui" else "wecom_original",
                    "message_locator": identity, "evidence": {"collector_message_id": message.message_id,
                        "source_type": message.source_type, "sent_at_raw": message.sent_at_raw}}}
        with self.business.transaction():
            self.business.execute('''INSERT OR IGNORE INTO collector_answer_tasks
                (id,collector_message_id,mode,state,incoming_json,reason,created_at)
                VALUES(?,?,?,?,?,?,?)''', (task_id, message.message_id, str(mode), state,
                encode(incoming) if incoming else None, None if trusted else "IDENTITY_OR_TIME_REQUIRES_REVIEW", now()))
        return task_id

    def drain(self, *, limit=1000):
        """Business commit precedes event acknowledgement; replay is idempotent."""
        with self.collector.connect() as db:
            events = db.execute("SELECT * FROM events WHERE processed_at IS NULL ORDER BY created_at,event_id LIMIT ?", (limit,)).fetchall()
        completed = 0
        names = {field.name for field in fields(NormalizedMessage)}
        for event in events:
            try:
                with self.collector.connect() as db:
                    # Keep source policy stable through the short business commit.
                    # A crash after business commit rolls back this acknowledgement;
                    # the next drain sees the existing deterministic business task.
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute("SELECT * FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
                    if not current or current["processed_at"] is not None:
                        continue
                    values = dict(db.execute("SELECT * FROM messages WHERE message_id=?", (current["message_id"],)).fetchone())
                    values["raw_payload"] = json.loads(values["raw_payload"])
                    values["sent_at_raw"] = json.loads(values["sent_at_raw"])
                    message = NormalizedMessage(**{k: v for k, v in values.items() if k in names})
                    conflict = db.execute("SELECT 1 FROM message_conflicts WHERE message_id=? LIMIT 1", (current["message_id"],)).fetchone()
                    self.enqueue(message, current["mode"], auto_reply_allowed=bool(current["auto_reply_allowed"]) and not conflict)
                    db.execute("UPDATE events SET processed_at=?,last_error=NULL WHERE event_id=?", (now(), event["event_id"]))
                completed += 1
            except Exception as error:
                with self.collector.connect() as db:
                    db.execute("UPDATE events SET last_error=? WHERE event_id=?", (type(error).__name__ + ": " + str(error), event["event_id"]))
                raise
        return completed

    def pending(self):
        return [dict(row) for row in self.business.all("SELECT * FROM collector_answer_tasks WHERE state!='RESOLVED' ORDER BY created_at,id")]

    @staticmethod
    def _receipt_transport(receipt):
        return {"binding_id": receipt["binding_id"], "text": receipt["raw_text"],
            "source": receipt["source"], "platform_id": receipt["platform_id"],
            "observation_id": receipt["observation_id"], "observed_at": receipt["observed_at"],
            "attachments": tuple(json.loads(receipt["attachments"])),
            "source_sent_at": receipt["source_sent_at"],
            "source_time_evidence": json.loads(receipt["source_time_evidence"])}

    def received_incoming(self, task_id, *, intent, **business_fields):
        """Build a reviewer decision using the unchanged original receipt.

        No intent inference, source verification, case creation or desktop action.
        The trusted reviewer supplies only business fields; resolve rechecks the
        collector source and policy. Callers cannot provide sender/time/transport.
        """
        task = self.business.one("SELECT * FROM collector_answer_tasks WHERE id=?", (task_id,))
        receipt = self.business.one("SELECT * FROM messages WHERE id=?", (task["business_message_id"],)) if task else None
        if not task or task["incoming_json"] or task["mode"] != "LIVE" or not receipt:
            raise ValueError("Original ACK_ONLY collector receipt required")
        base = self._receipt_transport(receipt)
        allowed = {field.name for field in fields(Incoming)} - set(base) - {"intent"}
        if set(business_fields) - allowed:
            raise ValueError("Only business decision fields may be supplied")
        return Incoming(**base, intent=intent, **business_fields)

    def _checked_source(self, db, task, base, *, promotion):
        """Shared source-policy check for resolution and later source review."""
        event = db.execute("SELECT mode,auto_reply_allowed FROM events WHERE message_id=?", (task["collector_message_id"],)).fetchone()
        source_row = db.execute("SELECT * FROM messages WHERE message_id=?", (task["collector_message_id"],)).fetchone()
        conflict = db.execute("SELECT 1 FROM message_conflicts WHERE message_id=? LIMIT 1", (task["collector_message_id"],)).fetchone()
        if conflict or not source_row:
            raise ValueError("Collector source conflict or missing original message")
        if self.business.one("SELECT id FROM source_conflicts WHERE message_id=? LIMIT 1", (task["business_message_id"],)):
            raise ValueError("Original receipt has an unresolved source conflict")
        raw = json.loads(source_row["raw_payload"])
        raw = raw if isinstance(raw, dict) else {}
        role = str(raw.get("sender_role", raw.get("role", ""))).lower()
        binding = self.business.one("SELECT * FROM bindings WHERE id=? AND verified=1", (base["binding_id"],))
        if (not binding or source_row["room_id"] != binding["group_key"] or source_row["sender_id"] != binding["student_key"]
                or source_row["sender_id"] in self.self_sender_ids or raw.get("is_self") is True
                or source_row["sender_id"] in self.teacher_sender_ids or role in {"teacher", "staff", "老师", "教师"}
                or source_row["source_confidence"] != "high" or source_row["time_confidence"] != "high"
                or source_row["parse_status"] != "parsed"
                or (source_row["source_type"] == "windows_gui" and raw.get("identity_verified") is not True)):
            raise ValueError("Original verified student source required")
        if (base["source"] != "collector:" + source_row["source_type"]
                or base["platform_id"] != (source_row["source_message_id"] or source_row["message_id"])
                or base["text"] != (source_row["normalized_text"] or source_row["raw_content"])
                or base["source_sent_at"] != source_row["sent_at_local"]
                or base["observed_at"] != source_row["ingested_at"]):
            raise ValueError("Original collector transport no longer matches receipt")
        evidence = base["source_time_evidence"]
        if (not isinstance(evidence, dict) or evidence.get("message_locator") != base["platform_id"]
                or evidence.get("source") != ("operator_verified_original" if source_row["source_type"] == "windows_gui" else "wecom_original")
                or not isinstance(evidence.get("evidence"), dict)
                or evidence["evidence"].get("collector_message_id") != task["collector_message_id"]
                or encode(evidence["evidence"].get("sent_at_raw")) != encode(json.loads(source_row["sent_at_raw"]))):
            raise ValueError("Original collector time evidence no longer matches receipt")
        expected_attachments = self.source_attachments(source_row, promotion=promotion)
        legacy_attachments = [{key: value for key, value in item.items() if key not in {"path", "sha256", "provenance"}}
                              for item in expected_attachments]
        if list(base["attachments"]) not in (expected_attachments, legacy_attachments):
            raise ValueError("Original collector attachments no longer match receipt")
        if not event or event["mode"] != task["mode"] or (task["mode"] == "LIVE" and not event["auto_reply_allowed"]):
            raise ValueError("Event policy forbids automatic reply resolution")
        return source_row

    def verified_received(self, task_id):
        """Read an already linked original receipt; never resolve or write data.

        The caller must have performed trusted case/version resolution. This
        does not infer intent or transform an ACK-only/history record into a case.
        """
        task = self.business.one("SELECT * FROM collector_answer_tasks WHERE id=?", (task_id,))
        if not task or task["mode"] != "LIVE" or task["state"] != "RESOLVED":
            raise ValueError("Resolved LIVE collector receipt required")
        receipt = self.business.one("SELECT * FROM messages WHERE id=?", (task["business_message_id"],))
        if (not receipt or receipt["status"] != "PROCESSED" or not receipt["source"].startswith("collector:")
                or not self.business.one("SELECT id FROM outbox WHERE message_id=? AND binding_id=? AND purpose='ACK'",
                                        (receipt["id"], receipt["binding_id"]))):
            raise ValueError("Linked original receipt and original ACK required")
        with self.collector.connect() as db:
            db.execute("BEGIN")  # WAL snapshot read: no source writer lock or persistent write.
            source = self._checked_source(db, task, self._receipt_transport(receipt), promotion=not task["incoming_json"])
        saved = json.loads(task["decision_json"])
        result = saved.get("result", {})
        turn = self.business.one("SELECT * FROM turns WHERE id=? AND message_id=?", (result.get("turn_id"), receipt["id"]))
        if (not turn or result.get("status") != "LINKED" or result.get("message_id") != receipt["id"]
                or result.get("case_id") != receipt["case_id"] or result.get("question_id") != receipt["question_id"]
                or turn["case_id"] != receipt["case_id"] or turn["question_id"] != receipt["question_id"]):
            raise ValueError("Original receipt resolution no longer matches its turn")
        return {"task": dict(task), "message": dict(receipt), "source": dict(source), "turn": dict(turn)}

    @classmethod
    def read_verified_received(cls, collector, business, task_id, *, self_sender_ids=(), teacher_sender_ids=()):
        """Same verification without constructor schema writes, for read boundaries."""
        reader = cls.__new__(cls)
        reader.collector, reader.business = collector, business
        reader.self_sender_ids, reader.teacher_sender_ids = frozenset(self_sender_ids), frozenset(teacher_sender_ids)
        return reader.verified_received(task_id)

    def resolve(self, task_id, decision: Incoming, *, reviewer, rationale, confidence):
        """Case resolver/reviewer supplies intent/version; transport fields cannot change."""
        if self.processing_mode == "ACK_ONLY":
            raise ValueError("ACK_ONLY disables case resolution and teaching")
        if (not isinstance(decision, Incoming) or not isinstance(reviewer, str) or not reviewer.strip()
                or not isinstance(rationale, str) or not rationale.strip()
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1):
            raise ValueError("Persisted resolver decision requires reviewer, rationale and confidence")
        with self.collector.connect() as db:
            # Same lock order as drain: source policy cannot change between this
            # recheck and the short business commit. Never hold it for model/UI work.
            db.execute("BEGIN IMMEDIATE")
            task = self.business.one("SELECT * FROM collector_answer_tasks WHERE id=?", (task_id,))
            if not task or (not task["incoming_json"] and not task["business_message_id"]):
                raise ValueError("Task requires identity/time review before business resolution")
            receipt = self.business.one("SELECT * FROM messages WHERE id=?", (task["business_message_id"],)) if task["business_message_id"] else None
            promotion = not task["incoming_json"] and receipt is not None
            if promotion:
                base = self._receipt_transport(receipt)
                if asdict(replace(decision, **base)) != asdict(decision):
                    raise ValueError("Received source transport fields cannot be replaced")
            else:
                base = json.loads(task["incoming_json"])
            if decision.binding_id != base["binding_id"]:
                raise ValueError("Resolver cannot change student binding")
            self._checked_source(db, task, base, promotion=promotion)
            decision_key = Helpdesk.received_decision_key(decision)
            if task["state"] == "RESOLVED":
                saved = json.loads(task["decision_json"])
                saved_key = saved.get("decision_key") or Helpdesk.received_decision_key(saved["decision"])
                if saved_key != decision_key:
                    raise ValueError("Task already resolved with a different decision; use correction workflow")
                if not promotion:
                    return Outcome(**saved["result"])
            incoming = replace(decision, text=base["text"], source=base["source"],
                platform_id=base["platform_id"], observation_id=base.get("observation_id"), observed_at=base["observed_at"],
                attachments=tuple(base["attachments"]), source_sent_at=base["source_sent_at"],
                source_time_evidence=base["source_time_evidence"])
            desk = Helpdesk(self.business) if task["mode"] == "LIVE" else HistoryHelpdesk(self.business)
            outcome = (desk.resolve_received_message(receipt["id"], incoming, reviewer=reviewer, rationale=rationale)
                       if promotion else desk.ingest(incoming))
            # Promotion records the exact outcome and decision in its own commit;
            # interruption here recovers that turn, without creating another ACK.
            if task["state"] != "RESOLVED":
                with self.business.transaction():
                    self.business.execute("UPDATE collector_answer_tasks SET state='RESOLVED',business_message_id=?,resolved_at=?,decision_json=? WHERE id=?",
                        (outcome.message_id, now(), encode({"reviewer": reviewer, "rationale": rationale,
                         "confidence": confidence, "decision": asdict(decision),
                         "decision_key": decision_key, "result": asdict(outcome)}), task_id))
            db.execute("UPDATE messages SET processed_at=COALESCE(processed_at,?) WHERE message_id=?", (now(), task["collector_message_id"]))
            return outcome
