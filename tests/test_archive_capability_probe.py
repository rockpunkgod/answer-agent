from pathlib import Path
import json
import sys
import tempfile
import unittest

from helpdesk.archive_capability_probe import probe_archive, probe_exit_code


class CapabilityProbeTests(unittest.TestCase):
    def test_missing_credentials_never_calls_network(self):
        def forbidden(*args):
            self.fail("no account request allowed without credentials")
        report = probe_archive({}, environment={}, request=forbidden)
        self.assertEqual(report["status"], "NEEDS_ADMIN_CONFIGURATION")
        self.assertFalse(report["network_attempted"])

    def _configured(self, directory):
        key, sdk = Path(directory) / "archive.key", Path(directory) / "sdk.dll"
        key.touch(); sdk.touch()
        env = {"WECOM_CORP_ID": "PRIVATE_CORP", "WECOM_ARCHIVE_SECRET": "PRIVATE_SECRET",
            "WECOM_ARCHIVE_PRIVATE_KEY": str(key), "WECOM_ARCHIVE_SDK_PATH": str(sdk)}
        config = {"archive": {"enabled": True, "admin_authorized": True,
            "member_scope_verified": True, "consent_verified": True,
            "evidence_reference": "mock evidence", "bridge_command": [sys.executable]},
            "archive_probe": {"allow_network_read_only": True, "room_ids": ["PRIVATE_ROOM"]}}
        return env, config

    def test_mock_read_only_success_still_does_not_claim_sdk_pull(self):
        with tempfile.TemporaryDirectory() as directory:
            env, config = self._configured(directory)
            calls = []
            def fake(path, params, body, timeout):
                calls.append(path)
                if path == "gettoken":
                    return {"errcode": 0, "access_token": "PRIVATE_TOKEN"}
                if path.endswith("get_permit_user_list"):
                    return {"errcode": 0, "ids": ["PRIVATE_MEMBER"]}
                return {"errcode": 0, "agreeinfo": [{"exteranalopenid": "PRIVATE_STUDENT", "agree_status": "Agree"}]}
            report = probe_archive(config, environment=env, request=fake)
            self.assertEqual(calls, ["gettoken", "msgaudit/get_permit_user_list", "msgaudit/check_room_agree"])
            self.assertEqual(report["account_permission"], "PERMITTED_MEMBERS_VERIFIED")
            self.assertEqual(report["status"], "NEEDS_MANUAL_VERIFICATION")
            self.assertFalse(report["sdk_data_pull_verified"])
            self.assertEqual(report["collector_readiness"], "NOT_VERIFIED")
            self.assertTrue(report["read_only_checks_passed"])
            self.assertEqual(probe_exit_code(report), 3)
            for secret in ("PRIVATE_CORP", "PRIVATE_SECRET", "PRIVATE_TOKEN", "PRIVATE_MEMBER", "PRIVATE_ROOM", "PRIVATE_STUDENT"):
                self.assertNotIn(secret, json.dumps(report))

    def test_api_error_cannot_be_success_and_error_message_is_not_leaked(self):
        with tempfile.TemporaryDirectory() as directory:
            env, config = self._configured(directory)
            report = probe_archive(config, environment=env,
                request=lambda *args: {"errcode": 40013, "errmsg": "PRIVATE_SECRET"})
            self.assertEqual(report["account_permission"], "NOT_CHECKED")
            self.assertEqual(report["checks"][0]["errcode"], 40013)
            self.assertNotIn("PRIVATE_SECRET", json.dumps(report))
            self.assertEqual(probe_exit_code(report), 2)

    def test_permitted_members_do_not_mask_later_consent_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            env, config = self._configured(directory)
            def fake(path, *args):
                if path == "gettoken":
                    return {"errcode": 0, "access_token": "PRIVATE_TOKEN"}
                if path.endswith("get_permit_user_list"):
                    return {"errcode": 0, "ids": ["PRIVATE_MEMBER"]}
                return {"errcode": 48002}
            report = probe_archive(config, environment=env, request=fake)
            self.assertEqual(report["account_permission"], "PERMITTED_MEMBERS_VERIFIED")
            self.assertFalse(report["read_only_checks_passed"])
            self.assertFalse(report["sdk_data_pull_verified"])
            self.assertEqual(probe_exit_code(report), 2)

    def test_probe_never_exits_zero_based_on_permission_labels(self):
        for report in ({"account_permission": "AVAILABLE"},
                       {"account_permission": "PERMITTED_MEMBERS_VERIFIED"},
                       {"account_permission": "PERMITTED_MEMBERS_VERIFIED", "read_only_checks_passed": True}):
            self.assertNotEqual(probe_exit_code(report), 0)

    def test_no_effective_members_and_unapproved_network_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            env, config = self._configured(directory)
            report = probe_archive(config, environment=env, request=lambda path,*args:
                {"errcode": 0,"access_token":"PRIVATE_TOKEN"} if path == "gettoken" else {"errcode": 0,"ids":[]})
            self.assertEqual(report["status"], "NEEDS_ADMIN_CONFIGURATION")
            config["archive_probe"]["allow_network_read_only"] = False
            report = probe_archive(config, environment=env, request=lambda *args: self.fail("unexpected network"))
            self.assertFalse(report["network_attempted"])
