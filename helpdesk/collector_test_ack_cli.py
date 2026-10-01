"""Explicit operator commands; no automatic routing, selection or contact search."""
import argparse
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import time

from .collector_storage import CollectorStore
from .collector_test_ack import CollectorTestAckWorkflow, queue_test_ack, _schema
from .mcp_test_delivery import MCPTestAnswerDesktop
from .mcp_transport import MCPProcess
from .storage import Store
from .test_routing import TestRecipient


ROOT = Path(__file__).resolve().parents[1]


def _recipient(path):
    fields = json.loads(Path(path).read_text(encoding="utf-8"))
    fields["verified_at"] = datetime.fromisoformat(fields["verified_at"])
    return TestRecipient(**fields)


def _mapping(store, oid):
    row = store.one("SELECT * FROM collector_test_ack_copies WHERE test_outbox_id=?", (oid,))
    if not row:
        raise ValueError("EXPLICIT_MAPPED_TEST_ACK_REQUIRED")
    return row


def reviewed_pin_for_ack(store, oid, template, *, current_time=None):
    """Bind a still-valid reviewed contact pin to ONE pending actual-source ACK.

    Does not extend the template's lifetime or derive identity from coordinates.
    Caller must immediately verify the current foreground against this pin.
    """
    current_time = time.time() if current_time is None else current_time
    mapping = _mapping(store, oid)
    row = store.one("SELECT * FROM outbox WHERE id=?", (oid,))
    if mapping["observation_mode"] != "ACTUAL" or row["simulated"] != 0 or row["state"] != "PENDING" or row["body"] != "收到":
        raise ValueError("ONLY_PENDING_ACTUAL_SOURCE_TEST_ACK_CAN_GET_LIVE_PIN")
    expires = template.get("expires_at")
    if type(expires) not in (int, float) or not math.isfinite(expires) or not current_time < expires <= current_time + 900:
        raise ValueError("CURRENT_SHORT_LIVED_OPERATOR_REVIEWED_PIN_REQUIRED")
    if (template.get("platform"), template.get("display_name"), template.get("target_key")) != ("wecom", "苇中鹤", mapping["target_key"]):
        raise ValueError("ONLY_FROZEN_WECOM_TEST_TARGET_ALLOWED")
    pin = dict(template)
    pin.update(outbox_id=oid, binding_id=row["binding_id"], body_hash=sha256("收到".encode()).hexdigest(),
               expires_at=min(expires, current_time + 300), source_student_delivered=False,
               collector_message_id=mapping["collector_message_id"], source_outbox_id=mapping["source_outbox_id"])
    return pin


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("queue", "list", "prepare-pin", "dispatch", "inspect"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--collector-database", type=Path, default=ROOT / "data/messages.db")
    parser.add_argument("--source-ack")
    parser.add_argument("--recipient", type=Path, help="Operator-reviewed TestRecipient JSON")
    parser.add_argument("--source-evidence", type=Path)
    parser.add_argument("--observation-mode", choices=("ACTUAL", "FIXTURE"), default="FIXTURE")
    parser.add_argument("--outbox")
    parser.add_argument("--pin", type=Path)
    parser.add_argument("--pin-template", type=Path, help="Still-valid operator-reviewed target visual pin; never renewed automatically")
    args = parser.parse_args(argv)
    with closing(Store(args.database)) as store:
        _schema(store)
        collector = CollectorStore(args.collector_database)
        if args.command == "list":
            result = [dict(row) for row in store.all('''SELECT c.test_outbox_id,c.source_outbox_id,
                c.collector_message_id,c.source_binding_id,c.target_key,c.observation_mode,
                o.state,o.body,o.simulated FROM collector_test_ack_copies c
                JOIN outbox o ON o.id=c.test_outbox_id ORDER BY c.created_at''')]
        elif args.command == "queue":
            if not all((args.source_ack, args.recipient, args.source_evidence)):
                parser.error("queue requires --source-ack --recipient --source-evidence")
            oid = queue_test_ack(store, collector, args.source_ack, _recipient(args.recipient),
                source_evidence_path=args.source_evidence, observation_mode=args.observation_mode)
            result = {"test_outbox_id": oid, "state": "QUEUED_NOT_SENT", "source_student_delivered": False,
                      "observation_mode": args.observation_mode}
        else:
            if not args.outbox or not args.pin:
                parser.error("explicit --outbox and --pin required")
            mapping = _mapping(store, args.outbox)
            row = store.one("SELECT * FROM outbox WHERE id=?", (args.outbox,))
            if args.command == "dispatch" and row["state"] != "PENDING":
                result = {"state": row["state"], "resubmitted": False}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            if mapping["observation_mode"] != "ACTUAL" or row["simulated"] != 0:
                raise ValueError("FIXTURE_ACK_NEVER_USES_LIVE_MCP_TRANSPORT")
            if args.command == "prepare-pin":
                if not args.pin_template or args.pin.exists():
                    parser.error("prepare-pin requires reviewed --pin-template and a new --pin output path")
                template = json.loads(args.pin_template.read_text(encoding="utf-8"))
                pin = reviewed_pin_for_ack(store, args.outbox, template)
            else:
                pin = json.loads(args.pin.read_text(encoding="utf-8"))
                if pin.get("outbox_id") != args.outbox:
                    raise ValueError("PIN_AND_TEST_ACK_MISMATCH")
            with MCPProcess(ROOT / ".venv-windows-mcp/Scripts/python.exe") as mcp:
                desktop = MCPTestAnswerDesktop(mcp, pin, ROOT)
                flow = CollectorTestAckWorkflow(store, collector, desktop)
                if args.command == "prepare-pin":
                    bound = flow._validate(row)
                    desktop.preflight(bound)
                    args.pin.parent.mkdir(parents=True, exist_ok=True)
                    with args.pin.open("x", encoding="utf-8") as output:
                        json.dump(pin, output, ensure_ascii=False, indent=2)
                    result = {"state": "PIN_VERIFIED_NOT_SENT", "test_outbox_id": args.outbox, "expires_at": pin["expires_at"]}
                else:
                    state = flow.inspect_unknown(args.outbox) if args.command == "inspect" else flow.dispatch(args.outbox)
                    result = {"state": state, "test_outbox_id": args.outbox, "source_student_delivered": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
