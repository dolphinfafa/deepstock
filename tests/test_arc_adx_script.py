from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.run_arc_adx_backtest import load_ohlc_manifest, transition_summary


def test_load_ohlc_manifest_requires_auditable_coverage(tmp_path: Path) -> None:
    path = tmp_path / "spy.manifest.json"
    path.write_text(
        json.dumps(
            {
                "provider": "Norgate Data",
                "adjustment": "Norgate TOTALRETURN OHLC",
                "actual_from": "1993-01-29",
                "actual_to": "2026-09-29",
                "row_count": 8480,
            }
        ),
        encoding="utf-8",
    )

    assert load_ohlc_manifest(path)["provider"] == "Norgate Data"


def test_load_ohlc_manifest_rejects_missing_coverage(tmp_path: Path) -> None:
    path = tmp_path / "spy.manifest.json"
    path.write_text(json.dumps({"provider": "Norgate Data"}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing fields"):
        load_ohlc_manifest(path)


def test_load_ohlc_manifest_accepts_massive_rows_alias(tmp_path: Path) -> None:
    path = tmp_path / "spy.manifest.json"
    path.write_text(
        json.dumps(
            {
                "provider": "Massive",
                "adjustment": "split-adjusted OHLC",
                "actual_from": "2021-09-30",
                "actual_to": "2026-09-29",
                "rows": 1254,
            }
        ),
        encoding="utf-8",
    )

    assert load_ohlc_manifest(path)["row_count"] == 1254


def test_transition_summary_reports_raw_and_controlled_switches() -> None:
    signals = pd.DataFrame(
        {
            "raw_regime": ["range", "bull", "range", "bull", "bull"],
            "regime": ["range", "range", "range", "bull", "bull"],
        },
        index=pd.bdate_range("2024-01-02", periods=5),
    )

    summary = transition_summary(signals)

    assert summary["raw_state_switches"] == 3
    assert summary["state_switches"] == 1
    assert summary["raw_bull_range_switches"] == 3
    assert summary["bull_range_switches"] == 1
