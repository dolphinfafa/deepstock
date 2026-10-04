#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


BEGIN_MARKER = "# BEGIN DEEPSTOCK APP MAINTENANCE"
END_MARKER = "# END DEEPSTOCK APP MAINTENANCE"


def replace_managed_block(existing: str, block: str | None) -> str:
    retained: list[str] = []
    inside = False
    for line in existing.splitlines():
        if line == BEGIN_MARKER:
            inside = True
            continue
        if line == END_MARKER:
            inside = False
            continue
        if not inside:
            retained.append(line)
    while retained and not retained[-1].strip():
        retained.pop()
    if block:
        if retained:
            retained.append("")
        retained.extend(block.splitlines())
    return "\n".join(retained) + ("\n" if retained else "")


def build_block(project_root: Path, python: Path) -> str:
    root = shlex.quote(str(project_root))
    executable = shlex.quote(str(python))
    log_dir = shlex.quote(str(project_root / "artifacts/app/logs"))
    ingest_log = shlex.quote(str(project_root / "artifacts/app/logs/ingestion.log"))
    backup_log = shlex.quote(str(project_root / "artifacts/app/logs/backup.log"))
    ingest = (
        f"mkdir -p {log_dir} && cd {root} && "
        f"{executable} scripts/ingest_deepstock_state.py >> {ingest_log} 2>&1"
    )
    backup = (
        f"mkdir -p {log_dir} && cd {root} && "
        f"{executable} scripts/backup_deepstock_database.py >> {backup_log} 2>&1"
    )
    return "\n".join(
        (
            BEGIN_MARKER,
            "CRON_TZ=Asia/Shanghai",
            f"*/5 * * * * {ingest}",
            f"10 3 * * * {backup}",
            END_MARKER,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Deepstock app maintenance cron")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    current = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if current.returncode not in (0, 1):
        raise RuntimeError(current.stderr.strip() or "unable to read crontab")
    block = None if args.remove else build_block(project_root, args.python.resolve())
    updated = replace_managed_block(current.stdout, block)
    if args.dry_run:
        print(updated, end="")
        return 0
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    print("removed" if args.remove else "installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
