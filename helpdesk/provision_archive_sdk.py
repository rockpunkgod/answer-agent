"""Reversible official SDK file provisioning. Never loads a DLL or account API."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import struct
import sys
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile


OFFICIAL_URL = "https://wwcdn.weixin.qq.com/node/wework/images/sdk_win_v3.zip"
ZIP_SHA256 = "42c056c2ba7dda38c24ba27a7bf8430b7086b551191c620d698c23129187fb97"
HEADER_SHA256 = "231902c094ee02e94522fff4c27b539398f0226b0366cf4030b62199f58b77be"
SELECTED = ("WeWorkFinanceSdk.dll", "libcrypto-3-x64.dll", "libcurl-x64.dll", "libssl-3-x64.dll", "WeWorkFinanceSdk_C.h")
PREFIX = "C_sdk/FinanceSdkDemo/"


def pe_metadata(data):
    """Read PE architecture/import directory as bytes; no Windows loader involved."""
    if data[:2] != b"MZ":
        raise ValueError("NOT_PE_DLL")
    start = struct.unpack_from("<I", data, 0x3c)[0]
    if data[start:start+4] != b"PE\0\0":
        raise ValueError("INVALID_PE_HEADER")
    machine, count = struct.unpack_from("<HH", data, start+4)
    optional_size = struct.unpack_from("<H", data, start+20)[0]
    optional = start + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if machine != 0x8664 or magic != 0x20b:
        raise ValueError("OFFICIAL_WINDOWS_SDK_X64_REQUIRED")
    imports_rva, imports_size = struct.unpack_from("<II", data, optional+112+8)
    sections = []
    for index in range(count):
        entry = optional + optional_size + index*40
        virtual_size, rva, raw_size, offset = struct.unpack_from("<IIII", data, entry+8)
        sections.append((rva, max(virtual_size, raw_size), offset))
    def file_offset(rva):
        for address, size, offset in sections:
            if address <= rva < address + size:
                value = offset + rva - address
                if value >= len(data):
                    break
                return value
        raise ValueError("INVALID_PE_RVA")
    imports = []
    if imports_rva:
        entry = file_offset(imports_rva)
        for index in range(min(imports_size//20 + 1, 1024)):
            descriptor = struct.unpack_from("<IIIII", data, entry + index*20)
            if not any(descriptor):
                break
            name = file_offset(descriptor[3])
            end = data.find(b"\0", name, name+1024)
            if end < 0:
                raise ValueError("INVALID_PE_IMPORT_NAME")
            imports.append(data[name:end].decode("ascii"))
    return {"architecture": "x64", "machine": "0x8664", "imports": sorted(imports, key=str.lower)}


def validate_archive(raw):
    if hashlib.sha256(raw).hexdigest() != ZIP_SHA256:
        raise ValueError("OFFICIAL_ZIP_HASH_CHANGED_DO_NOT_INSTALL")
    archive = zipfile.ZipFile(io.BytesIO(raw))
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in entry.filename or entry.file_size > 32*1024*1024:
            raise ValueError("UNSAFE_ZIP_MEMBER")
    selected = {}
    for name in SELECTED:
        entry = PREFIX + name
        if archive.namelist().count(entry) != 1:
            raise ValueError("OFFICIAL_MEMBER_MISSING_OR_DUPLICATED")
        selected[name] = archive.read(entry)
    if hashlib.sha256(selected["WeWorkFinanceSdk_C.h"]).hexdigest() != HEADER_SHA256:
        raise ValueError("OFFICIAL_HEADER_HASH_CHANGED_DO_NOT_INSTALL")
    metadata = {name: pe_metadata(data) for name, data in selected.items() if name.endswith(".dll")}
    return selected, metadata


def provision(target="data/private/wecom-sdk"):
    with urlopen(OFFICIAL_URL, timeout=30) as response:
        if urlparse(response.geturl()).hostname != "wwcdn.weixin.qq.com":
            raise ValueError("UNEXPECTED_OFFICIAL_DOWNLOAD_REDIRECT")
        raw = response.read(32*1024*1024+1)
    if len(raw) > 32*1024*1024:
        raise ValueError("SDK_PACKAGE_TOO_LARGE")
    files, metadata = validate_archive(raw)
    root = Path(target).resolve()
    if not root.is_relative_to(Path.cwd().resolve()):
        raise ValueError("PROVISION_TARGET_MUST_BE_WITHIN_WORKSPACE")
    # Validate every destination before writing; never replace different local SDKs.
    for name, data in files.items():
        destination = (root / name).resolve()
        if not destination.is_relative_to(root):
            raise ValueError("PROVISION_PATH_ESCAPES_TARGET")
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError("EXISTING_SDK_FILE_DIFFERS_DO_NOT_OVERWRITE")
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        destination = root / name
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
    manifest = {"official_source_page": "https://developer.work.weixin.qq.com/document/path/91774",
        "official_url": OFFICIAL_URL, "zip_sha256": ZIP_SHA256, "zip_size": len(raw),
        "header_sha256": HEADER_SHA256, "provisioned_at": datetime.now(timezone.utc).isoformat(),
        "files": [], "python_architecture": "x64" if sys.maxsize > 2**32 else "x86",
        "dll_loaded": False, "account_api_called": False,
        "runtime_status": "FILES_PROVISIONED_NOT_EXECUTED"}
    shipped = {name.lower() for name in files}
    system = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32"
    python_dir = Path(sys.executable).parent
    for name, data in files.items():
        entry = {"filename": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        if name in metadata:
            entry.update(metadata[name])
            entry["dependencies"] = [{"name": dependency,
                "resolution": "PACKAGED" if dependency.lower() in shipped else
                "WINDOWS_API_SET" if dependency.lower().startswith(("api-ms-", "ext-ms-")) else
                "PRESENT_SYSTEM_FILE" if (system / dependency).is_file() else
                "PRESENT_PYTHON_FILE" if (python_dir / dependency).is_file() else "NEEDS_MANUAL_VERIFICATION"}
                for dependency in metadata[name]["imports"]]
        manifest["files"].append(entry)
    (root / "deployment-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Provision pinned official SDK files without execution")
    parser.add_argument("--target", default="data/private/wecom-sdk")
    args = parser.parse_args()
    try:
        manifest = provision(args.target)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "NOT_PROVISIONED", "failure_type": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, ValueError) else "OFFICIAL_PROVISION_FAILED",
            "dll_loaded": False, "account_api_called": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
