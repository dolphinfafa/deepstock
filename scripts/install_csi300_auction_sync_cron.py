#!/usr/bin/env python3
"""Install the local CSI 300 auction artifact synchronization schedule."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


BEGIN_MARKER = "# BEGIN DEEPSTOCK CSI300 AUCTION SYNC"
END_MARKER = "# END DEEPSTOCK CSI300 AUCTION SYNC"


def replace_managed_block(existing: str, block: str | None) -> str:
    lines = existing.splitlines()
    retained: list[str] = []
    inside = False
    for line in lines:
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
    command = (
        f"cd {shlex.quote(str(project_root))} && "
        f"{shlex.quote(str(python))} -m scripts.sync_csi300_auction_data "
        f">> {shlex.quote(str(project_root / 'artifacts/csi300_sync/cron.log'))} 2>&1"
    )
    return "\n".join(
        (
            BEGIN_MARKER,
            "CRON_TZ=Asia/Shanghai",
            f"50 12 * * * {command}",
            f"20 18 * * 1-5 {command}",
            f"58 23 * * * {command}",
            END_MARKER,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
