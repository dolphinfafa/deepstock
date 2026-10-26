"""Unpack the hash-pinned official SDK bundle for the isolated paper process.

The bundle contains the unmodified official 1051.1 Python client, its license,
and the SDK-required pure-Python protobuf 5.29.5 distribution. It stays in
machine-local artifacts; no existing Python package is installed or replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "artifacts/ibkr-paper-pilot/sdk"
BUNDLE_SHA256 = "f72a32dba9a8a77f7d762a706f8191768f96d6d3b36c00b516e446643e3ca0c3"
TREE_SHA256 = "1edaa81999a777edea5c44a8ca726952fababa6d6c7ef1df1041fd5ab1ce67c2"


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted((path for path in root.rglob("*") if path.is_file()
                    and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
                    and path.name != "bundle-manifest.json"),
                   key=lambda path: path.relative_to(root).as_posix())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" +
                      hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def verify_sdk(root: Path) -> None:
    manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("sdk_version") != "1051.1" or
            manifest.get("protobuf_version") != "5.29.5" or tree_digest(root) != TREE_SHA256):
        raise ValueError("isolated paper SDK content does not match reviewed bundle")


def prepare(bundle: Path, target: Path = TARGET) -> None:
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != BUNDLE_SHA256:
        raise ValueError("paper SDK archive checksum mismatch")
    if target.exists():
        verify_sdk(target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="paper-sdk-", dir=target.parent))
    try:
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in info.filename:
                    raise ValueError("invalid SDK archive path")
                output = temporary.joinpath(*path.parts)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.read(info))
        verify_sdk(temporary)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.bundle)
    print(json.dumps(dict(status="SDK_VERIFIED", sdk_version="1051.1", tree_sha256=TREE_SHA256)))


if __name__ == "__main__":
    main()
