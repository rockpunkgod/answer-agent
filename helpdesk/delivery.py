"""Simulated external UI ledger. Its transaction is intentionally separate from business DB."""
from dataclasses import dataclass
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3

from .storage import now


class SimulatedCrash(BaseException):
    """Power-loss injection after the external side effect, before local confirmation."""


class PreflightFailure(Exception):
    """No send attempted. A human must resolve the reason before retrying."""


class NotSubmitted(Exception):
    """Adapter proves failure happened before the submit gesture, possibly leaving a draft.

    This is not retry permission. Only adapters with a strict pre-submit boundary may
    raise it; any exception during/after that gesture remains SEND_UNKNOWN.
    """
    CODES = frozenset({"COMPOSER_INPUT_INCOMPLETE", "DRAFT_VERIFICATION_FAILED", "PRE_SUBMISSION_CHECK_FAILED"})

    def __init__(self, code):
        if code not in self.CODES:
            raise ValueError("Unsupported pre-submit failure code")
        super().__init__(code)


@dataclass(frozen=True)
class BoundMessage:
    outbox_id: str
    binding_id: str
    group_key: str
    student_key: str
    body: str

    @property
    def body_hash(self):
        return sha256(self.body.encode("utf-8")).hexdigest()


class MockDesktop:
    simulated = True

    def __init__(self, path: str | Path, *, fault=None):
        self.path = str(path)
        self.lock_path = self.path + ".desktop.lock"
        self.fault = fault
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts(
                outbox_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,group_key TEXT NOT NULL,
                student_key TEXT NOT NULL,body TEXT NOT NULL,body_hash TEXT NOT NULL,
                visible INTEGER NOT NULL,confirmed_at TEXT NOT NULL)""")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def preflight(self, message: BoundMessage):
        if self.fault == "locked":
            raise PreflightFailure("DESKTOP_LOCKED")
        if self.fault == "wrong_identity":
            raise PreflightFailure("RECIPIENT_MISMATCH")
        if self.fault == "before_send":
            raise PreflightFailure("WINDOW_UNAVAILABLE")
        # This is an explicit simulated selection check, never claimed to validate WeCom.
        if not message.group_key or not message.student_key:
            raise PreflightFailure("IDENTITY_MISSING")

    def send(self, message: BoundMessage):
        self.preflight(message)
        with self._connection() as db:
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?)",
                       (message.outbox_id, message.binding_id, message.group_key, message.student_key,
                        message.body, message.body_hash, int(self.fault != "unknown"), now()))
        if self.fault == "after_send":
            raise SimulatedCrash("Simulated crash after external delivery")
        return self.reconcile(message)

    def reconcile(self, message: BoundMessage):
        with self._connection() as db:
            row = db.execute("SELECT * FROM receipts WHERE outbox_id=?", (message.outbox_id,)).fetchone()
        if row and row["visible"] and (row["binding_id"], row["group_key"], row["student_key"], row["body_hash"]) == (
                message.binding_id, message.group_key, message.student_key, message.body_hash):
            return {"confirmed": True, "simulated": True, "body_hash": row["body_hash"], "confirmed_at": row["confirmed_at"]}
        return {"confirmed": False, "simulated": True, "reason": "NO_UNAMBIGUOUS_UI_EVIDENCE"}

    def receipts(self):
        with self._connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM receipts ORDER BY confirmed_at")]
