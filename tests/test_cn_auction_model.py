# -*- coding: utf-8 -*-
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from deepstock.strategies.cn.auction.auction import (
    ABLATION_FEATURE_SETS,
    AuctionBacktestConfig,
    CONTEXT_AUCTION_FEATURE_COLUMNS,
    FittedAuctionEnsemble,
    FittedAuctionModel,
    LIVE_AUCTION_FEATURE_COLUMNS,
    _historical_membership,
    _prospective_top_k_metrics,
    attach_execution_entries,
    evaluate_execution_backtest,
    fit_latest_auction_ensemble,
    prepare_auction_feature_frame,
    prepare_tushare_auction_feature_frame,
    rank_auction_signal,
    rank_auction_strategy,
    walk_forward_ablation,
    _assert_targets_known_before,
)
from deepstock.strategies.cn.auction.features import RAW_FEATURES


def _synthetic_ablation(seed=11, securities=30, periods=180):
    rng = np.random.default_rng(seed)
    dates = [date(2024, 1, 2) + timedelta(days=index) for index in range(periods)]
    rows = []
    for trade_date in dates:
        base_signal = rng.normal(size=securities)
        auction_signal = rng.normal(size=securities)
        target = 0.004 * base_signal + 0.006 * auction_signal + rng.normal(
            scale=0.003, size=securities
        )
        for security in range(securities):
            row = {
                "security_id": f"s{security}",
                "trade_date": trade_date,
                "target": target[security],
                "target_date": trade_date,
                "auction_tradable": True,
            }
            for name in ABLATION_FEATURE_SETS["combined"]:
                row[name] = rng.normal()
            row[ABLATION_FEATURE_SETS["base"][0]] = base_signal[security]
            row[ABLATION_FEATURE_SETS["auction"][0]] = auction_signal[security]
            rows.append(row)
    return pd.DataFrame(rows)


def test_three_way_ablation_measures_incremental_signal():
    frame = _synthetic_ablation()
    config = AuctionBacktestConfig(
        training_window_days=100,
        validation_days=40,
        minimum_cross_section=20,
        top_k=5,
    )
    report = walk_forward_ablation(
        frame, "target", "target_date", config
    )
    assert set(report["models"]) == {"base", "auction", "combined"}
    assert report["models"]["combined"]["mean_rank_ic"] > 0.5
    assert report["incremental_selected_return"]["combined_minus_base"] > 0
    assert report["incremental_daily_t_stat"]["combined_minus_base"] > 0


def test_daily_refit_uses_each_validation_date():
    frame = _synthetic_ablation()
    config = AuctionBacktestConfig(
        training_window_days=60,
        validation_days=20,
        half_life_days=20,
        minimum_cross_section=20,
        top_k=5,
        refit_frequency="daily",
    )
    report = walk_forward_ablation(frame, "target", "target_date", config)
    assert report["refit_frequency"] == "daily"
    assert report["refit_count"] == 20


def test_point_in_time_guard_rejects_future_label():
    samples = pd.DataFrame({"target_next_date": [date(2026, 9, 30)]})
    with pytest.raises(ValueError, match="future-label leakage"):
        _assert_targets_known_before(samples, date(2026, 9, 30))


def test_prospective_metrics_only_use_post_freeze_dates():
    scored = pd.DataFrame([
        {"trade_date": date(2026, 8, 28), "target": 0.50},
        {"trade_date": date(2026, 8, 31), "target": 0.01},
        {"trade_date": date(2026, 9, 1), "target": -0.01},
    ])
    selected = scored.copy()

    metrics = _prospective_top_k_metrics(
        scored, selected, "target", (0, 20)
    )

    assert metrics["dates"] == 2
    assert metrics["trades"] == 2
    assert metrics["selected_mean_return"] == pytest.approx(0.0)
    assert metrics["selected_mean_after_cost"]["20"] == pytest.approx(-0.002)


def test_historical_membership_parses_integer_yyyymmdd_point_in_time():
    snapshots = pd.DataFrame([
        {"snapshot_date": 20240131, "stock_code": "000001"},
        {"snapshot_date": 20240131, "stock_code": "000002"},
        {"snapshot_date": 20240229, "stock_code": "000002"},
        {"snapshot_date": 20240229, "stock_code": "000003"},
    ])
    dates = pd.Series([date(2024, 2, 15), date(2024, 3, 1)])
    membership = _historical_membership(dates, snapshots)
    assert membership[date(2024, 2, 15)] == {"000001", "000002"}
    assert membership[date(2024, 3, 1)] == {"000002", "000003"}


def test_auction_price_is_the_point_in_time_feature_and_label_anchor():
    dates = [date(2024, 1, 2) + timedelta(days=index) for index in range(12)]
    base_rows = []
    auction_rows = []
    for index, trade_date in enumerate(dates):
        local_open = 10.0
        local_close = 9.0 if index < 11 else 11.0
        row = {
            "security_id": "s1",
            "stock_code": "000001",
            "trade_date": trade_date,
            "open": local_open,
            "close": local_close,
            "volume": 1_000_000.0,
            "market_cap": 100_000_000_000.0,
            "opening_gap": local_open / 9.0 - 1.0,
            "historically_buyable": True,
            "target_return": 0.2,
            "target_date": trade_date + timedelta(days=1),
        }
        row.update({name: float(index + 1) for name in RAW_FEATURES})
        base_rows.append(row)
        auction_open = 10.01 if index == 11 else local_open
        auction_rows.append({
            "symbol": "000001.XSHE",
            "trade_date": trade_date,
            "open": auction_open,
            "close": auction_open,
            "high": auction_open,
            "low": auction_open,
            "volume": 100_000.0,
            "amount": auction_open * 100_000.0,
            "vwap": auction_open,
        })
    snapshots = pd.DataFrame([{
        "snapshot_date": 20231229,
        "stock_code": "000001",
    }])

    frame, diagnostics = prepare_auction_feature_frame(
        pd.DataFrame(base_rows), pd.DataFrame(auction_rows), snapshots
    )
    latest = frame[frame["trade_date"] == dates[-1]].iloc[0]

    assert latest["auction_gap"] == (10.01 / 9.0 - 1.0)
    assert latest["target_same_day"] == (11.0 / 10.01 - 1.0)
    assert latest["target_next_close"] == (12.0 / 10.01 - 1.0)
    assert diagnostics["open_match_rate_0_1pct"] == 1.0


def _tushare_production_inputs(securities=3, periods=25):
    dates = [date(2026, 1, 2) + timedelta(days=index) for index in range(periods)]
    base_rows = []
    auction_rows = []
    for security in range(securities):
        code = f"600{security:03d}"
        for index, trade_date in enumerate(dates):
            close = 10.0 + security + index * 0.01
            row = {
                "security_id": f"s{security}",
                "stock_code": code,
                "trade_date": trade_date,
                "open": close - 0.01,
                "close": close,
                "volume": 1_000_000 + index * 1_000,
                "market_cap": 10_000_000_000 + security * 1_000_000,
                "target_date": dates[index + 1] if index + 1 < periods else None,
            }
            row.update({name: float(index + security + 1) for name in RAW_FEATURES})
            base_rows.append(row)
            previous_close = 10.0 + security + (index - 1) * 0.01
            auction_volume = 100_000 + index + security * 1_000
            auction_rows.append({
                "ts_code": f"{code}.SH",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "price": previous_close * 1.01,
                "vol": auction_volume,
                "amount": previous_close * 1.01 * auction_volume,
                "pre_close": previous_close,
                "turnover_rate": 0.01 + security * 0.001,
                "volume_ratio": 1.0 + security * 0.1,
            })
    snapshots = pd.DataFrame([
        {"snapshot_date": "20251231", "stock_code": f"600{security:03d}"}
        for security in range(securities)
    ])
    return pd.DataFrame(base_rows), pd.DataFrame(auction_rows), snapshots, dates


def test_tushare_features_use_previous_day_and_anchor_pre_close():
    base, auction, snapshots, dates = _tushare_production_inputs()
    frame, diagnostics = prepare_tushare_auction_feature_frame(
        base, auction, snapshots
    )
    signal = frame[
        (frame["security_id"] == "s0") & (frame["trade_date"] == dates[20])
    ].iloc[0]
    assert signal["auction_gap"] == pytest.approx(0.01)
    assert signal["previous_close"] == pytest.approx(10.19)
    assert signal["pre_close_mismatch"] == pytest.approx(0.0)
    assert signal["target_auction_to_next_close"] == pytest.approx(
        10.21 / (10.19 * 1.01) - 1.0
    )
    assert diagnostics["maximum_cross_section"] == 3

    changed = base.copy()
    changed.loc[changed["trade_date"] == dates[20], ["close", "volume"]] *= 5
    changed_frame, _ = prepare_tushare_auction_feature_frame(
        changed, auction, snapshots
    )
    changed_signal = changed_frame[
        (changed_frame["security_id"] == "s0")
        & (changed_frame["trade_date"] == dates[20])
    ].iloc[0]
    np.testing.assert_allclose(
        changed_signal.loc[list(LIVE_AUCTION_FEATURE_COLUMNS)].to_numpy(dtype=float),
        signal.loc[list(LIVE_AUCTION_FEATURE_COLUMNS)].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_context_features_are_industry_relative_and_point_in_time():
    base, auction, snapshots, dates = _tushare_production_inputs(
        securities=6, periods=25
    )
    industries = {
        f"600{security:03d}": "bank" if security < 5 else "software"
        for security in range(6)
    }
    base["industry_name"] = base["stock_code"].map(industries)
    frame, diagnostics = prepare_tushare_auction_feature_frame(
        base, auction, snapshots
    )
    signal = frame[frame["trade_date"] == dates[20]].set_index("stock_code")
    assert bool(signal.loc["600000", "auction_industry_supported"])
    assert not bool(signal.loc["600005", "auction_industry_supported"])
    assert diagnostics["industry_count"] == 2
    assert diagnostics["market_context_scope"].startswith("point-in-time CSI 300")
    assert signal.loc[:, list(CONTEXT_AUCTION_FEATURE_COLUMNS)].notna().all().all()


def test_tushare_pre_close_mismatch_blocks_tradability():
    base, auction, snapshots, dates = _tushare_production_inputs()
    mask = (
        (auction["ts_code"] == "600000.SH")
        & (auction["trade_date"] == dates[20].strftime("%Y%m%d"))
    )
    auction.loc[mask, "pre_close"] *= 1.02
    frame, _ = prepare_tushare_auction_feature_frame(base, auction, snapshots)
    signal = frame[
        (frame["security_id"] == "s0") & (frame["trade_date"] == dates[20])
    ].iloc[0]
    assert not bool(signal["auction_tradable"])


def test_execution_label_uses_observed_0931_vwap_and_capacity():
    base, auction, snapshots, dates = _tushare_production_inputs()
    frame, _ = prepare_tushare_auction_feature_frame(base, auction, snapshots)
    entries = pd.DataFrame([{
        "stock_code": "600000",
        "trade_date": dates[20],
        "entry_0931_open": 10.30,
        "entry_0931_vwap": 10.25,
        "entry_0931_volume": 200_000,
    }])
    attached = attach_execution_entries(frame, entries)
    signal = attached[
        (attached["security_id"] == "s0")
        & (attached["trade_date"] == dates[20])
    ].iloc[0]
    assert signal["target_0931_vwap_to_next_close"] == pytest.approx(
        10.21 / 10.25 - 1.0
    )
    assert signal["entry_capacity_5pct"] == pytest.approx(102_500)


def test_execution_label_supports_next_day_0931_exit():
    base, auction, snapshots, dates = _tushare_production_inputs()
    frame, _ = prepare_tushare_auction_feature_frame(base, auction, snapshots)
    entries = pd.DataFrame([
        {
            "stock_code": "600000",
            "trade_date": dates[20],
            "entry_0931_open": 10.20,
            "entry_0931_vwap": 10.25,
            "entry_0931_volume": 200_000,
        },
        {
            "stock_code": "600000",
            "trade_date": dates[21],
            "entry_0931_open": 10.40,
            "entry_0931_vwap": 10.50,
            "entry_0931_volume": 210_000,
        },
    ])
    attached = attach_execution_entries(frame, entries)
    signal = attached[
        (attached["security_id"] == "s0")
        & (attached["trade_date"] == dates[20])
    ].iloc[0]
    assert signal["exit_next_0931_vwap"] == pytest.approx(10.50)
    assert signal["target_0931_vwap_to_next_0931_vwap"] == pytest.approx(
        10.50 / 10.25 - 1.0
    )


def test_dynamic_strategy_caps_industry_and_can_select_fewer_than_top_k():
    feature_names = ("signal",)
    absolute_model = FittedAuctionModel(
        feature_names=feature_names,
        feature_mean=np.zeros(1),
        feature_std=np.ones(1),
        return_coefficients=np.array([0.005, 0.001]),
        probability_coefficients=np.array([0.60, 0.0]),
        train_start=date(2026, 1, 1),
        train_end=date(2026, 8, 1),
        training_rows=100,
        target_column="absolute",
    )
    excess_model = FittedAuctionModel(
        feature_names=feature_names,
        feature_mean=np.zeros(1),
        feature_std=np.ones(1),
        return_coefficients=np.array([0.004, 0.001]),
        probability_coefficients=np.array([0.60, 0.0]),
        train_start=date(2026, 1, 1),
        train_end=date(2026, 8, 1),
        training_rows=100,
        target_column="excess",
    )
    absolute = FittedAuctionEnsemble((absolute_model,), (60,), "absolute")
    excess = FittedAuctionEnsemble((excess_model,), (60,), "excess")
    rows = pd.DataFrame([
        {
            "security_id": f"s{index}",
            "stock_code": f"60000{index}",
            "trade_date": date(2026, 8, 28),
            "auction_tradable": True,
            "industry_name": "bank" if index < 3 else "software",
            "signal": signal,
            "auction_market_gap_median": 0.0,
            "auction_market_positive_share": 0.5,
        }
        for index, signal in enumerate((5.0, 4.0, 3.0, 2.0, -10.0))
    ])
    ranked = rank_auction_strategy(
        absolute,
        excess,
        rows,
        date(2026, 8, 28),
        minimum_coverage=5,
        top_k=5,
        minimum_model_agreement=0.0,
        max_per_industry=2,
    )
    selected = ranked[ranked["strategy_selected"]]
    assert len(selected) == 3
    assert selected["industry_name"].value_counts().to_dict() == {
        "bank": 2,
        "software": 1,
    }


def test_ensemble_asof_excludes_unrealized_next_day_label():
    base, auction, snapshots, dates = _tushare_production_inputs(
        securities=6, periods=90
    )
    base["industry_name"] = "industry"
    frame, _ = prepare_tushare_auction_feature_frame(base, auction, snapshots)
    entries = pd.DataFrame([
        {
            "stock_code": f"600{security:03d}",
            "trade_date": trade_date,
            "entry_0931_open": 10.0,
            "entry_0931_vwap": 10.0,
            "entry_0931_volume": 100_000,
        }
        for security in range(6)
        for trade_date in dates
    ])
    frame = attach_execution_entries(frame, entries)
    config = AuctionBacktestConfig(minimum_cross_section=5)
    ensemble = fit_latest_auction_ensemble(
        frame,
        config,
        windows=(40, 60),
        as_of_date=dates[-1],
    )
    assert all(model.train_end <= dates[-3] for model in ensemble.models)


def test_execution_backtest_refuses_auction_price_fallback():
    with pytest.raises(ValueError, match="fallback is forbidden"):
        evaluate_execution_backtest(pd.DataFrame(), AuctionBacktestConfig())


def test_live_ranking_rejects_incomplete_auction_coverage():
    rows = []
    for security in range(3):
        row = {
            "security_id": f"s{security}",
            "trade_date": date(2026, 8, 28),
            "auction_tradable": True,
        }
        row.update({name: float(security) for name in LIVE_AUCTION_FEATURE_COLUMNS})
        rows.append(row)
    model = FittedAuctionModel(
        feature_names=LIVE_AUCTION_FEATURE_COLUMNS,
        feature_mean=np.zeros(len(LIVE_AUCTION_FEATURE_COLUMNS)),
        feature_std=np.ones(len(LIVE_AUCTION_FEATURE_COLUMNS)),
        return_coefficients=np.zeros(len(LIVE_AUCTION_FEATURE_COLUMNS) + 1),
        probability_coefficients=np.zeros(len(LIVE_AUCTION_FEATURE_COLUMNS) + 1),
        train_start=date(2026, 1, 1),
        train_end=date(2026, 8, 1),
        training_rows=100,
        target_column="target_0931_vwap_to_next_close",
    )
    with pytest.raises(RuntimeError, match="requires 4"):
        rank_auction_signal(
            model, pd.DataFrame(rows), date(2026, 8, 28), minimum_coverage=4
        )
