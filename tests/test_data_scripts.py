from __future__ import annotations

import pandas as pd
import pytest

from deepstock.backtest import StrategyConfig
from scripts.merge_ticker_alias import merge_alias
from scripts.run_defensive_etf_backtest import load_prices


def test_merge_ticker_alias_uses_alias_before_switch() -> None:
    base = pd.DataFrame(
        {
            "date": ["2022-06-09", "2022-06-10"],
            "symbol": ["META", "META"],
            "adjusted_close": [190.0, 195.0],
        }
    )
    alias = pd.DataFrame(
        {
            "date": ["2022-06-07", "2022-06-08"],
            "symbol": ["FB", "FB"],
            "adjusted_close": [185.0, 188.0],
        }
    )
    merged = merge_alias(base, alias, "META", "FB", "2022-06-09")
    assert merged["symbol"].unique().tolist() == ["META"]
    assert merged["date"].tolist() == ["2022-06-07", "2022-06-08", "2022-06-09", "2022-06-10"]


def test_merge_ticker_alias_rejects_duplicate_dates() -> None:
    base = pd.DataFrame({"date": ["2022-06-09"], "symbol": ["META"], "adjusted_close": [190.0]})
    alias = pd.DataFrame({"date": ["2022-06-08"], "symbol": ["FB"], "adjusted_close": [188.0]})
    with pytest.raises(ValueError, match="duplicate"):
        merge_alias(pd.concat([base, base]), alias, "META", "FB", "2022-06-09")


def test_defensive_backtest_uses_common_complete_history(tmp_path) -> None:
    config = StrategyConfig()
    rows = []
    for symbol in config.symbols:
        rows.append(
            {
                "date": "2024-01-03",
                "symbol": symbol,
                "adjusted_close": 100.0,
            }
        )
    rows.append(
        {
            "date": "2024-01-02",
            "symbol": config.benchmark,
            "adjusted_close": 99.0,
        }
    )
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    prices = load_prices(path, config)

    assert prices.index.tolist() == [pd.Timestamp("2024-01-03")]
    assert prices.columns.tolist() == list(config.symbols)
