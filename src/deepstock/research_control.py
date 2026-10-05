"""Read-only, fail-closed research pause control; no trading authority."""
from __future__ import annotations

import json
from pathlib import Path


def research_pause(strategy_id: str, project_root: Path) -> dict | None:
    registry = json.loads((project_root / "config/strategy_registry.json").read_text(encoding="utf-8"))
    entry = next((s for s in registry["strategies"] if s["strategy_id"] == strategy_id), None)
    if entry is None:
        raise ValueError(f"Unregistered strategy: {strategy_id}")
    status = entry.get("research_status", "")
    if status.startswith("paused") or entry.get("execution_status") == "frozen_research_no_orders":
        return {"strategy_id": strategy_id, "status": status or "paused_frozen",
                "reason": entry.get("pause_reason", "Research is frozen; explicit user resumption is required")}
    return None
