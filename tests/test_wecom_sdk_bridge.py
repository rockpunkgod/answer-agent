import base64
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from helpdesk.wecom_sdk_bridge import handle_request


class NativeFunction:
    def __init__(self, function):
        self.function = function
    def __call__(self, *args):
        return self.function(*args)


class FakeNativeLibrary:
    """C-like opaque handles and length-based buffers, including binary NULs."""
    def __init__(self):
        self.destroyed, self.freed_slices, self.freed_media = [], [], []
        self.calls, self.buffers = [], []
        self.native_error = 0
        self.init_error = 0
        self.json_payload = {"errcode": 0, "chatdata": []}
        self.media_bytes = b"a\0b\xff"
        methods = {
            "NewSdk": lambda: 10,
            "Init": self.init, "DestroySdk": self.destroyed.append,
            "NewSlice": lambda: 20, "FreeSlice": self.freed_slices.append,
            "GetContentFromSlice": lambda _: self.buffer(json.dumps(self.json_payload).encode()),
            "GetSliceLen": lambda _: len(json.dumps(self.json_payload).encode()),
            "GetChatData": self.chat, "DecryptData": self.decrypt,
            "NewMediaData": lambda: 30, "FreeMediaData": self.freed_media.append,
            "GetOutIndexBuf": lambda _: self.buffer(b"next"), "GetIndexLen": lambda _: 4,
            "GetData": lambda _: self.buffer(self.media_bytes),
            "GetDataLen": lambda _: len(self.media_bytes),
            "IsMediaDataFinish": lambda _: 0, "GetMediaData": self.media}
        for name, method in methods.items():
            setattr(self, name, NativeFunction(method))
    def buffer(self, data):
        buffer = ctypes.create_string_buffer(data)
        self.buffers.append(buffer)
        return ctypes.addressof(buffer)
    def init(self, *args):
        self.calls.append(("Init", args)); return self.init_error
    def chat(self, *args):
        self.calls.append(("GetChatData", args)); return self.native_error
    def decrypt(self, *args):
        self.calls.append(("DecryptData", args)); return self.native_error
    def media(self, *args):
        self.calls.append(("GetMediaData", args)); return self.native_error


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sdk_path = Path(self.directory.name) / "official-sdk.dll"
        self.sdk_path.touch()
        self.env = {"WECOM_ARCHIVE_SDK_PATH": str(self.sdk_path), "WECOM_CORP_ID": "mockcorp",
            "WECOM_ARCHIVE_SECRET": "mocksecret", "WECOM_ARCHIVE_AUTHORIZED": "true",
            "WECOM_ARCHIVE_AUTHORIZATION_EVIDENCE": "mock administrator fixture"}
        self.library = FakeNativeLibrary()
    def call(self, operation, params):
        return handle_request({"protocol": "wecom-archive-sdk-v1", "operation": operation,
            "params": params}, self.env, loader=lambda _: self.library)

    def test_get_chat_calls_native_uint64_and_frees_slice_and_sdk(self):
        result = self.call("get_chat_data", {"seq": str(2**64-2), "limit": 1000})
        self.assertEqual(result["sdk_code"], 0)
        self.assertEqual(result["result"], self.library.json_payload)
        self.assertEqual(self.library.calls[-1][1][1:3], (2**64-2, 1000))
        self.assertEqual(self.library.freed_slices, [20])
        self.assertEqual(self.library.destroyed, [10])
        self.assertEqual(self.library.GetChatData.argtypes[1], ctypes.c_uint64)

    def test_init_error_destroys_sdk_and_never_fetches(self):
        self.library.init_error = 10009
        result = self.call("get_chat_data", {"seq": "0", "limit": 1000})
        self.assertEqual(result["sdk_code"], 10009)
        self.assertEqual(self.library.destroyed, [10])
        self.assertEqual([x[0] for x in self.library.calls], ["Init"])

    def test_native_fetch_and_json_failure_release_resources(self):
        self.library.native_error = 10006
        result = self.call("get_chat_data", {"seq": "0", "limit": 1})
        self.assertEqual(result["sdk_code"], 10006)
        self.assertEqual(self.library.freed_slices, [20])
        self.assertEqual(self.library.destroyed, [10])

    def test_media_preserves_binary_nul_and_exact_argument_order(self):
        result = self.call("get_media_data", {"sdkfileid": "mockfile", "indexbuf": "previous"})
        self.assertEqual(base64.b64decode(result["result"]["data_base64"]), b"a\0b\xff")
        self.assertEqual(self.library.calls[-1][1][1:3], (b"previous", b"mockfile"))
        self.assertEqual(self.library.freed_media, [30])
        self.assertEqual(self.library.destroyed, [10])

    def test_oversized_native_media_rejected_and_freed(self):
        self.library.GetDataLen = NativeFunction(lambda _: 512 * 1024 + 1)
        result = self.call("get_media_data", {"sdkfileid": "mockfile", "indexbuf": ""})
        self.assertEqual(result["error_category"], "INVALID_NATIVE_BUFFER")
        self.assertEqual(self.library.freed_media, [30])
        self.assertEqual(self.library.destroyed, [10])

    def test_rsa_pkcs1v15_uses_correct_publickey_version_and_native_decrypt(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path = Path(self.directory.name) / "mock-key.pem"
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.env["WECOM_ARCHIVE_PRIVATE_KEYS"] = json.dumps({"3": str(path)})
        encrypted = base64.b64encode(key.public_key().encrypt(b"mock-random-key", padding.PKCS1v15())).decode()
        envelope = {"publickey_ver": 3, "encrypt_random_key": encrypted, "encrypt_chat_msg": "mockencrypted"}
        self.library.json_payload = {"msgid": "mockid", "text": {"content": "question"}}
        result = self.call("decrypt_message", {"envelope": envelope})
        self.assertEqual(result["sdk_code"], 0)
        self.assertEqual(self.library.calls[-1][1][:2], (b"mock-random-key", b"mockencrypted"))
        self.assertEqual(self.library.freed_slices, [20])
        self.assertEqual(self.library.destroyed, [10])

    def test_unknown_privatekey_version_does_not_decrypt_or_fake_success(self):
        result = self.call("decrypt_message", {"envelope": {"publickey_ver": 99}})
        self.assertEqual(result["sdk_code"], 10007)
        self.assertEqual(self.library.destroyed, [10])
        self.assertNotIn("DecryptData", [x[0] for x in self.library.calls])

    def test_missing_sdk_or_authorization_never_loads_library(self):
        def forbidden(_):
            self.fail("must not load native library")
        request = {"protocol": "wecom-archive-sdk-v1", "operation": "get_chat_data",
                   "params": {"seq": "0", "limit": 1000}}
        env = dict(self.env, WECOM_ARCHIVE_AUTHORIZED="false")
        result = handle_request(request, env, loader=forbidden)
        self.assertEqual(result["error_category"], "EXPLICIT_AUTHORIZATION_REQUIRED")
        env = dict(self.env, WECOM_ARCHIVE_SDK_PATH=str(self.sdk_path.with_name("missing.dll")))
        result = handle_request(request, env, loader=forbidden)
        self.assertEqual(result["error_category"], "OFFICIAL_SDK_FILE_MISSING")

    def test_server_error_returns_integer_code_without_sensitive_errmsg(self):
        self.library.json_payload = {"errcode": 40013, "errmsg": "mocksecret must not leak"}
        result = self.call("get_chat_data", {"seq": "0", "limit": 1})
        self.assertEqual(result["errcode"], 40013)
        self.assertEqual(result["result"], {})
        self.assertNotIn("mocksecret", json.dumps(result))

    def test_real_cli_without_authorization_outputs_protocol_failure_not_mock_data(self):
        environment = {"PATH": __import__("os").environ.get("PATH", ""), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
        request = {"protocol": "wecom-archive-sdk-v1", "operation": "get_chat_data", "params": {"seq": "0", "limit": 1}}
        process = subprocess.run([sys.executable, "-m", "helpdesk.wecom_sdk_bridge"],
            input=json.dumps(request), capture_output=True, text=True, env=environment, timeout=10)
        response = json.loads(process.stdout)
        self.assertEqual(response["sdk_code"], -1)
        self.assertEqual(response["result"], {})
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
