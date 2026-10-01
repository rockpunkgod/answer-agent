"""Real ctypes bridge for an administrator-supplied official Finance SDK.

ABI verified against official SDK v3 WeWorkFinanceSdk_C.h; no mock fallback.
Invoke with an absolute Python executable and -m helpdesk.wecom_sdk_bridge.
"""
import base64
from contextlib import contextmanager
import ctypes as C
import json
import os
from pathlib import Path
import sys


class BridgeFailure(RuntimeError):
    def __init__(self, code=-1, category="BRIDGE_FAILURE"):
        self.code, self.category = code, category


def _cstring(value):
    if not isinstance(value, str) or "\0" in value:
        raise BridgeFailure(category="INVALID_PARAMETER")
    return value.encode("utf-8")


class FinanceSdk:
    def __init__(self, library, corp_id, secret, *, timeout=25, proxy="", proxy_password=""):
        self.lib, self.sdk = library, None
        self.timeout, self.proxy, self.proxy_password = timeout, _cstring(proxy), _cstring(proxy_password)
        p, s, i = C.c_void_p, C.c_char_p, C.c_int
        signatures = {
            "NewSdk": ([], p), "Init": ([p, s, s], i), "DestroySdk": ([p], None),
            "NewSlice": ([], p), "FreeSlice": ([p], None),
            "GetContentFromSlice": ([p], p), "GetSliceLen": ([p], i),
            "GetChatData": ([p, C.c_uint64, C.c_uint32, s, s, i, p], i),
            "DecryptData": ([s, s, p], i), "NewMediaData": ([], p),
            "FreeMediaData": ([p], None), "GetOutIndexBuf": ([p], p),
            "GetData": ([p], p), "GetIndexLen": ([p], i), "GetDataLen": ([p], i),
            "IsMediaDataFinish": ([p], i), "GetMediaData": ([p, s, s, s, s, i, p], i)}
        try:
            for name, (args, result) in signatures.items():
                function = getattr(library, name)
                function.argtypes, function.restype = args, result
        except AttributeError:
            raise BridgeFailure(category="SDK_ABI_INCOMPATIBLE") from None
        corp, credential = _cstring(corp_id), _cstring(secret)
        self.sdk = library.NewSdk()
        if not self.sdk:
            raise BridgeFailure(category="SDK_ALLOCATION_FAILED")
        try:
            self._check(library.Init(self.sdk, corp, credential))
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(code):
        if type(code) is not int or code != 0:
            raise BridgeFailure(code if type(code) is int else -1, "NATIVE_SDK_ERROR")

    def close(self):
        if self.sdk:
            sdk, self.sdk = self.sdk, None
            self.lib.DestroySdk(sdk)

    @staticmethod
    def _read(pointer, length, maximum):
        if type(length) is not int or not 0 <= length <= maximum or (length and not pointer):
            raise BridgeFailure(category="INVALID_NATIVE_BUFFER")
        return C.string_at(pointer, length) if length else b""

    def _slice(self, call):
        pointer = self.lib.NewSlice()
        if not pointer:
            raise BridgeFailure(category="SDK_ALLOCATION_FAILED")
        try:
            self._check(call(pointer))
            raw = self._read(self.lib.GetContentFromSlice(pointer), self.lib.GetSliceLen(pointer), 32 * 1024 * 1024)
            result = json.loads(raw.rstrip(b"\0"))
            if not isinstance(result, dict):
                raise BridgeFailure(category="INVALID_SDK_JSON")
            return result
        finally:
            self.lib.FreeSlice(pointer)

    def chat(self, seq, limit):
        if not isinstance(seq, str) or not seq.isascii() or not seq.isdecimal() or not 0 <= int(seq) < 2**64:
            raise BridgeFailure(category="INVALID_PARAMETER")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise BridgeFailure(category="INVALID_PARAMETER")
        return self._slice(lambda output: self.lib.GetChatData(self.sdk, int(seq), limit,
            self.proxy, self.proxy_password, self.timeout, output))

    def decrypt(self, envelope, key_paths, *, key_password=None):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except ImportError:
            raise BridgeFailure(category="RSA_DEPENDENCY_MISSING") from None
        if not isinstance(envelope, dict) or type(envelope.get("publickey_ver")) is not int:
            raise BridgeFailure(category="INVALID_PARAMETER")
        key_path = key_paths.get(str(envelope["publickey_ver"]))
        if not key_path:
            raise BridgeFailure(10007, "PRIVATE_KEY_VERSION_MISSING")
        path = Path(key_path)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 65536:
            raise BridgeFailure(category="PRIVATE_KEY_FILE_INVALID")
        key = serialization.load_pem_private_key(path.read_bytes(), password=key_password)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
            raise BridgeFailure(category="RSA_2048_KEY_REQUIRED")
        encrypted = envelope.get("encrypt_random_key")
        if not isinstance(encrypted, str) or len(encrypted) > 4096:
            raise BridgeFailure(category="INVALID_PARAMETER")
        random_key = key.decrypt(base64.b64decode(encrypted, validate=True), padding.PKCS1v15())
        if not random_key or b"\0" in random_key:
            raise BridgeFailure(category="INVALID_DECRYPTED_KEY")
        message = _cstring(envelope.get("encrypt_chat_msg"))
        return self._slice(lambda output: self.lib.DecryptData(random_key, message, output))

    def media(self, sdkfileid, indexbuf):
        fileid, index = _cstring(sdkfileid), _cstring(indexbuf)
        if not fileid:
            raise BridgeFailure(category="INVALID_PARAMETER")
        pointer = self.lib.NewMediaData()
        if not pointer:
            raise BridgeFailure(category="SDK_ALLOCATION_FAILED")
        try:
            self._check(self.lib.GetMediaData(self.sdk, index, fileid, self.proxy,
                self.proxy_password, self.timeout, pointer))
            data = self._read(self.lib.GetData(pointer), self.lib.GetDataLen(pointer), 512 * 1024)
            next_index = self._read(self.lib.GetOutIndexBuf(pointer), self.lib.GetIndexLen(pointer), 1024 * 1024)
            finished = self.lib.IsMediaDataFinish(pointer)
            if type(finished) is not int or finished not in (0, 1):
                raise BridgeFailure(category="INVALID_NATIVE_BUFFER")
            return {"data_base64": base64.b64encode(data).decode("ascii"),
                "outindexbuf": next_index.rstrip(b"\0").decode("utf-8"), "is_finish": finished}
        finally:
            self.lib.FreeMediaData(pointer)


def handle_request(request, environment=None, *, loader=C.CDLL):
    environment = os.environ if environment is None else environment
    operation = request.get("operation", "") if isinstance(request, dict) else ""
    response = {"protocol": "wecom-archive-sdk-v1", "operation": operation,
                "sdk_code": -1, "errcode": 0, "result": {}}
    sdk, directory = None, None
    try:
        if not isinstance(request, dict) or request.get("protocol") != response["protocol"] or operation not in {"get_chat_data", "decrypt_message", "get_media_data"} or not isinstance(request.get("params"), dict):
            raise BridgeFailure(category="INVALID_PROTOCOL")
        if environment.get("WECOM_ARCHIVE_AUTHORIZED") != "true" or not environment.get("WECOM_ARCHIVE_AUTHORIZATION_EVIDENCE", "").strip():
            raise BridgeFailure(category="EXPLICIT_AUTHORIZATION_REQUIRED")
        path = Path(environment.get("WECOM_ARCHIVE_SDK_PATH", ""))
        if not path.is_absolute() or not path.is_file():
            raise BridgeFailure(category="OFFICIAL_SDK_FILE_MISSING")
        if not environment.get("WECOM_CORP_ID") or not environment.get("WECOM_ARCHIVE_SECRET"):
            raise BridgeFailure(category="ARCHIVE_CREDENTIALS_MISSING")
        runtime_mode = environment.get("WECOM_ARCHIVE_RUNTIME_MODE", "FIXTURE")
        if runtime_mode not in {"ACTUAL", "FIXTURE"}:
            raise BridgeFailure(category="INVALID_RUNTIME_MODE")
        import hashlib
        sdk_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        deployment = {"deployment_id": environment.get("WECOM_ARCHIVE_DEPLOYMENT_ID", ""),
            "sdk_sha256": sdk_hash, "bridge_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        if runtime_mode == "ACTUAL":
            from .archive_capture_evidence import current_deployment, evidence_key
            if loader is not C.CDLL or deployment != current_deployment(environment):
                raise BridgeFailure(category="ACTUAL_NATIVE_DEPLOYMENT_MISMATCH")
            evidence_key(environment)
        timeout = int(environment.get("WECOM_ARCHIVE_TIMEOUT", "25"))
        if not 1 <= timeout <= 120:
            raise BridgeFailure(category="INVALID_PARAMETER")
        if os.name == "nt":
            directory = os.add_dll_directory(str(path.parent))
        try:
            library = loader(str(path))
        except OSError:
            raise BridgeFailure(category="SDK_LOAD_FAILED") from None
        sdk = FinanceSdk(library, environment["WECOM_CORP_ID"],
            environment["WECOM_ARCHIVE_SECRET"], timeout=timeout,
            proxy=environment.get("WECOM_ARCHIVE_PROXY", ""),
            proxy_password=environment.get("WECOM_ARCHIVE_PROXY_PASSWORD", ""))
        params = request["params"]
        if operation == "get_chat_data":
            result = sdk.chat(params.get("seq"), params.get("limit"))
            code = result.get("errcode")
            if type(code) is not int:
                raise BridgeFailure(category="INVALID_SDK_JSON")
            if code != 0:
                response["errcode"] = code
                result = {}  # never emit server error text or encrypted credential details
        elif operation == "decrypt_message":
            key_paths = json.loads(environment.get("WECOM_ARCHIVE_PRIVATE_KEYS", "{}"))
            if not isinstance(key_paths, dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in key_paths.items()):
                raise BridgeFailure(category="PRIVATE_KEY_MAPPING_INVALID")
            if not key_paths and environment.get("WECOM_ARCHIVE_PRIVATE_KEY_VERSION"):
                key_paths = {environment["WECOM_ARCHIVE_PRIVATE_KEY_VERSION"]: environment.get("WECOM_ARCHIVE_PRIVATE_KEY", "")}
            password = environment.get("WECOM_ARCHIVE_KEY_PASSWORD")
            result = sdk.decrypt(params.get("envelope"), key_paths,
                                 key_password=password.encode("utf-8") if password else None)
        else:
            result = sdk.media(params.get("sdkfileid"), params.get("indexbuf"))
        response.update(sdk_code=0, result=result)
        response["native_runtime"] = {**deployment, "execution_kind": "NATIVE_SDK",
            "observation_mode": runtime_mode}
        if runtime_mode == "ACTUAL" and response["errcode"] == 0:
            from .archive_capture_evidence import sign_native_receipt
            response["receipt_hmac_sha256"] = sign_native_receipt(response, environment)
    except BridgeFailure as exc:
        response.update(sdk_code=exc.code, error_category=exc.category)
    except Exception:
        response.update(sdk_code=-1, error_category="SDK_BRIDGE_OPERATION_FAILED")
    finally:
        if sdk:
            sdk.close()
        if directory:
            directory.close()
    return response


@contextmanager
def _silence_native():
    """Native SDK output cannot contaminate the JSON stream or leak credentials."""
    saved = [os.dup(1), os.dup(2)]
    sys.stdout.flush(); sys.stderr.flush()
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1); os.dup2(sink.fileno(), 2)
            yield
    finally:
        # Flush native CRT buffers while output descriptors still point to null.
        # The SDK may use either Windows CRT; no diagnostic text is surfaced.
        for name in (["ucrtbase.dll", "msvcrt.dll"] if os.name == "nt" else [None]):
            try:
                flush = C.CDLL(name).fflush
                flush.argtypes, flush.restype = [C.c_void_p], C.c_int
                flush(None)
            except (OSError, AttributeError):
                pass
        os.dup2(saved[0], 1); os.dup2(saved[1], 2)
        for descriptor in saved:
            os.close(descriptor)


def main():
    try:
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError()
        request = json.loads(raw)
        with _silence_native():
            response = handle_request(request)
    except Exception:
        response = {"protocol": "wecom-archive-sdk-v1", "operation": "",
            "sdk_code": -1, "errcode": 0, "result": {}, "error_category": "INVALID_REQUEST"}
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
