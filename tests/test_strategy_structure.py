import importlib
import json
from pathlib import Path

import pytest

from deepstock.markets import StrategyMarket
from deepstock.strategies.cn.tail_momentum import TailMomentumConfig


ROOT = Path(__file__).resolve().parents[1]


def test_every_strategy_has_consistent_explicit_market():
    catalog = json.loads((ROOT / "config/strategy_catalog.json").read_text())["strategies"]
    registry = json.loads((ROOT / "config/strategy_registry.json").read_text())["strategies"]
    mapping = {row["strategy_id"]: row["market"] for row in registry}
    assert {row["id"] for row in catalog} == set(mapping)
    for row in catalog:
        assert row["market"] == mapping[row["id"]]
        assert StrategyMarket(row["market"]) in set(StrategyMarket)
    assert mapping["cn_etf_tail_momentum"] == "CN"
    assert mapping["csi300_opening_auction"] == "CN"
    assert mapping["ahl_global_futures_trend"] == "US"  # Initial CME universe, not an A-share validation claim.


@pytest.mark.parametrize("module,symbol", [
    ("arc", "run_arc_portfolio"), ("backtest", "StrategyConfig"), ("bull", "fixed_bull_candidates"),
    ("defensive", "frozen_defensive_config"), ("grid", "run_grid_backtest"),
    ("mean_reversion", "run_mean_reversion_backtest"), ("regime", "classify_market_regime"),
    ("turtle", "run_turtle_backtest"),
])
def test_old_us_imports_preserve_the_same_implementation(module, symbol):
    old = importlib.import_module(f"deepstock.{module}")
    new = importlib.import_module(f"deepstock.strategies.us.{module}")
    assert getattr(old, symbol) is getattr(new, symbol)


def test_tail_is_cn_only_and_has_no_us_strategy_dependencies():
    with pytest.raises(ValueError, match="no US extension"):
        TailMomentumConfig(symbol="SPY")
    old = importlib.import_module("deepstock.tail_momentum")
    new = importlib.import_module("deepstock.strategies.cn.tail_momentum")
    assert old.run_tail_momentum is new.run_tail_momentum
    source = (ROOT / "src/deepstock/strategies/cn/tail_momentum.py").read_text()
    assert "strategies.us" not in source and "deepstock.arc" not in source


def test_cn_cli_keeps_legacy_entry_points():
    for legacy, canonical, symbol in (
        ("run_cn_etf_tail_momentum", "run_etf_tail_momentum", "run_research"),
        ("download_cn_etf_tail_minutes", "download_etf_tail_minutes", "download"),
    ):
        assert getattr(importlib.import_module(f"scripts.{legacy}"), symbol) is getattr(importlib.import_module(f"scripts.cn.{canonical}"), symbol)
