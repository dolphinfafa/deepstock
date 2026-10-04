from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.install_csi300_auction_sync_cron import (
    BEGIN_MARKER,
    END_MARKER,
    build_block,
    replace_managed_block,
)
from scripts.sync_csi300_auction_data import SOURCE_DIRECTORIES, synchronize


def _create_source(root: Path) -> None:
    for directory in SOURCE_DIRECTORIES:
        (root / directory).mkdir(parents=True)
    (root / "auction_history_tushare/minute_manifest.json").write_text(
        '{"status":"ok"}', encoding="utf-8"
    )
    database = root / "short_term_forward/forward.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO observations(value) VALUES ('first')")


def test_synchronize_copies_files_and_consistent_sqlite(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _create_source(source)

    report = synchronize(source, destination)

    assert report["status"] == "ok"
    assert (destination / "auction_history_tushare/minute_manifest.json").read_text(
        encoding="utf-8"
    ) == '{"status":"ok"}'
    with sqlite3.connect(destination / "short_term_forward/forward.sqlite3") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM observations").fetchall() == [
            ("first",)
        ]


def test_cron_block_replacement_is_scoped(tmp_path: Path) -> None:
    block = build_block(tmp_path, Path("/opt/deepstock/bin/python"))
    existing = "15 2 * * * unrelated\n" + BEGIN_MARKER + "\nold\n" + END_MARKER + "\n"

    updated = replace_managed_block(existing, block)

    assert "15 2 * * * unrelated" in updated
    assert updated.count(BEGIN_MARKER) == 1
    assert "50 12 * * *" in updated
    assert "58 23 * * *" in updated
