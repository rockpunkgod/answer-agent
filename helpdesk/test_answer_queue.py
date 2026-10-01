"""Operator-only test copies of approved answers. This module never sends."""

from hashlib import sha256
import json

from .delivery import BoundMessage
from .domain import new_id
from .service import Helpdesk
from .storage import now
from .test_routing import TestRecipient, TEST_DISPLAY_NAME, TEST_PLATFORM


TEST_PREFIX = "【测试答案副本，仅发苇中鹤，不代表已回复学生，编号"
LEGACY_BODY_FORMAT = "legacy-prefixed-v1"
PLAIN_BODY_FORMAT = "plain-source-v2"


def render_test_body(test_outbox_id: str, source_body: str, body_format: str = PLAIN_BODY_FORMAT) -> str:
    if body_format == LEGACY_BODY_FORMAT:
        return f"{TEST_PREFIX}{test_outbox_id[:12]}】 {source_body}"
    if body_format == PLAIN_BODY_FORMAT:
        return source_body
    raise ValueError("TEST_BODY_FORMAT_INVALID")


def validate_source_answer(store, row, *, approval=True):
    """Validate the frozen answer and, when requested, its manual review."""
    if not row or row["purpose"] not in ("ANSWER", "CORRECTION"):
        raise ValueError("SOURCE_ANSWER_REQUIRED")
    message = store.one("SELECT binding_id FROM messages WHERE id=?", (row["message_id"],))
    binding = store.one("SELECT * FROM bindings WHERE id=?", (message[0],)) if message else None
    if not binding or not binding["verified"] or binding["id"] != row["binding_id"]:
        raise ValueError("RECIPIENT_BINDING_MISMATCH")
    case = store.one("SELECT binding_id FROM cases WHERE id=?", (row["case_id"],))
    if not case or case[0] != binding["id"]:
        raise ValueError("CASE_BINDING_MISMATCH")
    bound = BoundMessage(row["id"], binding["id"], binding["group_key"], binding["student_key"], row["body"])
    context = Helpdesk(store).context(row["turn_id"])
    if (context["question_version"], context["context_revision"]) != (row["question_version"], row["context_revision"]):
        raise ValueError("STALE_VERSION")
    answer = store.one("SELECT * FROM answers WHERE id=?", (row["answer_id"],))
    evidence = store.one("SELECT * FROM answer_evidence WHERE answer_id=?", (row["answer_id"],))
    if (not answer or answer["state"] != "GENERATED" or answer["text"] != row["body"]
            or answer["turn_id"] != row["turn_id"] or answer["question_version"] != row["question_version"]
            or answer["context_revision"] != row["context_revision"]):
        raise ValueError("ANSWER_INVALID")
    if not evidence or not evidence["complete"] or not evidence["uploads_confirmed"] or evidence["simulated"] != row["simulated"]:
        raise ValueError("ANSWER_EVIDENCE_MISSING")
    if evidence["correct_option_id"] not in {o["id"] for o in context["student_question"]["options"]}:
        raise ValueError("OPTION_VERSION_MISMATCH")
    if store.one("SELECT name FROM sqlite_master WHERE name='operator_tasks'"):
        source_task = store.one("SELECT id,run_id FROM operator_tasks WHERE turn_id=? AND label='SOURCE_MESSAGE'", (row['turn_id'],))
        if source_task:
            from .source_question_tasks import validate_reviewed_source_task
            marker = validate_reviewed_source_task(store, source_task['id'])[-1]
            run = store.one('SELECT input_json FROM runs WHERE id=?', (row['run_id'],))
            if (source_task['run_id'] != row['run_id'] or not run
                    or json.loads(run[0]).get('source_clarity_review') != marker):
                raise ValueError('ORIGINAL_SOURCE_REVIEW_MISMATCH')
    if approval:
        review = store.one("SELECT * FROM reviews WHERE outbox_id=? AND status='APPROVED' ORDER BY rowid DESC LIMIT 1", (row["id"],))
        if not review or row["review_status"] != "APPROVED":
            raise ValueError("MANUAL_REVIEW_REQUIRED")
        if (review["question_version"], review["context_revision"], review["answer_revision"], review["body_hash"]) != (
                row["question_version"], row["context_revision"], answer["answer_revision"], bound.body_hash):
            raise ValueError("APPROVAL_STALE")
    return bound, answer


def validate_test_copy(store, row):
    mapping = store.one("SELECT * FROM test_answer_copies WHERE test_outbox_id=?", (row["id"],))
    if not mapping or row["purpose"] != "TEST_ANSWER" or row["simulated"] != 0:
        raise ValueError("TEST_COPY_MAPPING_MISSING")
    source = store.one("SELECT * FROM outbox WHERE id=?", (mapping["source_outbox_id"],))
    if not source or source["state"] != "PENDING" or source["simulated"] != 0:
        raise ValueError("TEST_SOURCE_INVALID")
    source_bound, answer = validate_source_answer(store, source)
    if (mapping["target_platform"] != TEST_PLATFORM or mapping["target_display_name"] != TEST_DISPLAY_NAME
            or not mapping["target_key"] or mapping["target_key"] == TEST_DISPLAY_NAME):
        raise ValueError("TEST_TARGET_INVALID")
    if (mapping["source_body_hash"] != source_bound.body_hash
            or mapping["question_version"] != source["question_version"]
            or mapping["context_revision"] != source["context_revision"]
            or mapping["answer_revision"] != answer["answer_revision"]
            or row["body"] != render_test_body(row["id"], source["body"], mapping["body_format"])
            or row["answer_id"] != source["answer_id"] or row["run_id"] != source["run_id"]
            or row["binding_id"] != source["binding_id"] or row["message_id"] != source["message_id"]
            or row["turn_id"] != source["turn_id"] or row["case_id"] != source["case_id"]
            or row["question_version"] != source["question_version"]
            or row["context_revision"] != source["context_revision"]):
        raise ValueError("TEST_COPY_STALE")
    return BoundMessage(row["id"], source["binding_id"], TEST_PLATFORM, mapping["target_key"], row["body"])


def test_copy_reconcile_bound(store, row):
    """Rebuild the frozen destination after a possible send, even if source went stale."""
    mapping = store.one("SELECT * FROM test_answer_copies WHERE test_outbox_id=?", (row["id"],))
    if not mapping or row["purpose"] != "TEST_ANSWER" or mapping["target_platform"] != TEST_PLATFORM or not mapping["target_key"]:
        raise ValueError("TEST_COPY_MAPPING_MISSING")
    source = store.one("SELECT binding_id FROM outbox WHERE id=?", (mapping["source_outbox_id"],))
    if not source or row["binding_id"] != source["binding_id"]:
        raise ValueError("TEST_COPY_BINDING_MISMATCH")
    return BoundMessage(row["id"], row["binding_id"], TEST_PLATFORM, mapping["target_key"], row["body"])


def queue_test_answer(store, source_outbox_id: str, recipient: TestRecipient) -> str:
    """Queue one real, reviewed answer copy for the verified WeCom test contact."""
    if not isinstance(recipient, TestRecipient):
        raise ValueError("Verified TestRecipient required")
    if not isinstance(source_outbox_id, str) or not source_outbox_id:
        raise ValueError("Source outbox id required")
    with store.transaction():
        source = store.one("SELECT * FROM outbox WHERE id=?", (source_outbox_id,))
        if not source or source["state"] != "PENDING" or source["simulated"] != 0:
            raise ValueError("Approved real pending answer required")
        bound, answer = validate_source_answer(store, source)
        if any(char in source["body"] for char in "\r\n\t{}"):
            raise ValueError("TEST_ANSWER_REQUIRES_SINGLE_LINE_SAFE_BODY")
        prior = store.one("SELECT * FROM test_answer_copies WHERE source_outbox_id=?", (source_outbox_id,))
        if prior:
            if (prior["target_platform"], prior["target_key"], prior["target_display_name"]) != (
                    recipient.platform, recipient.stable_key, recipient.display_name):
                raise ValueError("TEST_TARGET_ALREADY_BOUND")
            copy = store.one("SELECT * FROM outbox WHERE id=?", (prior["test_outbox_id"],))
            validate_test_copy(store, copy)
            return prior["test_outbox_id"]
        test_id = new_id()
        store.execute("""INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
            context_revision,idempotency_key,state,created_at,answer_id,run_id,simulated,review_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (test_id, source["message_id"], source["case_id"], source["turn_id"], source["binding_id"],
             "TEST_ANSWER", render_test_body(test_id, source["body"]), source["question_version"], source["context_revision"],
             "test-answer:" + source_outbox_id, "PENDING", now(), source["answer_id"], source["run_id"], 0, "APPROVED"))
        store.execute("""INSERT INTO test_answer_copies(source_outbox_id,test_outbox_id,target_platform,
            target_key,target_display_name,verification_evidence,verified_at,source_body_hash,
            question_version,context_revision,answer_revision,created_at,body_format)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source_outbox_id, test_id, recipient.platform, recipient.stable_key, recipient.display_name,
             recipient.verification_evidence, recipient.verified_at.isoformat(),
             sha256(source["body"].encode("utf-8")).hexdigest(), source["question_version"],
             source["context_revision"], answer["answer_revision"], now(), PLAIN_BODY_FORMAT))
        return test_id
