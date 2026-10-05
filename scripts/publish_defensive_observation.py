#!/usr/bin/env python3
"""Publish aggregate defensive research results; no TWS or raw-price upload."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from deepstock.observation_reporting import validate_bundle
from deepstock.web.config import settings


def build_bundle(root: Path) -> dict:
    research = root / "artifacts/research/strategy-governance"
    paper = root / "artifacts/paper/defensive-etf"
    def read(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))
    bundle = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan": read(paper / "latest.json"),
        "summary": read(research / "adaptive-defensive-latest/summary.json"),
        "walkforward_manifest": read(research / "adaptive-defensive-walkforward/manifest.json"),
        "snapshot": read(research / "adaptive-defensive-snapshot.json"),
        "observations": [
            {key: row[key] for key in ("plan_id", "data_date")}
            for line in (paper / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
            for row in [json.loads(line)]
        ],
    }
    validate_bundle(bundle)
    return bundle


def main() -> None:
    if not settings.node_token:
        raise ValueError("DEEPSTOCK_NODE_TOKEN is not configured")
    response = httpx.post(
        f"{settings.public_base_url}/api/agent/research/defensive-observation",
        headers={"Authorization": f"Bearer {settings.node_token}"},
        json=build_bundle(settings.project_root), timeout=30,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
