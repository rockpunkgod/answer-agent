"""Verify provisioned official DLLs and local allocation, without account calls."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "data/private/wecom-sdk"
    manifest = json.loads((directory / "deployment-manifest.json").read_text(encoding="utf-8"))
    verified = []
    for item in manifest["files"]:
        name = item["filename"]
        if Path(name).name != name:
            raise ValueError("invalid SDK manifest filename")
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("SDK file differs from provisioned official archive")
        verified.append(name)
    if sys.maxsize <= 2**32 or os.name != "nt":
        raise RuntimeError("this official deployment requires Windows x64")
    functions = ("NewSdk", "Init", "DestroySdk", "GetChatData", "DecryptData",
        "NewSlice", "FreeSlice", "GetContentFromSlice", "GetSliceLen", "NewMediaData",
        "FreeMediaData", "GetMediaData", "GetData", "GetDataLen", "GetOutIndexBuf",
        "GetIndexLen", "IsMediaDataFinish")
    allocations = []
    with os.add_dll_directory(str(directory)):
        library = ctypes.CDLL(str(directory / "WeWorkFinanceSdk.dll"))
        for name in functions:
            getattr(library, name)
        for allocate, release in (("NewSdk", "DestroySdk"), ("NewSlice", "FreeSlice"),
                                  ("NewMediaData", "FreeMediaData")):
            creator, destructor = getattr(library, allocate), getattr(library, release)
            creator.argtypes, creator.restype = [], ctypes.c_void_p
            destructor.argtypes, destructor.restype = [ctypes.c_void_p], None
            pointer = creator()
            if not pointer:
                raise RuntimeError("official SDK allocation failed")
            try:
                allocations.append(allocate)
            finally:
                destructor(pointer)
    result = {"status": "SDK_LOADED_LOCAL_ALLOCATION_VERIFIED", "files_verified": verified,
        "exports_verified": list(functions), "allocated_and_released": allocations,
        "init_called": False, "account_network_called": False,
        "actual_message_pull_verified": False, "sdk_sha256": manifest["files"][0]["sha256"]}
    output = ROOT / "data/private/acceptance/20260930-official-sdk-local-check.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
