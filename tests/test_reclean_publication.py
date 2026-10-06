from datetime import date
import json

import pandas as pd
import pytest

from scripts import rerun_clean_research as rerun
from scripts.backtest_auction_features import bound_replay_signal_dates
from deepstock.data.store import write_json


def test_replay_preserves_pre_start_training_and_future_exit_labels():
    frame = pd.DataFrame({
        "trade_date": [date(2025, 12, 31), date(2026, 8, 28), date(2026, 8, 31)],
        "target_next_date": [date(2026, 1, 5), date(2026, 8, 31), date(2026, 9, 1)]})
    bounded = bound_replay_signal_dates(frame, date(2026, 8, 28))
    assert bounded.trade_date.tolist() == [date(2025, 12, 31), date(2026, 8, 28)]
    assert bounded.target_next_date.iloc[-1] == date(2026, 8, 31)
    assert len(frame) == 3


def test_controller_publication_ids_are_unique_between_runs(tmp_path):
    first, second = tmp_path / "arc-first", tmp_path / "arc-second"
    assert rerun.publication_id("arc", first / "controller", first) != rerun.publication_id("arc", second / "controller", second)


def test_oos_metrics_cost_and_legacy_code_provenance_are_consistent(tmp_path, monkeypatch):
    monkeypatch.setattr(rerun, "ROOT", tmp_path)
    folder = tmp_path / "run"
    folder.mkdir()
    summary = folder / "summary.json"
    write_json(summary, {"total_return": 99, "sharpe_ratio": 99, "maximum_drawdown": -.99,
                         "total_turnover": 99, "total_transaction_cost": 99, "config": {"transaction_cost_bps": 10.0}})
    daily = folder / "daily.csv"
    pd.DataFrame({"date": ["2021-08-20", "2021-08-23", "2021-08-24"],
                  "portfolio_net_return": [.9, -.01, .02], "turnover": [9, 1, 2],
                  "transaction_cost": [.009, .001, .002]}).to_csv(daily, index=False)
    rerun.publish("test", folder, summary, daily, "oos", "fixed")
    result = json.loads((folder / "publication.json").read_text(encoding="utf-8"))
    assert result["metrics"]["total_return"] == pytest.approx(.99 * 1.02 - 1)
    assert result["metrics"]["maximum_drawdown"] == pytest.approx(-.01)
    assert result["metrics"]["total_turnover"] == 3
    assert result["metrics"]["total_transaction_cost"] == pytest.approx(.003)
    assert result["annualization"]["sessions"] == 2 and "10bp" in result["annualization"]["cost_basis"]
    assert result["code_version"] is None
    assert result["code_provenance"]["status"] == "legacy_start_not_captured"
    before = (folder / "publication.json").read_bytes()
    with pytest.raises(FileExistsError):
        rerun.publish("test", folder, summary, daily, "oos", "fixed")
    assert (folder / "publication.json").read_bytes() == before


def test_turtle_summary_serializes_numpy_scalars_and_keeps_all_candidates(tmp_path):
    rows = [{"candidate": "baseline_55_20_5" if i == 0 else f"candidate-{i}",
             "standalone_out_of_sample_trading_days": 1255,
             "standalone_out_of_sample_total_return": .6} for i in range(6)]
    pd.DataFrame(rows).to_csv(tmp_path / "parameter_results.csv", index=False)
    write_json(tmp_path / "manifest.json", {"parameter_count": 6, "data_versions": []})
    rerun.turtle_summary(tmp_path, tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["trading_days"] == 1255
    assert summary["config"]["candidate"] == "baseline_55_20_5"
    assert summary["config"]["all_candidates_preserved"] is True
    assert summary["config"]["transaction_cost_bps"] == 5.0
