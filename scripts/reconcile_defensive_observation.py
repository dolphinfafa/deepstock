#!/usr/bin/env python3
"""Correct mismatched research evidence, preserving original observations/decisions."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from deepstock.defensive import config_hash, frozen_defensive_config


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    governance = root / "artifacts/research/strategy-governance"
    now = datetime.now(timezone.utc)
    archive = governance / "reconciliation-archive" / now.strftime("%Y%m%dT%H%M%S%fZ")
    archive.mkdir(parents=True, exist_ok=False)
    for name in ("adaptive-defensive-latest", "adaptive-defensive-walkforward", "adaptive-defensive-snapshot.json", "decisions.jsonl"):
        source = governance / name
        if source.is_dir():
            shutil.copytree(source, archive / name)
        elif source.is_file():
            shutil.copy2(source, archive / name)
    old_summary = archive / "adaptive-defensive-latest/summary.json"
    previous_config = json.loads(old_summary.read_text(encoding="utf-8")).get("config") if old_summary.exists() else None
    prices = "artifacts/research/norgate/defensive_etf_prices.csv"
    latest = "artifacts/research/strategy-governance/adaptive-defensive-latest"
    wf = "artifacts/research/strategy-governance/adaptive-defensive-walkforward"
    snapshot = "artifacts/research/strategy-governance/adaptive-defensive-snapshot.json"
    commands = [
        ["scripts/run_defensive_etf_backtest.py", "--profile", "adaptive", "--prices", prices, "--output-dir", latest],
        ["scripts/run_adaptive_defensive_walkforward.py", "--prices", prices, "--output-dir", wf],
        ["scripts/build_defensive_governance_snapshot.py", "--prices", prices, "--daily", latest + "/daily_results.csv",
         "--walkforward", wf + "/walkforward_results.csv", "--manifest", wf + "/manifest.json",
         "--plan", "artifacts/paper/defensive-etf/latest.json", "--observations", "artifacts/paper/defensive-etf/observations.jsonl", "--output", snapshot],
    ]
    for args in commands:
        subprocess.run([sys.executable, *args], cwd=root, check=True)
    corrected = json.loads((root / snapshot).read_text(encoding="utf-8"))
    audit = {
        "recorded_at_utc": now.isoformat(), "archive": str(archive.relative_to(root)),
        "reason": "Daily baseline defaults differed from the unchanged frozen adaptive configuration",
        "previous_config": previous_config, "corrected_config_hash": config_hash(frozen_defensive_config()),
        "corrected_snapshot": corrected, "original_decision_ledger_preserved": True,
        "observation_clock_restarted": False, "orders_submitted": False,
    }
    (governance / "adaptive-defensive-reconciliation.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "reconciled", "archive": audit["archive"], "snapshot": corrected}, indent=2))


if __name__ == "__main__":
    main()
