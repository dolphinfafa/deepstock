#!/usr/bin/env python3
"""Synchronize the active CSI 300 auction research state into Deepstock."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SOURCE_DIRECTORIES = (
    "auction_history",
    "auction_history_smoke",
    "auction_history_tushare",
    "auction_probe",
    "short_term_forward",
)
FORWARD_DATABASE = Path("short_term_forward/forward.sqlite3")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_if_changed(source: Path, destination: Path) -> tuple[bool, int]:
    source_stat = source.stat()
    if destination.exists():
        destination_stat = destination.stat()
        if (
            source_stat.st_size == destination_stat.st_size
            and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
        ):
            return False, 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.sync.tmp")
    shutil.copy2(source, temporary)
    if _sha256(source) != _sha256(temporary):
        raise RuntimeError(f"checksum mismatch while copying {source}")
    os.replace(temporary, destination)
    return True, source_stat.st_size


def _backup_sqlite(source: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.sync.tmp")
    if temporary.exists():
        temporary.unlink()

    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_connection:
        with sqlite3.connect(temporary, timeout=30) as destination_connection:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError(f"SQLite backup failed quick_check: {result}")
    shutil.copystat(source, temporary)
    os.replace(temporary, destination)
    return destination.stat().st_size


def synchronize(source_root: Path, destination_root: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    missing = [name for name in SOURCE_DIRECTORIES if not (source_root / name).is_dir()]
    if missing:
        raise FileNotFoundError(f"missing source directories: {', '.join(missing)}")

    copied_files = 0
    copied_bytes = 0
    scanned_files = 0
    for directory in SOURCE_DIRECTORIES:
        directory_root = source_root / directory
        for source in sorted(path for path in directory_root.rglob("*") if path.is_file()):
            relative = source.relative_to(source_root)
            if relative == FORWARD_DATABASE or source.name in {
                "forward.sqlite3-wal",
                "forward.sqlite3-shm",
            }:
                continue
            scanned_files += 1
            changed, byte_count = _copy_if_changed(
                source, destination_root / relative
            )
            copied_files += int(changed)
            copied_bytes += byte_count

    database_source = source_root / FORWARD_DATABASE
    if not database_source.is_file():
        raise FileNotFoundError(f"missing forward database: {database_source}")
    database_bytes = _backup_sqlite(
        database_source, destination_root / FORWARD_DATABASE
    )

    report = {
        "status": "ok",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "scanned_files_excluding_database": scanned_files,
        "copied_files_excluding_database": copied_files,
        "copied_bytes_excluding_database": copied_bytes,
        "sqlite_backup": str(FORWARD_DATABASE),
        "sqlite_backup_bytes": database_bytes,
    }
    report_directory = destination_root / "csi300_sync"
    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / "latest.json"
    report_temporary = report_path.with_suffix(".tmp")
    report_temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(report_temporary, report_path)
    return report


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("/srv/workspaces/zheyang/darwen/artifacts"),
    )
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=project_root / "artifacts",
    )
    args = parser.parse_args()

    lock_path = args.destination_root / ".csi300_auction_sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "skipped_locked"}, sort_keys=True))
            return 0
        report = synchronize(args.source_root, args.destination_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
