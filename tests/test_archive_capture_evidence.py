import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import os
from unittest.mock import patch

from helpdesk.archive_capture_evidence import (canonical, sign_native_receipt,
    verify_native_receipt, verify_archive_capture, current_deployment,
    ArchiveCaptureEvidence, lookup_archive_capture)
from helpdesk.wecom_archive import ArchiveAuthorization, SdkBridgeTransport
from helpdesk.wecom_sdk_bridge import handle_request
from tests.test_wecom_sdk_bridge import FakeNativeLibrary


class ArchiveCaptureEvidenceTests(unittest.TestCase):
    """No account execution. Synthetic signed contracts test only local integrity.

    Positive receipts are constructed with an isolated test-only signing key;
    they do not come from a loaded SDK, a real account, or a successful pull.
    No synthetic artifact survives TemporaryDirectory or enters a business DB.
    """
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fake.dll"
        self.path.write_bytes(b"this is not a native SDK")
        self.env = {"WECOM_ARCHIVE_RUNTIME_MODE": "ACTUAL", "WECOM_ARCHIVE_DEPLOYMENT_ID": "test-deployment",
            "WECOM_ARCHIVE_SDK_SHA256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "WECOM_ARCHIVE_EVIDENCE_KEY": base64.b64encode(b"test-only-evidence-key-32bytes!!!").decode(),
            "WECOM_ARCHIVE_SDK_PATH": str(self.path), "WECOM_CORP_ID": "fixturecorp",
            "WECOM_ARCHIVE_SECRET": "fixturesecret", "WECOM_ARCHIVE_AUTHORIZED": "true",
            "WECOM_ARCHIVE_AUTHORIZATION_EVIDENCE": "fixture-only authorization"}
        # Ensure test-key meets the minimum without reading actual host secrets.
        self.env["WECOM_ARCHIVE_EVIDENCE_KEY"] = base64.b64encode(b"fixture-key-never-deploy" * 2).decode()

    def test_fake_native_library_cannot_sign_actual_capture(self):
        library = FakeNativeLibrary()
        result = handle_request({"protocol": "wecom-archive-sdk-v1", "operation": "get_chat_data",
            "params": {"seq": "0", "limit": 1}}, self.env, loader=lambda _: library)
        self.assertNotEqual(result["sdk_code"], 0)
        self.assertEqual(result["error_category"], "ACTUAL_NATIVE_DEPLOYMENT_MISMATCH")
        self.assertNotIn("receipt_hmac_sha256", result)
        self.assertEqual(library.calls, [])

    def test_signed_fixture_receipt_is_rejected_even_with_matching_deployment(self):
        deployment = current_deployment(self.env)
        receipt = {"protocol": "wecom-archive-sdk-v1", "operation": "get_chat_data", "sdk_code": 0,
            "errcode": 0, "result": {"errcode": 0, "chatdata": []},
            "native_runtime": {**deployment, "execution_kind": "NATIVE_SDK", "observation_mode": "FIXTURE"}}
        receipt["receipt_hmac_sha256"] = sign_native_receipt(receipt, self.env)
        with self.assertRaisesRegex(ValueError, "ACTUAL_NATIVE_RUNTIME_MISMATCH"):
            verify_native_receipt(receipt, "get_chat_data", deployment, self.env)
        receipt["native_runtime"]["observation_mode"] = "ACTUAL"
        with self.assertRaisesRegex(ValueError, "SIGNATURE_INVALID"):
            verify_native_receipt(receipt, "get_chat_data", deployment, self.env)

    def test_generic_bridge_command_cannot_enable_actual_runtime(self):
        auth = ArchiveAuthorization(True, True, True, True, "test-only evidence")
        with self.assertRaisesRegex(RuntimeError, "bundled native SDK bridge"):
            SdkBridgeTransport((sys.executable, "fixture-script.py"), authorization=auth, runtime_mode="ACTUAL")

    def test_capture_rejects_fixture_artifact_without_changing_official_payload(self):
        plaintext = {"msgid": "fixture-message", "msgtime": 1234, "text": {"content": "fixture"}}
        raw = {"source_type": "wecom_archive", "source_name": "wecom_archive", "message_id": "local-id",
            "source_message_id": "fixture-message", "source_seq": "1", "raw_payload": json.dumps(plaintext)}
        artifact = Path(self.directory.name) / "fixture.json"
        artifact.write_text(json.dumps({"format": "wecom-native-capture-v1", "source_name": "wecom_archive",
            "source_type": "wecom_archive", "mode": "LIVE", "observation_mode": "FIXTURE"}), encoding="utf-8")
        original = raw["raw_payload"]
        with patch.dict("os.environ", self.env, clear=True):
            with self.assertRaisesRegex(ValueError, "CAPTURE_MISMATCH"):
                verify_archive_capture(raw, artifact)
        self.assertEqual(raw["raw_payload"], original)
        self.assertNotIn("capture_evidence", json.loads(original))

    def test_missing_signing_key_or_deployment_is_not_actual_evidence(self):
        with self.assertRaisesRegex(ValueError, "EVIDENCE_KEY_REQUIRED"):
            sign_native_receipt({"result": {}}, {})
        with self.assertRaisesRegex(ValueError, "DEPLOYMENT_REQUIRED"):
            current_deployment({})

    def synthetic_contract(self, count=2):
        """Model success responses for a verifier contract, NEVER account evidence."""
        deployment = current_deployment(self.env)
        def receipt(operation, result):
            body = {"protocol": "wecom-archive-sdk-v1", "operation": operation,
                "sdk_code": 0, "errcode": 0, "result": result,
                "native_runtime": {**deployment, "execution_kind": "NATIVE_SDK", "observation_mode": "ACTUAL"}}
            body["receipt_hmac_sha256"] = sign_native_receipt(body, self.env)
            return body
        entries, rows, envelopes = [], [], []
        for seq in range(1, count+1):
            payload = {"msgid": f"synthetic-{seq}", "msgtime": 1234+seq,
                       "from": f"student{seq}", "text": {"content": f"contract-{seq}"}}
            envelopes.append({"msgid": payload["msgid"], "seq": seq, "encrypt_chat_msg": "synthetic-ciphertext"})
            entries.append({"source_message_id": payload["msgid"], "source_seq": seq,
                "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(), "raw_payload": payload,
                "decrypt_receipt": receipt("decrypt_message", payload)})
            rows.append({"source_type": "wecom_archive", "source_name": "synthetic-contract-source",
                "message_id": f"local-contract-{seq}", "source_message_id": payload["msgid"],
                "source_seq": str(seq), "raw_payload": json.dumps(payload)})
        return deployment, receipt("get_chat_data", {"errcode": 0, "chatdata": envelopes}), entries, rows

    def test_synthetic_signed_contract_save_lookup_verify_multiple_messages(self):
        deployment, page, entries, rows = self.synthetic_contract()
        original_payloads = [row["raw_payload"] for row in rows]
        root = Path(self.directory.name) / "synthetic-contract-evidence"
        with patch.dict("os.environ", self.env, clear=True):
            writer = ArchiveCaptureEvidence(root, deployment)
            artifact = writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            for row in rows:
                resolved = lookup_archive_capture(row, root)
                self.assertEqual(resolved, artifact)
                contract_result = verify_archive_capture(row, resolved)
                self.assertTrue(contract_result["verified_actual"])
                self.assertEqual(contract_result["collector_message_id"], row["message_id"])
                self.assertEqual(contract_result["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual([row["raw_payload"] for row in rows], original_payloads)
        self.assertFalse(any("capture_evidence" in json.loads(raw) for raw in original_payloads))

    def test_synthetic_contract_identical_retry_reuses_file_and_later_retry_preserves_first(self):
        deployment, page, entries, rows = self.synthetic_contract(count=1)
        root = Path(self.directory.name) / "synthetic-contract-retry"
        with patch.dict("os.environ", self.env, clear=True):
            writer = ArchiveCaptureEvidence(root, deployment)
            with patch("helpdesk.archive_capture_evidence.utc_now", return_value="synthetic-time-1"):
                first = writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
                second = writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            self.assertEqual(first, second)
            with patch("helpdesk.archive_capture_evidence.utc_now", return_value="synthetic-time-2"):
                later = writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            self.assertNotEqual(later, first)
            self.assertEqual(lookup_archive_capture(rows[0], root), first)
            verify_archive_capture(rows[0], first)

    def test_corrupt_existing_index_holds_explicitly_and_is_never_overwritten(self):
        deployment, page, entries, rows = self.synthetic_contract(count=1)
        root = Path(self.directory.name) / "synthetic-corrupt-index"
        with patch.dict("os.environ", self.env, clear=True):
            writer = ArchiveCaptureEvidence(root, deployment)
            writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            index = next(root.glob("*.index.json"))
            index.write_bytes(b'{"incomplete":')
            with self.assertRaisesRegex(ValueError, "INDEX_HOLD_REQUIRED"):
                writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            with self.assertRaisesRegex(ValueError, "INDEX_HOLD_REQUIRED"):
                lookup_archive_capture(rows[0], root)
            self.assertEqual(index.read_bytes(), b'{"incomplete":')

    def test_index_publication_failure_leaves_no_partial_index_and_retry_succeeds(self):
        deployment, page, entries, rows = self.synthetic_contract(count=1)
        root = Path(self.directory.name) / "synthetic-publish-failure"
        real_link = os.link
        def fail_index_publish(source, destination):
            if str(destination).endswith(".index.json"):
                raise OSError("synthetic interruption before publication")
            return real_link(source, destination)
        with patch.dict("os.environ", self.env, clear=True):
            writer = ArchiveCaptureEvidence(root, deployment)
            with patch("helpdesk.archive_capture_evidence.os.link", side_effect=fail_index_publish):
                with self.assertRaises(OSError):
                    writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            self.assertEqual(list(root.glob("*.index.json")), [])
            self.assertEqual(list(root.glob("*.tmp")), [])
            writer.save_batch("synthetic-contract-source", "LIVE", page, entries)
            verify_archive_capture(rows[0], lookup_archive_capture(rows[0], root))
