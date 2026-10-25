from dataclasses import replace
from datetime import date, datetime, time, timedelta
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from deepstock.data.store import digest, write_json
from deepstock.strategies.cn.auction import study
from deepstock.strategies.cn.auction.auction import (
    CONTEXT_AUCTION_FEATURE_COLUMNS, fit_latest_auction_ensemble,
    prepare_tushare_auction_feature_frame, rank_auction_strategy,
)
from deepstock.strategies.cn.auction.features import build_feature_frame
from deepstock.strategies.cn.auction import forward


DAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 9, 27, tzinfo=study.SHANGHAI)


def activate(root, enabled=True):
    write_json(root / "activation.json", {
        "config_sha256": digest(study.CONFIG_PATH), "enabled": enabled,
        "policy_version": study.POLICY, "frozen_at": "2026-10-25T12:00:00+08:00",
    })


def frame_fixture(periods=140, securities=12):
    rng = np.random.default_rng(719)
    rows = []
    dates = list(pd.bdate_range("2025-01-01", periods=periods).date)
    for i, day in enumerate(dates):
        for s in range(securities):
            row = {name: rng.normal() for name in CONTEXT_AUCTION_FEATURE_COLUMNS}
            row.update({name: rng.normal() for name in study.STATES})
            row.update({
                "security_id": f"s{s}", "stock_code": f"600{s:03d}",
                "trade_date": day, "target_next_date": dates[i + 1] if i + 1 < periods else day + timedelta(days=3),
                "industry_name": f"industry-{s // 2}",
                "auction_gap_cs": rng.normal(), "auction_gap": 0.01,
                "auction_tradable": True, "execution_tradable": True,
                "auction_market_gap_median": 0.001, "auction_market_positive_share": 0.7,
            })
            target = 0.01 + 0.005 * row["auction_volume_log_ratio_cs"] + rng.normal(scale=0.002)
            row[study.TARGET] = target
            row[f"{study.TARGET}_universe_excess"] = target - 0.01
            rows.append(row)
    return pd.DataFrame(rows), dates


def selected(codes=("600001",)):
    return pd.DataFrame([{
        "stock_code": code, "strategy_selected": True, "strategy_rank": i + 1,
        "prediction_score": 0.2, "predicted_return": 0.01,
    } for i, code in enumerate(codes)])


def record(root, *, codes=("600001",), day=DAY, mode="forward", blocked=False):
    db = study.open_ledger(root)
    try:
        return study.record_decisions(
            db, {arm: selected(codes) for arm in study.ARMS}, day,
            day - timedelta(days=3), day + timedelta(days=1),
            datetime.combine(day, time(9, 27), study.SHANGHAI),
            {"entry_input_blocked": blocked}, mode=mode,
        )
    finally:
        db.close()


def entries(codes=("600001",), volume=1_000_000):
    return pd.DataFrame([{
        "stock_code": c, "trade_date": DAY, "entry_0931_open": 10.0,
        "entry_0931_vwap": 10.0, "entry_0931_volume": volume,
    } for c in codes])


def test_daily_features_and_interactions_ignore_signal_day_and_future_bars():
    dates = list(pd.bdate_range("2026-01-01", periods=35).date)
    rows, auctions = [], []
    for i, day in enumerate(dates):
        for s in range(6):
            price = 10 + s + i * (s + 1) / 100
            rows.append({"security_id": f"s{s}", "stock_code": f"600{s:03d}",
                         "trade_date": day, "open": price * 0.99, "close": price,
                         "high": price * (1.02 + s / 1000), "low": price * 0.97,
                         "volume": 1e6 + s * 3000 + i * i, "market_cap": 1e10 + s * 1e8})
            prior = 10 + s + (i - 1) * (s + 1) / 100
            auctions.append({"ts_code": f"600{s:03d}.SH", "trade_date": day.strftime("%Y%m%d"),
                             "price": prior * (1 + s / 1000), "pre_close": prior,
                             "vol": 1e5 + s * 1000, "amount": 1e6 + s * 30000,
                             "turnover_rate": 0.1 + s / 10, "volume_ratio": 1 + s / 10})
    bars = pd.DataFrame(rows)
    snapshots = pd.DataFrame([{"stock_code": f"600{s:03d}", "snapshot_date": "20251231"} for s in range(6)])

    def make(source):
        f, _ = prepare_tushare_auction_feature_frame(build_feature_frame(source), pd.DataFrame(auctions), snapshots)
        f = study.add_features(f)
        return f.loc[f["trade_date"].eq(dates[-3]), list(study.FEATURES["C"])]

    changed = bars.copy()
    changed.loc[changed["trade_date"].ge(dates[-3]), ["open", "high", "low", "close", "volume", "market_cap"]] *= 2
    pd.testing.assert_frame_equal(make(bars), make(changed))


def test_common_sample_and_future_fill_information_do_not_change_predictions():
    frame, dates = frame_fixture()
    cfg = replace(study.model_config(), minimum_cross_section=6)
    day = dates[-1]
    frame.loc[frame["trade_date"].eq(day) & frame["stock_code"].eq("600000"), study.STATES[0]] = np.nan
    initial, diagnostics = study.score(frame, day, cfg, include_full=True)
    assert diagnostics["common_decision_rows"] == 11
    assert len(initial["A_full"]) == 12
    assert all(set(initial[a].stock_code) == set(initial["C"].stock_code) for a in study.ARMS)
    changed = frame.copy()
    future = changed["target_next_date"].ge(day)
    changed.loc[future, [study.TARGET, f"{study.TARGET}_universe_excess"]] = 999
    changed.loc[changed["trade_date"].eq(day), "execution_tradable"] = False
    again, _ = study.score(changed, day, cfg)
    for arm in study.ARMS:
        np.testing.assert_allclose(initial[arm].predicted_return, again[arm].predicted_return)
        assert initial[arm].strategy_selected.tolist() == again[arm].strategy_selected.tolist()


def test_full_baseline_retains_existing_fit_and_ranking():
    frame, dates = frame_fixture()
    cfg = replace(study.model_config(), minimum_cross_section=6)
    day = dates[-1]
    scored, _ = study.score(frame, day, cfg, include_full=True)
    absolute = fit_latest_auction_ensemble(frame, cfg, as_of_date=day)
    excess = fit_latest_auction_ensemble(frame, cfg, target_column=f"{study.TARGET}_universe_excess",
                                        probability_target_column=study.TARGET, as_of_date=day)
    legacy = rank_auction_strategy(absolute, excess, frame, day, 6)
    np.testing.assert_allclose(scored["A_full"].predicted_return, legacy.predicted_return, rtol=1e-12)
    assert scored["A_full"].strategy_selected.tolist() == legacy.strategy_selected.tolist()


def test_zero_interaction_variance_is_finite_and_missing_inputs_are_not_zeroed():
    frame, dates = frame_fixture()
    frame["auction_gap_cs"] = 0.0
    frame.loc[0, study.STATES[0]] = np.nan
    augmented = study.add_features(frame)
    assert pd.isna(augmented.loc[0, study.INTERACTIONS[0]])
    scored, _ = study.score(frame, dates[-1], replace(study.model_config(), minimum_cross_section=6))
    assert all(np.isfinite(rows.predicted_return).all() for rows in scored.values())


def test_insufficient_common_universe_blocks_instead_of_filling_missing_features():
    frame, dates = frame_fixture()
    frame.loc[frame["trade_date"].eq(dates[-1]), study.STATES[0]] = np.nan
    with pytest.raises(RuntimeError, match="complete tradable"):
        study.score(frame, dates[-1], replace(study.model_config(), minimum_cross_section=6))


def test_shared_inputs_hash_checked_and_first_observation_is_immutable(tmp_path):
    frame, _ = frame_fixture(periods=3)
    prepared = study.PreparedAuctionInputs(frame, study.model_config(), NOW, {"data_versions": []})
    study.save_inputs(tmp_path, prepared, DAY, DAY - timedelta(days=3), DAY + timedelta(days=1), {})
    loaded, meta = study.load_inputs(tmp_path, DAY)
    assert loaded.feature_frame["trade_date"].tolist() == frame["trade_date"].tolist()
    numeric = frame.select_dtypes(include="number").columns
    np.testing.assert_allclose(loaded.feature_frame[numeric], frame[numeric], rtol=1e-14, atol=1e-14)
    changed = study.PreparedAuctionInputs(frame.iloc[:1], study.model_config(), NOW, {})
    study.save_inputs(tmp_path, changed, DAY, DAY - timedelta(days=3), DAY + timedelta(days=1), {})
    assert len(study.load_inputs(tmp_path, DAY)[0].feature_frame) == len(frame)
    path = tmp_path / "inputs" / f"{DAY}.frame.json.gz"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        study.load_inputs(tmp_path, DAY)


def test_forward_cutoff_rechecked_after_fitting_and_retry_is_idempotent(tmp_path, monkeypatch):
    activate(tmp_path)
    frame, _ = frame_fixture(periods=3)
    prepared = study.PreparedAuctionInputs(frame, study.model_config(), NOW, {})
    study.save_inputs(tmp_path, prepared, DAY, DAY - timedelta(days=3), DAY + timedelta(days=1), {})
    monkeypatch.setattr(study, "score", lambda *a, **kw: ({arm: selected() for arm in study.ARMS}, {}))
    ticks = iter([NOW, NOW.replace(hour=9, minute=30), NOW.replace(hour=9, minute=30)])
    assert study.run_forward(tmp_path, DAY, now_fn=lambda: next(ticks))["status"] == "input_blocked"
    assert study.pending_codes(tmp_path, DAY) == []
    assert study.run_forward(tmp_path, DAY, now_fn=lambda: NOW)["status"] == "recorded"
    assert study.run_forward(tmp_path, DAY, now_fn=lambda: NOW)["status"] == "already_recorded"
    db = study.open_ledger(tmp_path)
    assert db.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0] == 3
    assert db.execute("SELECT SUM(actionable) FROM signal_run").fetchone()[0] == 0
    db.close()


def test_no_backdating_or_naive_decision_time(tmp_path):
    db = study.open_ledger(tmp_path)
    for now in (NOW.replace(tzinfo=None), NOW.replace(day=27), NOW.replace(minute=30)):
        with pytest.raises(ValueError):
            study.record_decisions(db, {"A": selected()}, DAY, DAY - timedelta(days=3), DAY + timedelta(days=1), now, {}, mode="forward")
    db.close()


def test_missing_quotes_are_not_cash_or_completed_evidence(tmp_path):
    activate(tmp_path)
    record(tmp_path)
    study.apply_shared_exits(tmp_path, DAY + timedelta(days=1), {})
    report = study.build_report(tmp_path, as_of=DAY + timedelta(days=1))
    assert report["review"]["common_completed_days"] == 0
    assert all(r["status"] == "missing_entry" and r["net_return_20bp"] is None for r in report["daily"])
    assert report["annualized_return"] is None


def test_capacity_and_delayed_limit_exit_remain_pending_even_when_disabled(tmp_path):
    activate(tmp_path, enabled=False)
    record(tmp_path)
    fill = study.apply_shared_fills(tmp_path, DAY, entries())
    assert fill["filled"] == 3
    limit = pd.DataFrame([{"ts_code": "600001.SH", "pre_close": 10, "low": 9, "close": 9}])
    study.apply_shared_exits(tmp_path, DAY + timedelta(days=1), {DAY + timedelta(days=1): limit})
    assert study.build_report(tmp_path, as_of=DAY + timedelta(days=1))["review"]["common_completed_days"] == 0
    normal = pd.DataFrame([{"ts_code": "600001.SH", "pre_close": 9, "low": 9.5, "close": 10.5}])
    result = study.apply_shared_exits(tmp_path, DAY + timedelta(days=2), {DAY + timedelta(days=2): normal})
    assert result["closed"] == 3
    report = study.build_report(tmp_path, as_of=DAY + timedelta(days=2))
    assert report["review"]["common_completed_days"] == 1
    assert report["arms"]["C"]["stress_50bp"]["mean_daily_return"] < report["arms"]["C"]["mean_daily_return"]
    assert report["review"]["automatic_promotion"] is False


def test_unbuyable_minimum_lot_is_distinct_from_missing_quote(tmp_path):
    activate(tmp_path)
    record(tmp_path)
    result = study.apply_shared_fills(tmp_path, DAY, entries(volume=1))
    assert result["missed_capacity"] == 3
    report = study.build_report(tmp_path, as_of=DAY)
    assert report["review"]["common_completed_days"] == 1
    assert all(r["MISSED_CAPACITY"] == 1 and r["status"] == "closed" for r in report["daily"])


def test_historical_results_never_claim_prospective_or_promotion(tmp_path):
    activate(tmp_path)
    record(tmp_path, mode="historical", blocked=True)
    study.apply_shared_fills(tmp_path, DAY, entries())
    prices = pd.DataFrame([{"ts_code": "600001.SH", "pre_close": 10, "low": 10, "close": 10.5}])
    study.apply_shared_exits(tmp_path, DAY + timedelta(days=1), {DAY + timedelta(days=1): prices})
    report = study.build_report(tmp_path, mode="historical", as_of=DAY + timedelta(days=1))
    assert report["evidence_label"] == "historical_development_diagnostic"
    assert report["review"]["common_completed_days"] == 0
    assert not study.build_report(tmp_path, as_of=DAY + timedelta(days=1))["daily"]


def test_fill_batches_are_shared_and_restart_reuses_identical_snapshots(tmp_path, monkeypatch):
    base = tmp_path / "baseline"
    root = study.root_for(base)
    activate(root)
    record(root, codes=("600001", "600002"))
    db = forward.open_store(base)
    report = {"signal_date": str(DAY), "candidate_mode": "context_research_fallback", "actionable": False,
              "strategy_health": {"passed": False}, "candidates": [{"stock_code": "600001", "rank": 1}]}
    forward.record_signal_report(db, report, NOW, DAY - timedelta(days=3), DAY + timedelta(days=1), ["600001", "600002"])
    db.close()
    calls = []

    def poll(symbols, day, **kwargs):
        calls.append(symbols)
        result = entries(codes=tuple(s[:6] for s in symbols))
        result = result.assign(entry_0931_high=10.1, entry_0931_low=9.9,
                               entry_0931_close=10.0, entry_0931_amount=10_000_000,
                               bar_time=f"{DAY}T09:31:00+08:00", observed_at=f"{DAY}T09:32:00+08:00", raw_json="{}")
        return result, {"attempts": 1}

    monkeypatch.setattr(forward, "poll_tushare_rt_min_entries", poll)
    first = forward.fill_entries(base, DAY, observed_at=NOW.replace(minute=32))
    assert len(calls) == 1 and set(calls[0]) == {"600001.SH", "600002.SH"}
    assert first["research_study"]["filled"] == 6
    forward.fill_entries(base, DAY, observed_at=NOW.replace(minute=33))
    assert len(calls) == 1


def test_frozen_already_recorded_signal_retries_study_without_provider(tmp_path, monkeypatch):
    base = tmp_path / "baseline"
    db = forward.open_store(base)
    report = {"signal_date": str(DAY), "candidate_mode": "context_research_fallback", "actionable": False,
              "strategy_health": {"passed": False}, "candidates": []}
    forward.record_signal_report(db, report, NOW, DAY - timedelta(days=3), DAY + timedelta(days=1), [])
    db.close()
    monkeypatch.setattr(study, "after_baseline", lambda *a: {"status": "recorded"})
    monkeypatch.setattr(forward, "get_pro", lambda: pytest.fail("must reuse shared snapshot, not fetch"))
    result = forward.generate_signal(base, DAY, generated_at=NOW)
    assert result["status"] == "already_recorded" and result["research_study"]["status"] == "recorded"


def test_preregister_preserves_note_hashes_and_config_is_immutable(tmp_path, monkeypatch):
    notes = tmp_path / "notebook"
    notes.mkdir()
    for i in range(5):
        (notes / f"{i}.md").write_text(f"original {i}")
    monkeypatch.setattr(study, "ROOT", tmp_path)
    root = tmp_path / "study"
    value = study.initialize(root, note_id="approved-note", now=NOW)
    assert len(value["source_notes"]) == 5 and value["user_decision"] == "approved_for_test"
    value["config_sha256"] = "changed"
    write_json(root / "activation.json", value)
    with pytest.raises(ValueError, match="changed after preregistration"):
        study.activation(root)


def test_late_persistence_is_diagnostic_and_cannot_fill_or_count(tmp_path):
    activate(tmp_path)
    db = study.open_ledger(tmp_path)
    ticks = iter([NOW, NOW.replace(minute=30)])
    result = study.record_decisions(
        db, {arm: selected() for arm in study.ARMS}, DAY, DAY - timedelta(days=3),
        DAY + timedelta(days=1), NOW, {}, mode="forward", persistence_clock=lambda: next(ticks),
    )
    db.close()
    assert result["status"] == "missed_decision_cutoff"
    assert study.pending_codes(tmp_path, DAY) == []
    assert study.apply_shared_fills(tmp_path, DAY, entries())["filled"] == 0
    report = study.build_report(tmp_path, as_of=DAY + timedelta(days=1))
    assert report["late_persistence_days"] == [str(DAY)] and not report["daily"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_original_signal_persisted_before_study_and_failure_is_isolated(tmp_path, monkeypatch, corrupt):
    base = tmp_path / "baseline"
    root = study.root_for(base)
    activate(root)
    if corrupt:
        value = json.loads((root / "activation.json").read_text())
        value["config_sha256"] = "changed"
        write_json(root / "activation.json", value)
    prepared_frame, _ = frame_fixture(periods=3)
    report = {"signal_date": str(DAY), "candidate_mode": "context_research_fallback", "actionable": False,
              "strategy_health": {"passed": False}, "candidates": [{"stock_code": "600001", "rank": 1}]}
    calls = []

    def predict(args, prepared_callback=None):
        calls.append("original")
        if prepared_callback:
            prepared_callback(study.PreparedAuctionInputs(prepared_frame, study.model_config(), NOW, {}))
        return report

    def score(*args, **kwargs):
        with forward.open_store(base) as db:
            assert db.execute("SELECT COUNT(*) FROM signal_run").fetchone()[0] == 1
        db.close()
        return {arm: selected() for arm in study.ARMS}, {}

    real_forward = study.run_forward
    monkeypatch.setattr(study, "run_forward", lambda root, day: real_forward(root, day, now_fn=lambda: NOW))
    monkeypatch.setattr(study, "score", score)
    monkeypatch.setattr(forward, "_now", lambda: NOW)
    monkeypatch.setattr(forward, "get_pro", lambda: object())
    monkeypatch.setattr(forward, "trading_neighbors", lambda *a: (DAY - timedelta(days=3), DAY + timedelta(days=1)))
    monkeypatch.setattr(forward, "load_current_hs300", lambda *a: SimpleNamespace(
        members=[SimpleNamespace(stock_code="600001")], missing_codes=[]))
    monkeypatch.setattr(forward, "run_auction_prediction", predict)
    result = forward.generate_signal(base, DAY)
    assert result["status"] == "recorded"
    assert result["research_study"]["status"] == ("input_blocked" if corrupt else "recorded")
    assert calls == ["original"]
    repeated = forward.generate_signal(base, DAY)
    assert repeated["status"] == "already_recorded" and calls == ["original"]


def test_settlement_shares_market_response_and_disabled_study_still_closes(tmp_path, monkeypatch):
    base = tmp_path / "baseline"
    root = study.root_for(base)
    activate(root, enabled=False)
    record(root)
    study.apply_shared_fills(root, DAY, entries())
    next_day = DAY + timedelta(days=1)
    calls = []

    class Pro:
        def daily(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame([{"ts_code": "600001.SH", "pre_close": 10, "low": 10, "close": 10.5}])

    monkeypatch.setattr(forward, "get_pro", Pro)
    monkeypatch.setattr(forward, "_trading_dates", lambda *a: [next_day])
    result = forward.settle_positions(base, next_day)
    assert len(calls) == 1 and result["research_study"]["closed"] == 3
    assert result["status"] == "nothing_pending"
    assert study.build_report(root, as_of=next_day)["review"]["common_completed_days"] == 1


def test_replay_blocks_provider_calls_and_restores_environment(tmp_path, monkeypatch):
    import os
    from scripts import auction_state_study
    from deepstock.strategies.cn.auction.providers import CapturedTushare
    activate(tmp_path)
    monkeypatch.delenv("DEEPSTOCK_AUCTION_OFFLINE", raising=False)

    def replay(*args):
        assert os.environ["DEEPSTOCK_AUCTION_OFFLINE"] == "true"
        with pytest.raises(RuntimeError, match="cannot call providers"):
            CapturedTushare(object()).stk_auction()
        return {"status": "completed"}

    monkeypatch.setattr(auction_state_study, "_backtest", replay)
    assert auction_state_study.backtest(tmp_path, tmp_path)["status"] == "completed"
    assert "DEEPSTOCK_AUCTION_OFFLINE" not in os.environ
