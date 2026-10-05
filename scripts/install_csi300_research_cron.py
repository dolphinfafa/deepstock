#!/usr/bin/env python3
"""Restore the sole Darwen research writer and quota-limited minute backfill."""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.run_csi300_minute_backfill import SOURCE_PYTHON, SOURCE_ROOT


BEGIN = "# BEGIN DEEPSTOCK CSI300 MINUTE BACKFILL"
END = "# END DEEPSTOCK CSI300 MINUTE BACKFILL"


def replace_block(existing: str, begin: str, end: str, block: str | None) -> str:
    retained = []
    inside = False
    for line in existing.splitlines():
        if line.strip() == begin:
            if inside:
                raise ValueError("nested cron block")
            inside = True
        elif line.strip() == end:
            if not inside:
                raise ValueError("unmatched cron marker")
            inside = False
        elif not inside:
            retained.append(line)
    if inside:
        raise ValueError("unterminated cron block")
    output = "\n".join(retained).rstrip()
    return output + ("\n\n" if output and block else "") + (block or "") + "\n"


def backfill_block(root: Path, python: Path) -> str:
    command = (
        f"cd {shlex.quote(str(root))} && {shlex.quote(str(python))} "
        f"-m scripts.run_csi300_minute_backfill >> {shlex.quote(str(root / 'artifacts/csi300_sync/minute_backfill_cron.log'))} 2>&1"
    )
    return "\n".join((BEGIN, "CRON_TZ=Asia/Shanghai", f"30 11 * * * {command}", f"35 12 * * * {command}", END))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    current = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if current.returncode and "no crontab" not in current.stderr.lower():
        raise RuntimeError(current.stderr)
    # Reuse the existing forward schedule, interpreter and sole SQLite writer.
    forward = subprocess.run([str(SOURCE_PYTHON), "-c",
        "from scripts.install_short_term_forward_cron import cron_block; print(cron_block())"],
        cwd=SOURCE_ROOT, text=True, capture_output=True, check=True).stdout.strip()
    updated = replace_block(current.stdout, "# BEGIN DARWEN SHORT TERM FORWARD", "# END DARWEN SHORT TERM FORWARD", forward)
    # Remove the inherited backfill block to avoid duplicate quota consumers.
    updated = replace_block(updated, "# BEGIN DARWEN AUCTION MINUTE BACKFILL", "# END DARWEN AUCTION MINUTE BACKFILL", None)
    updated = replace_block(updated, BEGIN, END, backfill_block(root, Path(sys.executable)))
    if args.dry_run:
        print(updated, end="")
        return
    backup = root / "artifacts/csi300_sync/crontab-before-research-restore.txt"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_text(current.stdout, encoding="utf-8")
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    print("installed: original single-writer forward schedule and quota-limited backfill")


if __name__ == "__main__":
    main()
