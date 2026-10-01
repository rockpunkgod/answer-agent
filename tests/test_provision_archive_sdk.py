import hashlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from helpdesk.provision_archive_sdk import validate_archive, pe_metadata


class ProvisionArchiveSdkTests(unittest.TestCase):
    def test_changed_download_hash_is_rejected_before_zip_extraction(self):
        with self.assertRaisesRegex(ValueError, "HASH_CHANGED_DO_NOT_INSTALL"):
            validate_archive(b"untrusted or changed package")

    def test_traversal_member_is_rejected_even_when_unit_fixture_hash_is_pinned(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../unsafe.dll", b"fixture")
        raw = buffer.getvalue()
        with patch("helpdesk.provision_archive_sdk.ZIP_SHA256", hashlib.sha256(raw).hexdigest()):
            with self.assertRaisesRegex(ValueError, "UNSAFE_ZIP_MEMBER"):
                validate_archive(raw)

    def test_pe_parser_rejects_non_dll_without_loading_it(self):
        with self.assertRaisesRegex(ValueError, "NOT_PE_DLL"):
            pe_metadata(b"this is not a native executable")

    def test_provisioned_official_dll_metadata_is_read_without_execution(self):
        # Optional structural check when the officially provisioned file is present.
        path = Path("data/private/wecom-sdk/WeWorkFinanceSdk.dll")
        if not path.exists():
            self.skipTest("official SDK files have not been provisioned")
        with patch("ctypes.CDLL", side_effect=AssertionError("must never load DLL")):
            metadata = pe_metadata(path.read_bytes())
        self.assertEqual(metadata["architecture"], "x64")
        self.assertIn("libcrypto-3-x64.dll", metadata["imports"])
        self.assertIn("libcurl-x64.dll", metadata["imports"])
