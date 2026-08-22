from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from deepstock.backtest import StrategyConfig
from deepstock.observation import record_observation
from deepstock.paper_plan import create_defensive_etf_plan
from scripts.generate_defensive_etf_plan import main as generate_plan_main


def defensive_prices() -> pd.DataFrame:
    dates = pd.date_range("2023-01-02", periods=420, freq="B")
    config = StrategyConfig()
    return pd.DataFrame(
        {symbol: 100 + index * 10 + pd.Series(range(len(dates)), index=dates) * 0.1
         for index, symbol in enumerate(config.symbols)},
        index=dates,
    )


def test_defensive_plan_is_deterministic_and_order_free() -> None:
    generated = datetime(2026, 8, 22, tzinfo=timezone.utc)
    first = create_defensive_etf_plan(defensive_prices(), generated_at=generated)
    second = create_defensive_etf_plan(defensive_prices(), generated_at=generated)

    assert first["strategy"] == "adaptive_defensive_etf"
    assert first["mode"] == "paper"
    assert first["status"] == "ready_for_review"
    assert first["plan_id"] == second["plan_id"]
    assert sum(first["target_weights"].values()) == pytest.approx(1.0)


def test_observation_rejects_duplicate_plan_id(tmp_path) -> None:
    plan = create_defensive_etf_plan(defensive_prices())
    records = tmp_path / "observations.jsonl"

    record = record_observation(plan, records, recorded_at=datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert record["plan_id"] == plan["plan_id"]
    assert len(records.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(ValueError, match="already exists"):
        record_observation(plan, records)


def test_generator_drops_pre_inception_missing_rows(tmp_path, monkeypatch) -> None:
    prices = defensive_prices().reset_index(names="date").melt(
        id_vars="date", var_name="symbol", value_name="adjusted_close"
    )
    prices = prices[~((prices["symbol"] == "GLD") & (prices["date"] < "2023-04-03"))]
    source = tmp_path / "prices.csv"
    output = tmp_path / "plan.json"
    prices.to_csv(source, index=False)
    monkeypatch.setattr(
        "sys.argv", ["generate_defensive_etf_plan.py", "--prices", str(source), "--output", str(output)]
    )

    assert generate_plan_main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["data_date"] == "2024-08-09"
