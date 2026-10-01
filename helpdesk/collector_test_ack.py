"""Only a collector ACK test copy may reach the operator-pinned WeCom contact.

The source ACK remains pending. PROGRESS is used solely to reuse Workflow's
restricted probe transport; the separate lineage table makes its meaning clear.
No case, teaching task or performance counting unit is created here.
"""
from hashlib import sha256
import json
from pathlib import Path

from .domain import new_id
from .storage import encode, now
from .test_routing import TestRecipient
from .workflow import Workflow
from .mcp_test_delivery import MCPTestAnswerDesktop


def _schema(store):
    with store.transaction():
        store.execute('''CREATE TABLE IF NOT EXISTS collector_test_ack_copies(
            test_outbox_id TEXT PRIMARY KEY REFERENCES outbox(id),
            source_outbox_id TEXT NOT NULL UNIQUE REFERENCES outbox(id),
            collector_message_id TEXT NOT NULL, source_binding_id TEXT NOT NULL,
            source_snapshot TEXT NOT NULL, target_key TEXT NOT NULL,
            target_verification TEXT NOT NULL, observation_mode TEXT NOT NULL,
            source_evidence_path TEXT NOT NULL, source_evidence_hash TEXT NOT NULL,
            created_at TEXT NOT NULL)''')


def _source(store, collector, source_outbox_id):
    source = store.one("SELECT * FROM outbox WHERE id=?", (source_outbox_id,))
    if not source or source["purpose"] != "ACK" or source["body"] != "收到" or source["state"] != "PENDING":
        raise ValueError("SOURCE_PENDING_COLLECTOR_ACK_REQUIRED")
    task = store.one("SELECT * FROM collector_answer_tasks WHERE business_message_id=? AND mode='LIVE' AND state='ACK_QUEUED'", (source["message_id"],))
    binding = store.one("SELECT * FROM bindings WHERE id=? AND verified=1", (source["binding_id"],))
    business_message = store.one("SELECT * FROM messages WHERE id=?", (source["message_id"],))
    if not task or not binding or not business_message or business_message["binding_id"] != binding["id"]:
        raise ValueError("VERIFIED_COLLECTOR_LINEAGE_REQUIRED")
    raw = collector.get_message(task["collector_message_id"])
    with collector.connect() as db:
        event = db.execute("SELECT * FROM events WHERE message_id=?", (task["collector_message_id"],)).fetchone()
        conflict = db.execute("SELECT 1 FROM message_conflicts WHERE message_id=?", (task["collector_message_id"],)).fetchone()
        fingerprint_collision = (db.execute("SELECT 1 FROM messages WHERE source_type=? AND fingerprint=? AND message_id!=? LIMIT 1",
            (raw["source_type"], raw["fingerprint"], raw["message_id"])).fetchone()
            if raw and raw["fingerprint"] else None)
    if not raw or not event or event["mode"] != "LIVE" or not event["auto_reply_allowed"] or conflict or fingerprint_collision:
        raise ValueError("COLLECTOR_EVENT_REQUIRES_REVIEW")
    payload = json.loads(raw["raw_payload"])
    if (raw["source_confidence"] != "high" or raw["time_confidence"] != "high" or raw["parse_status"] != "parsed"
            or not raw["sent_at_local"] or raw["room_id"] != binding["group_key"] or raw["sender_id"] != binding["student_key"]
            or business_message["raw_text"] != (raw["normalized_text"] or raw["raw_content"])
            or business_message["platform_id"] != (raw["source_message_id"] or raw["message_id"])
            or business_message["source"] != "collector:" + raw["source_type"]
            or business_message["source_sent_at"] != raw["sent_at_local"]
            or (raw["source_type"] == "windows_gui" and (not isinstance(payload, dict) or payload.get("identity_verified") is not True))):
        raise ValueError("SOURCE_IDENTITY_OR_TIME_NOT_VERIFIED")
    snapshot = encode({"collector_message": dict(raw), "source_binding": dict(binding),
                      "business_message_id": source["message_id"], "source_outbox_id": source["id"], "body": source["body"]})
    return source, raw, snapshot


def queue_test_ack(store, collector, source_outbox_id: str, recipient: TestRecipient, *,
                   source_evidence_path: str | Path, observation_mode: str = "FIXTURE") -> str:
    """ACTUAL requires operator-attested saved source evidence; fixtures stay simulated."""
    if not isinstance(recipient, TestRecipient) or observation_mode not in {"ACTUAL", "FIXTURE"}:
        raise ValueError("VERIFIED_TEST_TARGET_AND_EXPLICIT_OBSERVATION_MODE_REQUIRED")
    evidence = Path(source_evidence_path).resolve()
    evidence_hash = sha256(evidence.read_bytes()).hexdigest()
    _schema(store)
    with store.transaction():
        source, raw, snapshot = _source(store, collector, source_outbox_id)
        payload = json.loads(raw["raw_payload"])
        if observation_mode == "ACTUAL" and isinstance(payload, dict) and (payload.get("simulated") is True or payload.get("fixture") is True):
            raise ValueError("FIXTURE_CANNOT_BECOME_ACTUAL_SOURCE")
        if observation_mode == "ACTUAL":
            from .archive_capture_evidence import verify_archive_capture
            capture = verify_archive_capture(dict(raw), evidence)
            if capture.get("verified_actual") is not True or capture.get("sha256") != evidence_hash or capture.get("collector_message_id") != raw["message_id"]:
                raise ValueError("VERIFIED_NATIVE_SOURCE_CAPTURE_REQUIRED")
        prior = store.one("SELECT * FROM collector_test_ack_copies WHERE source_outbox_id=?", (source_outbox_id,))
        if prior:
            if (prior["target_key"], prior["observation_mode"], prior["source_snapshot"], prior["source_evidence_hash"]) != (recipient.stable_key, observation_mode, snapshot, evidence_hash):
                raise ValueError("TEST_ACK_ALREADY_FROZEN")
            return prior["test_outbox_id"]
        target = store.one("SELECT * FROM bindings WHERE group_key='wecom' AND student_key=?", (recipient.stable_key,))
        if target and (target["verified"] != 1 or target["display_name"] != "苇中鹤"):
            raise ValueError("TEST_TARGET_BINDING_UNVERIFIED")
        binding = target["id"] if target else new_id()
        if not target:
            store.execute("INSERT INTO bindings VALUES(?,?,?,?,?)", (binding, "wecom", recipient.stable_key, "苇中鹤", 1))
        if binding == source["binding_id"]:
            raise ValueError("TEST_DESTINATION_MUST_DIFFER_FROM_SOURCE")
        mid, oid = new_id(), new_id()
        store.execute('''INSERT INTO messages(id,binding_id,source,platform_id,observed_at,raw_text,
            attachments,fingerprint,intent,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (mid, binding, "collector_ack_test_copy", source_outbox_id, now(), "收到", "[]", evidence_hash, "IRRELEVANT", "TEST_ONLY", now()))
        store.execute('''INSERT INTO outbox(id,message_id,binding_id,purpose,body,idempotency_key,state,
            created_at,review_status,simulated) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (oid, mid, binding, "PROGRESS", "收到", "collector-test-ack:" + source_outbox_id, "PENDING", now(), "OPERATOR_AUTHORIZED", int(observation_mode == "FIXTURE")))
        store.execute("INSERT INTO collector_test_ack_copies VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (oid, source_outbox_id, raw["message_id"], source["binding_id"], snapshot, recipient.stable_key,
             recipient.verification_evidence, observation_mode, str(evidence), evidence_hash, now()))
        store.execute("INSERT INTO audit(event,outbox_id,details,created_at) VALUES(?,?,?,?)",
            ("COLLECTOR_ACK_TEST_COPY_QUEUED", oid, encode({"source_outbox_id": source_outbox_id,
             "collector_message_id": raw["message_id"], "source_student_delivered": False, "observation_mode": observation_mode}), now()))
        return oid


class CollectorTestAckWorkflow(Workflow):
    """Explicit one-ID dispatch, with source revalidation and no model invocation."""
    def __init__(self, store, collector, desktop):
        super().__init__(store, desktop=desktop)
        self.collector = collector
        if desktop.simulated is False and not isinstance(desktop, MCPTestAnswerDesktop):
            raise ValueError("PINNED_MCP_TEST_CONTACT_TRANSPORT_REQUIRED")

    def _validate(self, row, **kwargs):
        mapping = self.db.one("SELECT * FROM collector_test_ack_copies WHERE test_outbox_id=?", (row["id"],))
        if not mapping or row["body"] != "收到" or row["purpose"] != "PROGRESS" or row["turn_id"] or row["case_id"]:
            raise ValueError("ONLY_MAPPED_TEST_ACK_ALLOWED")
        _, raw, snapshot = _source(self.db, self.collector, mapping["source_outbox_id"])
        if snapshot != mapping["source_snapshot"] or sha256(Path(mapping["source_evidence_path"]).read_bytes()).hexdigest() != mapping["source_evidence_hash"]:
            raise ValueError("SOURCE_LINEAGE_CHANGED")
        if bool(row["simulated"]) != (mapping["observation_mode"] == "FIXTURE") or bool(row["simulated"]) != self.desktop.simulated:
            raise ValueError("FIXTURE_LIVE_TRANSPORT_MISMATCH")
        if mapping["observation_mode"] == "ACTUAL":
            from .archive_capture_evidence import verify_archive_capture
            capture = verify_archive_capture(dict(raw), Path(mapping["source_evidence_path"]))
            if capture.get("verified_actual") is not True or capture.get("sha256") != mapping["source_evidence_hash"] or capture.get("collector_message_id") != mapping["collector_message_id"]:
                raise ValueError("VERIFIED_NATIVE_SOURCE_CAPTURE_REQUIRED")
        bound = super()._validate(row, **kwargs)
        if (bound.group_key, bound.student_key) != ("wecom", mapping["target_key"]):
            raise ValueError("TEST_DESTINATION_CHANGED")
        if self.desktop.simulated is False:
            self.desktop.authorize(bound)
        return bound

    def dispatch(self, outbox_id=None):
        if not outbox_id or not self.db.one("SELECT 1 FROM collector_test_ack_copies WHERE test_outbox_id=?", (outbox_id,)):
            raise ValueError("EXPLICIT_MAPPED_TEST_ACK_ID_REQUIRED")
        return super().dispatch(outbox_id)

    def _record_check(self, row, evidence):
        evidence = dict(evidence)
        mapping = self.db.one("SELECT * FROM collector_test_ack_copies WHERE test_outbox_id=?", (row["id"],))
        if not mapping:
            raise ValueError("TEST_ACK_MAPPING_REQUIRED")
        if self.desktop.simulated is False and (evidence.get("target_platform") != "wecom" or evidence.get("target_key") != mapping["target_key"] or evidence.get("source_student_delivered") is not False):
            evidence["confirmed"] = False
        evidence.update(source_student_delivered=False, collector_message_id=mapping["collector_message_id"],
                        source_outbox_id=mapping["source_outbox_id"], observation_mode=mapping["observation_mode"])
        return super()._record_check(row, evidence)

    def recover(self):
        raise ValueError("USE_EXPLICIT_INSPECT_UNKNOWN_ONLY; NO_BULK_RECOVERY")

    def inspect_unknown(self, outbox_id):
        if not self.db.one("SELECT 1 FROM collector_test_ack_copies WHERE test_outbox_id=?", (outbox_id,)):
            raise ValueError("ONLY_MAPPED_TEST_ACK_ALLOWED")
        return super().inspect_unknown(outbox_id)
