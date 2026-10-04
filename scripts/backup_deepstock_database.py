#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deepstock.web.config import settings


def sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("the built-in backup job currently supports SQLite only")
    return Path(url.removeprefix(prefix)).resolve()


def backup_database(source: Path, destination_dir: Path, retention_days: int) -> dict:
    if not source.exists():
        raise FileNotFoundError(source)
    destination_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    destination = destination_dir / f"deepstock-{now:%Y%m%d-%H%M%S}.sqlite3"
    with sqlite3.connect(source) as input_db, sqlite3.connect(destination) as output_db:
        input_db.backup(output_db)
        integrity = output_db.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity check failed: {integrity}")

    cutoff = now - timedelta(days=retention_days)
    removed = []
    for candidate in destination_dir.glob("deepstock-*.sqlite3"):
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
        if candidate != destination and modified < cutoff:
            candidate.unlink()
            removed.append(candidate.name)
    return {
        "status": "ok",
        "source": str(source),
        "backup": str(destination),
        "bytes": destination.stat().st_size,
        "integrity_check": integrity,
        "removed_expired": removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an online SQLite backup")
    parser.add_argument(
        "--destination",
        type=Path,
        default=settings.project_root / "artifacts/app/backups",
    )
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    if args.retention_days < 1:
        raise ValueError("retention days must be positive")
    result = backup_database(
        sqlite_path_from_url(settings.database_url),
        args.destination.resolve(),
        args.retention_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
