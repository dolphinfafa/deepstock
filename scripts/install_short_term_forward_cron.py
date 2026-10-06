# -*- coding: utf-8 -*-
"""Install or remove the persistent cron schedule for forward paper trading."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile


BEGIN_MARKER = "# BEGIN DEEPSTOCK SHORT TERM FORWARD"
END_MARKER = "# END DEEPSTOCK SHORT TERM FORWARD"
WORKSPACE = Path(__file__).resolve().parents[1]
PYTHON = Path(__import__("sys").executable)


def cron_block(workspace: Path = WORKSPACE, python: Path = PYTHON) -> str:
    root = workspace / "artifacts/short_term_forward"
    log = root / "cron.log"
    command = f"cd {workspace} && {python} -m scripts.short_term_forward"
    output = f">> {log} 2>&1"
    return "\n".join([
        BEGIN_MARKER,
        "CRON_TZ=Asia/Shanghai",
        f"*/10 0-8 * * 1-5 {command} collect {output}",
        f"0-20/5 9 * * 1-5 {command} collect {output}",
        f"24 9 * * 1-5 {command} collect {output}",
        f"*/10 15-23 * * 1-5 {command} collect {output}",
        f"*/30 * * * 0,6 {command} collect {output}",
        f"27,29 9 * * 1-5 {command} signal {output}",
        f"31 9 * * 1-5 {command} fill --wait-seconds 90 {output}",
        f"33,38,43 9 * * 1-5 {command} fill --wait-seconds 0 {output}",
        f"0 10 * * 1-5 {command} fill --wait-seconds 0 {output}",
        f"20 15 * * 1-5 {command} settle {output}",
        f"5 16,18 * * 1-5 {command} settle {output}",
        f"15 16,18 * * 1-5 {command} settle-counterfactual {output}",
        f"10 18 * * 1-5 {command} report {output}",
        END_MARKER,
    ])


def replace_managed_block(existing: str, block: str | None) -> str:
    lines = existing.splitlines()
    output: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            inside = True
            continue
        if line.strip() == END_MARKER:
            inside = False
            continue
        if not inside:
            output.append(line)
    while output and not output[-1].strip():
        output.pop()
    if block:
        if output:
            output.append("")
        output.extend(block.splitlines())
    return "\n".join(output) + ("\n" if output else "")


def current_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if result.returncode == 0:
        return result.stdout
    if "no crontab" in result.stderr.lower():
        return ""
    raise RuntimeError(result.stderr.strip() or "cannot read crontab")


def install_crontab(content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        subprocess.run(["crontab", handle.name], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    WORKSPACE.joinpath("artifacts/short_term_forward").mkdir(
        parents=True, exist_ok=True
    )
    updated = replace_managed_block(
        current_crontab(), None if args.remove else cron_block()
    )
    if args.dry_run:
        print(updated, end="")
        return
    install_crontab(updated)
    print("removed" if args.remove else "installed")


if __name__ == "__main__":
    main()
