from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.backup_deepstock_database import backup_database
from scripts.install_deepstock_app_cron import (
    BEGIN_MARKER,
    END_MARKER,
    build_block,
    replace_managed_block,
)


def test_backup_is_consistent_and_removes_expired_files(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as database:
        database.execute("create table evidence (value text not null)")
        database.execute("insert into evidence values ('verified')")
    backups = tmp_path / "backups"
    backups.mkdir()
    expired = backups / "deepstock-20000101-000000.sqlite3"
    expired.write_bytes(b"expired")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(expired, (old, old))

    result = backup_database(source, backups, retention_days=30)
    assert result["integrity_check"] == "ok"
    assert result["removed_expired"] == [expired.name]
    with sqlite3.connect(result["backup"]) as database:
        assert database.execute("select value from evidence").fetchone()[0] == "verified"


def test_app_cron_managed_block_is_idempotent(tmp_path: Path) -> None:
    block = build_block(tmp_path, Path("/opt/deepstock/bin/python"))
    installed = replace_managed_block("0 0 * * * existing\n", block)
    assert installed.count(BEGIN_MARKER) == 1
    assert installed.count(END_MARKER) == 1
    assert "*/5 * * * *" in installed
    assert "10 3 * * *" in installed
    reinstalled = replace_managed_block(installed, block)
    assert reinstalled == installed
    assert replace_managed_block(installed, None) == "0 0 * * * existing\n"
