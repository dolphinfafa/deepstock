# -*- coding: utf-8 -*-
"""Opening-auction feature construction and matched ablation backtests."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from deepstock.strategies.cn.auction.features import FEATURE_COLUMNS, RAW_FEATURES


AUCTION_RAW_FEATURES = (
    "auction_gap",
    "auction_path",
    "auction_range",
    "auction_vwap_deviation",
    "auction_volume_log_ratio",
    "auction_amount_to_cap",
)
AUCTION_FEATURE_COLUMNS = tuple(f"{name}_cs" for name in AUCTION_RAW_FEATURES) + (
    "auction_market_gap_median",
    "auction_market_positive_share",
)
ABLATION_FEATURE_SETS = {
    "base": FEATURE_COLUMNS,
    "auction": AUCTION_FEATURE_COLUMNS,
    "combined": FEATURE_COLUMNS + AUCTION_FEATURE_COLUMNS,
}

# Production features are limited to fields available from Tushare's completed
# opening-auction snapshot. The older FTShare-only path/range/VWAP fields above
# remain available for the historical information-content ablation.
LIVE_AUCTION_RAW_FEATURES = (
    "auction_gap",
    "auction_volume_log_ratio",
    "auction_amount_to_cap",
    "auction_turnover_rate",
    "auction_volume_ratio_log",
)
LIVE_AUCTION_FEATURE_COLUMNS = tuple(
    f"{name}_cs" for name in LIVE_AUCTION_RAW_FEATURES
) + (
    "auction_market_gap_median",
    "auction_market_positive_share",
)
PRODUCTION_AUCTION_FEATURE_COLUMNS = (
    "auction_volume_log_ratio_cs",
    "auction_amount_to_cap_cs",
    "auction_turnover_rate_cs",
    "auction_volume_ratio_log_cs",
)
INDUSTRY_RELATIVE_RAW_FEATURES = (
    "auction_volume_log_ratio",
    "auction_amount_to_cap",
    "auction_turnover_rate",
    "auction_volume_ratio_log",
)
INDUSTRY_RELATIVE_FEATURE_COLUMNS = tuple(
    f"{name}_industry_cs" for name in INDUSTRY_RELATIVE_RAW_FEATURES
)
MARKET_CONTEXT_FEATURE_COLUMNS = (
    "auction_market_gap_median",
    "auction_market_positive_share",
    "auction_market_gap_dispersion",
    "auction_market_large_gap_share",
    "auction_market_volume_ratio_median",
)
CONTEXT_AUCTION_FEATURE_COLUMNS = (
    PRODUCTION_AUCTION_FEATURE_COLUMNS
    + INDUSTRY_RELATIVE_FEATURE_COLUMNS
    + MARKET_CONTEXT_FEATURE_COLUMNS
)
DEFAULT_ENSEMBLE_WINDOWS = (40, 60, 80, 120)
PRODUCTION_POLICY_VERSION = "auction_context_v1_frozen_20260829"
PRODUCTION_POLICY_FROZEN_AT = date(2026, 8, 29)
PROSPECTIVE_EVALUATION_START = date(2026, 8, 31)
LIVE_AUCTION_FEATURE_SETS = {
    "base": FEATURE_COLUMNS,
    "auction": PRODUCTION_AUCTION_FEATURE_COLUMNS,
    "context": CONTEXT_AUCTION_FEATURE_COLUMNS,
    "combined": FEATURE_COLUMNS + PRODUCTION_AUCTION_FEATURE_COLUMNS,
}


@dataclass(frozen=True)
class AuctionBacktestConfig:
    training_window_days: int = 252
    validation_days: int = 60
    half_life_days: int = 126
    ridge_alpha: float = 10.0
    target_clip: float = 0.12
    minimum_cross_section: int = 240
    top_k: int = 5
    cost_bps: tuple[int, ...] = (0, 10, 20, 50)
    refit_frequency: str = "monthly"


@dataclass
class FittedAuctionModel:
    feature_names: tuple[str, ...]
    feature_mean: np.ndarray
    feature_std: np.ndarray
    return_coefficients: np.ndarray
    probability_coefficients: np.ndarray
    train_start: date
    train_end: date
    training_rows: int
    target_column: str
    probability_target_column: str | None = None

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        values = frame.loc[:, self.feature_names].to_numpy(dtype=float)
        matrix = np.column_stack([
            np.ones(len(values)),
            (values - self.feature_mean) / self.feature_std,
        ])
        out = frame.copy()
        out["predicted_return"] = matrix @ self.return_coefficients
        out["up_probability"] = np.clip(
            matrix @ self.probability_coefficients, 0.05, 0.95
        )
        return out

    def explain_return(
        self, row: pd.Series, limit: int = 3
    ) -> list[dict[str, float | str]]:
        values = row.loc[list(self.feature_names)].to_numpy(dtype=float)
        standardized = (values - self.feature_mean) / self.feature_std
        contributions = standardized * self.return_coefficients[1:]
        order = np.argsort(np.abs(contributions))[::-1][:limit]
        return [
            {
                "feature": self.feature_names[index],
                "contribution": float(contributions[index]),
            }
            for index in order
        ]


@dataclass
class FittedAuctionEnsemble:
    models: tuple[FittedAuctionModel, ...]
    windows: tuple[int, ...]
    target_column: str

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.models:
            raise ValueError("auction ensemble has no fitted models")
        out = frame.copy()
        scores: list[np.ndarray] = []
        returns: list[np.ndarray] = []
        probabilities: list[np.ndarray] = []
        for index, model in enumerate(self.models):
            predicted = _score(model.predict(frame))
            out[f"ensemble_score_{index}"] = predicted["prediction_score"].to_numpy()
            scores.append(predicted["prediction_score"].to_numpy(dtype=float))
            returns.append(predicted["predicted_return"].to_numpy(dtype=float))
            probabilities.append(predicted["up_probability"].to_numpy(dtype=float))
        score_matrix = np.column_stack(scores)
        out["prediction_score"] = score_matrix.mean(axis=1)
        out["ensemble_score_std"] = score_matrix.std(axis=1)
        out["ensemble_model_agreement"] = (score_matrix > 0).mean(axis=1)
        out["predicted_return"] = np.column_stack(returns).mean(axis=1)
        out["up_probability"] = np.column_stack(probabilities).mean(axis=1)
        out["prediction_rank"] = out.groupby("trade_date")["prediction_score"].rank(
            ascending=False, method="first"
        )
        return out

    def explain_return(
        self, row: pd.Series, limit: int = 3
    ) -> list[dict[str, float | str]]:
        by_feature: dict[str, list[float]] = {}
        for model in self.models:
            for item in model.explain_return(row, limit=len(model.feature_names)):
                by_feature.setdefault(str(item["feature"]), []).append(
                    float(item["contribution"])
                )
        averaged = [
            {"feature": name, "contribution": float(np.mean(values))}
            for name, values in by_feature.items()
        ]
        return sorted(
            averaged, key=lambda item: abs(float(item["contribution"])), reverse=True
        )[:limit]


def _historical_membership(
    trade_dates: pd.Series,
    snapshots: pd.DataFrame,
) -> dict[date, set[str]]:
    source = snapshots.copy()
    normalized_dates = source["snapshot_date"].astype(str).str.replace("-", "", regex=False)
    source["snapshot_date"] = pd.to_datetime(
        normalized_dates, format="%Y%m%d", errors="raise"
    ).dt.date
    snapshot_dates = sorted(source["snapshot_date"].unique())
    source["stock_code"] = source["stock_code"].astype(str).str[:6].str.zfill(6)
    members = {
        value: set(source.loc[source["snapshot_date"] == value, "stock_code"])
        for value in snapshot_dates
    }
    output: dict[date, set[str]] = {}
    for trade_date in sorted(set(trade_dates)):
        index = bisect_right(snapshot_dates, trade_date) - 1
        output[trade_date] = set() if index < 0 else members[snapshot_dates[index]]
    return output


def _cross_sectionalize(
    frame: pd.DataFrame,
    raw_features: tuple[str, ...],
) -> None:
    for name in raw_features:
        grouped = frame.groupby("trade_date")[name]
        std = grouped.transform("std").replace(0, np.nan)
        frame[f"{name}_cs"] = (frame[name] - grouped.transform("mean")) / std


def _add_industry_relative_features(
    frame: pd.DataFrame,
    raw_features: tuple[str, ...],
    minimum_group_size: int = 5,
) -> None:
    if "industry_name" not in frame:
        frame["industry_name"] = None
    industry = frame["industry_name"].fillna("").astype(str).str.strip()
    frame["industry_name"] = industry.mask(industry.eq(""), "UNKNOWN")
    group_keys = [frame["trade_date"], frame["industry_name"]]
    group_size = frame.groupby(["trade_date", "industry_name"])[
        "security_id"
    ].transform("nunique")
    frame["auction_industry_group_size"] = group_size
    frame["auction_industry_supported"] = group_size >= minimum_group_size
    for name in raw_features:
        industry_median = frame.groupby(group_keys)[name].transform("median")
        market_median = frame.groupby("trade_date")[name].transform("median")
        benchmark = industry_median.where(group_size >= minimum_group_size, market_median)
        relative_name = f"{name}_industry_relative"
        frame[relative_name] = frame[name] - benchmark
        grouped = frame.groupby("trade_date")[relative_name]
        std = grouped.transform("std").replace(0, np.nan)
        frame[f"{name}_industry_cs"] = (
            frame[relative_name] - grouped.transform("mean")
        ) / std


def _median_absolute_deviation(values: pd.Series) -> float:
    valid = values.dropna()
    if valid.empty:
        return float("nan")
    return float((valid - valid.median()).abs().median())


def _add_market_context_features(frame: pd.DataFrame) -> None:
    grouped = frame.groupby("trade_date", sort=False)
    frame["auction_market_gap_median"] = grouped["auction_gap"].transform("median")
    frame["auction_market_positive_share"] = grouped["auction_gap"].transform(
        lambda values: float((values > 0).mean())
    )
    frame["auction_market_gap_dispersion"] = grouped["auction_gap"].transform(
        _median_absolute_deviation
    )
    frame["auction_market_large_gap_share"] = grouped["auction_gap"].transform(
        lambda values: float((values.abs() >= 0.02).mean())
    )
    frame["auction_market_volume_ratio_median"] = grouped[
        "auction_volume_ratio_log"
    ].transform("median")


def classify_auction_market_regime(row: pd.Series) -> str:
    median_gap = float(row["auction_market_gap_median"])
    positive_share = float(row["auction_market_positive_share"])
    if median_gap < 0 and positive_share < 0.35:
        return "risk_off"
    if median_gap > 0 and positive_share > 0.65:
        return "risk_on"
    return "neutral"


def _maximum_cross_section(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(frame.groupby("trade_date")["security_id"].nunique().max())


def _price_limit_pct(stock_code: object) -> float:
    code = str(stock_code or "")[:6]
    if code.startswith(("300", "301", "688", "689")):
        return 0.195
    if code.startswith(("4", "8", "9")):
        return 0.295
    return 0.095


def _normalize_tushare_auction(auction_rows: pd.DataFrame) -> pd.DataFrame:
    required = {
        "ts_code", "trade_date", "price", "vol", "amount", "pre_close",
        "turnover_rate", "volume_ratio",
    }
    missing = sorted(required - set(auction_rows.columns))
    if missing:
        raise ValueError(f"Tushare auction rows missing columns: {', '.join(missing)}")
    auction = auction_rows.copy()
    normalized_dates = auction["trade_date"].astype(str).str.replace("-", "", regex=False)
    auction["trade_date"] = pd.to_datetime(
        normalized_dates, format="%Y%m%d", errors="raise"
    ).dt.date
    auction["stock_code"] = auction["ts_code"].astype(str).str[:6].str.zfill(6)
    auction = auction.rename(columns={
        "price": "auction_price",
        "vol": "auction_volume",
        "amount": "auction_amount",
        "pre_close": "auction_pre_close",
        "turnover_rate": "auction_turnover_rate",
        "volume_ratio": "auction_volume_ratio",
    })
    numeric = (
        "auction_price", "auction_volume", "auction_amount",
        "auction_pre_close", "auction_turnover_rate", "auction_volume_ratio",
    )
    for column in numeric:
        auction[column] = pd.to_numeric(auction[column], errors="coerce")
    return auction.drop_duplicates(["stock_code", "trade_date"], keep="last")


def prepare_tushare_auction_feature_frame(
    base_features: pd.DataFrame,
    auction_rows: pd.DataFrame,
    universe_snapshots: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the production 09:25 feature frame from Tushare point-in-time data."""
    frame = base_features.copy()
    frame["stock_code"] = frame["stock_code"].astype(str).str[:6].str.zfill(6)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame.sort_values(["security_id", "trade_date"])
    by_security = frame.groupby("security_id", sort=False)
    frame["previous_close"] = by_security["close"].shift(1)
    frame["previous_volume_mean_20"] = by_security["volume"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=10).mean()
    )
    frame["previous_market_cap"] = by_security["market_cap"].shift(1)
    frame["next_close_price"] = by_security["close"].shift(-1)

    auction = _normalize_tushare_auction(auction_rows)
    membership = _historical_membership(auction["trade_date"], universe_snapshots)
    auction = auction[
        auction.apply(
            lambda row: row["stock_code"] in membership[row["trade_date"]], axis=1
        )
    ]
    frame = frame.merge(auction, on=["stock_code", "trade_date"], how="inner")

    # Daily features are normalized again inside the historical CSI 300
    # membership, not inside today's survivor set.
    _cross_sectionalize(frame, RAW_FEATURES)
    frame["market_return_1d"] = frame.groupby("trade_date")["return_1d"].transform(
        "median"
    )
    frame["market_return_5d"] = frame.groupby("trade_date")["return_5d"].transform(
        "median"
    )

    frame["auction_gap"] = frame["auction_price"] / frame["auction_pre_close"] - 1.0
    frame["auction_volume_log_ratio"] = np.log(
        (frame["auction_volume"] + 1.0)
        / (frame["previous_volume_mean_20"] + 1.0)
    )
    frame["auction_amount_to_cap"] = (
        frame["auction_amount"] / frame["previous_market_cap"]
    )
    # Tushare documents turnover_rate as a percentage value. Cross-sectional
    # standardization makes the unit immaterial, but conversion keeps reports explicit.
    frame["auction_turnover_rate"] = frame["auction_turnover_rate"] / 100.0
    frame["auction_volume_ratio_log"] = np.log1p(
        frame["auction_volume_ratio"].clip(lower=0)
    )
    limits = {
        "auction_gap": (-0.25, 0.25),
        "auction_volume_log_ratio": (-6.0, 6.0),
        "auction_amount_to_cap": (0.0, 0.10),
        "auction_turnover_rate": (0.0, 0.10),
        "auction_volume_ratio_log": (0.0, np.log1p(100.0)),
    }
    for name in LIVE_AUCTION_RAW_FEATURES:
        frame[name] = frame[name].clip(*limits[name])
    _cross_sectionalize(frame, LIVE_AUCTION_RAW_FEATURES)
    _add_industry_relative_features(frame, INDUSTRY_RELATIVE_RAW_FEATURES)
    _add_market_context_features(frame)
    frame["auction_market_regime"] = frame.apply(
        classify_auction_market_regime, axis=1
    )

    frame["pre_close_mismatch"] = (
        frame["auction_pre_close"] / frame["previous_close"] - 1.0
    )
    frame["target_auction_to_next_close"] = (
        frame["next_close_price"] / frame["auction_price"] - 1.0
    )
    frame["target_next_date"] = frame["target_date"]
    limits_by_stock = frame["stock_code"].map(_price_limit_pct)
    frame["auction_tradable"] = (
        frame["auction_price"].gt(0)
        & frame["auction_pre_close"].gt(0)
        & frame["auction_volume"].gt(0)
        & frame["auction_amount"].gt(0)
        & frame["auction_gap"].lt(limits_by_stock)
        & frame["pre_close_mismatch"].abs().le(0.003)
    )
    mismatch = frame["pre_close_mismatch"].dropna().abs()
    diagnostics = {
        "rows": len(frame),
        "dates": frame["trade_date"].nunique(),
        "maximum_cross_section": _maximum_cross_section(frame),
        "pre_close_match_rate_0_1pct": float((mismatch <= 0.001).mean()),
        "pre_close_mismatch_median": float(mismatch.median()),
        "tradable_rows": int(frame["auction_tradable"].sum()),
        "industry_mapped_rows": int(frame["industry_name"].ne("UNKNOWN").sum()),
        "industry_supported_rows": int(frame["auction_industry_supported"].sum()),
        "industry_count": int(
            frame.loc[frame["industry_name"].ne("UNKNOWN"), "industry_name"].nunique()
        ),
        "market_context_scope": "point-in-time CSI 300 mapped members",
    }
    if diagnostics["maximum_cross_section"] > 300:
        raise ValueError("historical CSI 300 cross-section exceeds 300 members")
    return frame.sort_values(["trade_date", "security_id"]).reset_index(drop=True), diagnostics


def attach_execution_entries(
    feature_frame: pd.DataFrame,
    minute_entries: pd.DataFrame,
) -> pd.DataFrame:
    """Attach independently observed entry and next-day 09:31 VWAP labels."""
    required = {
        "stock_code", "trade_date", "entry_0931_open", "entry_0931_vwap",
        "entry_0931_volume",
    }
    missing = sorted(required - set(minute_entries.columns))
    if missing:
        raise ValueError(f"minute entries missing columns: {', '.join(missing)}")
    entries = minute_entries.copy()
    entries["stock_code"] = entries["stock_code"].astype(str).str[:6].str.zfill(6)
    entries["trade_date"] = pd.to_datetime(entries["trade_date"]).dt.date
    numeric = ("entry_0931_open", "entry_0931_vwap", "entry_0931_volume")
    for column in numeric:
        entries[column] = pd.to_numeric(entries[column], errors="coerce")
    entries = entries.drop_duplicates(["stock_code", "trade_date"], keep="last")

    frame = feature_frame.merge(
        entries[["stock_code", "trade_date", *numeric]],
        on=["stock_code", "trade_date"],
        how="left",
    )
    valid_entry = frame["entry_0931_vwap"].gt(0) & frame["entry_0931_volume"].gt(0)
    frame["execution_tradable"] = frame["auction_tradable"].fillna(False) & valid_entry
    frame["target_0931_vwap_to_next_close"] = np.where(
        valid_entry,
        frame["next_close_price"] / frame["entry_0931_vwap"] - 1.0,
        np.nan,
    )
    frame["entry_0931_notional"] = (
        frame["entry_0931_vwap"] * frame["entry_0931_volume"]
    )
    frame["entry_capacity_5pct"] = frame["entry_0931_notional"] * 0.05

    exits = entries[["stock_code", "trade_date", "entry_0931_vwap"]].rename(
        columns={
            "trade_date": "target_next_date",
            "entry_0931_vwap": "exit_next_0931_vwap",
        }
    )
    frame = frame.merge(
        exits,
        on=["stock_code", "target_next_date"],
        how="left",
    )
    valid_exit = frame["exit_next_0931_vwap"].gt(0)
    frame["target_0931_vwap_to_next_0931_vwap"] = np.where(
        valid_entry & valid_exit,
        frame["exit_next_0931_vwap"] / frame["entry_0931_vwap"] - 1.0,
        np.nan,
    )
    for target in (
        "target_0931_vwap_to_next_close",
        "target_0931_vwap_to_next_0931_vwap",
    ):
        eligible_target = frame[target].where(frame["execution_tradable"])
        benchmark = eligible_target.groupby(frame["trade_date"]).transform("mean")
        frame[f"{target}_universe_excess"] = frame[target] - benchmark
    return frame


def prepare_auction_feature_frame(
    base_features: pd.DataFrame,
    auction_rows: pd.DataFrame,
    universe_snapshots: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Merge point-in-time membership, lagged base features, and 09:25 data."""
    frame = base_features.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame.sort_values(["security_id", "trade_date"])
    by_security = frame.groupby("security_id", sort=False)
    frame["previous_close"] = by_security["close"].shift(1)
    frame["previous_volume_mean_20"] = by_security["volume"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=10).mean()
    )
    frame["previous_market_cap"] = by_security["market_cap"].shift(1)

    auction = auction_rows.copy()
    auction["trade_date"] = pd.to_datetime(auction["trade_date"]).dt.date
    auction["stock_code"] = auction["symbol"].astype(str).str[:6]
    rename = {
        "open": "auction_open",
        "close": "auction_close",
        "high": "auction_high",
        "low": "auction_low",
        "volume": "auction_volume",
        "amount": "auction_amount",
        "vwap": "auction_vwap",
    }
    auction = auction.rename(columns=rename)
    for column in rename.values():
        auction[column] = pd.to_numeric(auction[column], errors="coerce")
    auction = auction.drop_duplicates(["stock_code", "trade_date"], keep="last")

    membership = _historical_membership(auction["trade_date"], universe_snapshots)
    auction = auction[
        auction.apply(
            lambda row: row["stock_code"] in membership[row["trade_date"]], axis=1
        )
    ]
    frame = frame.merge(auction, on=["stock_code", "trade_date"], how="inner")

    # Recompute cross-sectional base normalization inside the point-in-time universe.
    for name in RAW_FEATURES:
        grouped = frame.groupby("trade_date")[name]
        std = grouped.transform("std").replace(0, np.nan)
        frame[f"{name}_cs"] = (frame[name] - grouped.transform("mean")) / std
    frame["market_return_1d"] = frame.groupby("trade_date")["return_1d"].transform("median")
    frame["market_return_5d"] = frame.groupby("trade_date")["return_5d"].transform("median")

    frame["auction_gap"] = frame["auction_open"] / frame["previous_close"] - 1.0
    frame["auction_path"] = frame["auction_close"] / frame["auction_open"] - 1.0
    frame["auction_range"] = (
        frame["auction_high"] - frame["auction_low"]
    ) / frame["auction_open"]
    frame["auction_vwap_deviation"] = (
        frame["auction_vwap"] / frame["auction_open"] - 1.0
    )
    frame["auction_volume_log_ratio"] = np.log(
        (frame["auction_volume"] + 1.0) / (frame["previous_volume_mean_20"] + 1.0)
    )
    frame["auction_amount_to_cap"] = (
        frame["auction_amount"] / frame["previous_market_cap"]
    )

    limits = {
        "auction_gap": (-0.25, 0.25),
        "auction_path": (-0.10, 0.10),
        "auction_range": (0.0, 0.20),
        "auction_vwap_deviation": (-0.10, 0.10),
        "auction_volume_log_ratio": (-6.0, 6.0),
        "auction_amount_to_cap": (0.0, 0.10),
    }
    for name in AUCTION_RAW_FEATURES:
        frame[name] = frame[name].clip(*limits[name])
        grouped = frame.groupby("trade_date")[name]
        std = grouped.transform("std").replace(0, np.nan)
        frame[f"{name}_cs"] = (frame[name] - grouped.transform("mean")) / std
    frame["auction_market_gap_median"] = frame.groupby("trade_date")[
        "auction_gap"
    ].transform("median")
    frame["auction_market_positive_share"] = frame.groupby("trade_date")[
        "auction_gap"
    ].transform(lambda values: float((values > 0).mean()))

    frame["auction_open_mismatch"] = frame["auction_open"] / frame["open"] - 1.0
    frame["target_same_day"] = frame["close"] / frame["auction_open"] - 1.0
    frame["target_same_date"] = frame["trade_date"]
    # Recover D+1 close from the daily-bar label, but use the independently
    # observed auction price as the return denominator.
    next_close = (1.0 + frame["target_return"]) * frame["open"]
    frame["target_next_close"] = next_close / frame["auction_open"] - 1.0
    frame["target_next_date"] = frame["target_date"]
    frame["auction_tradable"] = (
        frame["historically_buyable"].fillna(False)
        & frame["auction_volume"].gt(0)
        & frame["auction_amount"].gt(0)
        & frame["auction_open_mismatch"].abs().le(0.003)
    )
    mismatch = frame["auction_open_mismatch"].dropna().abs()
    diagnostics = {
        "rows": len(frame),
        "dates": frame["trade_date"].nunique(),
        "maximum_cross_section": int(
            frame.groupby("trade_date")["security_id"].nunique().max()
        ),
        "open_match_rate_0_1pct": float((mismatch <= 0.001).mean()),
        "open_mismatch_median": float(mismatch.median()),
        "tradable_rows": int(frame["auction_tradable"].sum()),
    }
    if diagnostics["maximum_cross_section"] > 300:
        raise ValueError("historical CSI 300 cross-section exceeds 300 members")
    return frame.sort_values(["trade_date", "security_id"]).reset_index(drop=True), diagnostics


def _fit_ridge(
    samples: pd.DataFrame,
    feature_names: tuple[str, ...],
    target_column: str,
    config: AuctionBacktestConfig,
    probability_target_column: str | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    signal_dates = sorted(samples["trade_date"].unique())
    mean = samples.loc[:, feature_names].mean().to_numpy(dtype=float)
    std = samples.loc[:, feature_names].std().replace(0, 1.0).fillna(1.0).to_numpy(dtype=float)
    values = samples.loc[:, feature_names].to_numpy(dtype=float)
    matrix = np.column_stack([np.ones(len(values)), (values - mean) / std])
    date_rank = {value: index for index, value in enumerate(signal_dates)}
    last_rank = len(signal_dates) - 1
    weights = np.array([
        0.5 ** ((last_rank - date_rank[value]) / max(config.half_life_days, 1))
        for value in samples["trade_date"]
    ])
    regularization = np.eye(matrix.shape[1]) * config.ridge_alpha
    regularization[0, 0] = 0.0
    normal = matrix.T @ (matrix * weights[:, None]) + regularization
    target = samples[target_column].to_numpy(dtype=float)
    probability_target = samples[
        probability_target_column or target_column
    ].to_numpy(dtype=float)
    clipped = np.clip(target, -config.target_clip, config.target_clip)
    returns = np.linalg.solve(normal, matrix.T @ (clipped * weights))
    probability = np.linalg.solve(
        normal, matrix.T @ ((probability_target > 0) * weights)
    )
    return mean, std, returns, probability


def _predict(
    frame: pd.DataFrame,
    feature_names: tuple[str, ...],
    fitted: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
) -> pd.DataFrame:
    mean, std, return_coefficients, probability_coefficients = fitted
    values = frame.loc[:, feature_names].to_numpy(dtype=float)
    matrix = np.column_stack([np.ones(len(values)), (values - mean) / std])
    out = frame.copy()
    out["predicted_return"] = matrix @ return_coefficients
    out["up_probability"] = np.clip(matrix @ probability_coefficients, 0.05, 0.95)
    return out


def _score(predictions: pd.DataFrame) -> pd.DataFrame:
    out = predictions.copy()
    for name in ("predicted_return", "up_probability"):
        grouped = out.groupby("trade_date")[name]
        std = grouped.transform("std").replace(0, 1.0).fillna(1.0)
        out[f"{name}_z"] = (out[name] - grouped.transform("mean")) / std
    out["prediction_score"] = out["predicted_return_z"] + 0.5 * out["up_probability_z"]
    out["prediction_rank"] = out.groupby("trade_date")["prediction_score"].rank(
        ascending=False, method="first"
    )
    return out


def _t_stat(values: pd.Series) -> float:
    std = float(values.std())
    if not np.isfinite(std) or std == 0:
        return float("nan")
    return float(values.mean() / (std / np.sqrt(len(values))))


def _prospective_top_k_metrics(
    scored: pd.DataFrame,
    selected: pd.DataFrame,
    target_column: str,
    cost_bps: tuple[int, ...],
) -> dict[str, Any]:
    period_scored = scored[
        scored["trade_date"] >= PROSPECTIVE_EVALUATION_START
    ]
    period_selected = selected[
        selected["trade_date"] >= PROSPECTIVE_EVALUATION_START
    ]
    if period_scored.empty:
        return {
            "start": PROSPECTIVE_EVALUATION_START.isoformat(),
            "end": None,
            "dates": 0,
            "trades": 0,
            "status": "awaiting_prospective_labels",
        }
    selected_daily = period_selected.groupby("trade_date")[target_column].mean()
    selected_mean = float(period_selected[target_column].mean())
    universe_mean = float(period_scored[target_column].mean())
    return {
        "start": PROSPECTIVE_EVALUATION_START.isoformat(),
        "end": max(period_scored["trade_date"]).isoformat(),
        "dates": int(period_scored["trade_date"].nunique()),
        "trades": len(period_selected),
        "selected_mean_return": selected_mean,
        "selected_mean_after_cost": {
            str(cost): selected_mean - cost / 10_000.0 for cost in cost_bps
        },
        "universe_mean_return": universe_mean,
        "mean_return_spread": selected_mean - universe_mean,
        "selected_stock_win_rate": float(
            (period_selected[target_column] > 0).mean()
        ),
        "selected_daily_t_stat": _t_stat(selected_daily),
        "status": "frozen_policy_out_of_sample",
    }


def _assert_targets_known_before(
    samples: pd.DataFrame,
    prediction_date: date,
    target_date_column: str = "target_next_date",
) -> None:
    """Fail closed if a fit contains a label unavailable at prediction time."""
    if samples.empty:
        raise ValueError(f"no known training labels before {prediction_date}")
    target_dates = pd.to_datetime(samples[target_date_column], errors="coerce").dt.date
    if target_dates.isna().any():
        raise ValueError("training labels contain an invalid target completion date")
    latest = max(target_dates)
    if latest >= prediction_date:
        raise ValueError(
            f"future-label leakage: latest target date {latest} is not before "
            f"prediction date {prediction_date}"
        )


def walk_forward_ablation(
    frame: pd.DataFrame,
    target_column: str,
    target_date_column: str,
    config: AuctionBacktestConfig,
    feature_sets: dict[str, tuple[str, ...]] | None = None,
    tradable_column: str = "auction_tradable",
) -> dict[str, Any]:
    selected_feature_sets = feature_sets or ABLATION_FEATURE_SETS
    all_features = sorted({
        feature for features in selected_feature_sets.values() for feature in features
    })
    samples = frame.dropna(subset=all_features + [target_column, target_date_column]).copy()
    samples = samples[samples[tradable_column].fillna(False)].copy()
    coverage = samples.groupby("trade_date")["security_id"].nunique()
    eligible_dates = sorted(coverage[coverage >= config.minimum_cross_section].index)
    if len(eligible_dates) <= config.validation_days + 60:
        raise ValueError(f"only {len(eligible_dates)} eligible auction dates")
    samples = samples[samples["trade_date"].isin(eligible_dates)]
    validation_dates = eligible_dates[-config.validation_days:]
    if config.refit_frequency == "daily":
        refit_groups = [(value, [value]) for value in validation_dates]
    elif config.refit_frequency == "weekly":
        keys = sorted({
            (value.isocalendar().year, value.isocalendar().week)
            for value in validation_dates
        })
        refit_groups = [
            (
                key,
                [
                    value for value in validation_dates
                    if (value.isocalendar().year, value.isocalendar().week) == key
                ],
            )
            for key in keys
        ]
    elif config.refit_frequency == "monthly":
        keys = sorted({(value.year, value.month) for value in validation_dates})
        refit_groups = [
            (
                key,
                [
                    value for value in validation_dates
                    if (value.year, value.month) == key
                ],
            )
            for key in keys
        ]
    else:
        raise ValueError("refit_frequency must be daily, weekly, or monthly")
    report: dict[str, Any] = {
        "target": target_column,
        "validation_start": validation_dates[0].isoformat(),
        "validation_end": validation_dates[-1].isoformat(),
        "eligible_dates": len(eligible_dates),
        "refit_frequency": config.refit_frequency,
        "refit_count": len(refit_groups),
        "models": {},
    }
    selected_daily_by_model: dict[str, pd.Series] = {}

    for model_name, feature_names in selected_feature_sets.items():
        predictions: list[pd.DataFrame] = []
        for _, test_dates in refit_groups:
            test_start = min(test_dates)
            prior_dates = [value for value in eligible_dates if value < test_start]
            window = set(prior_dates[-config.training_window_days:])
            train = samples[
                samples["trade_date"].isin(window)
                & (samples[target_date_column] < test_start)
            ]
            _assert_targets_known_before(train, test_start, target_date_column)
            test = samples[samples["trade_date"].isin(test_dates)]
            fitted = _fit_ridge(train, feature_names, target_column, config)
            predictions.append(_predict(test, feature_names, fitted))
        scored = _score(pd.concat(predictions, ignore_index=True))
        selected = scored[scored["prediction_rank"] <= config.top_k]
        selected_daily = selected.groupby("trade_date")[target_column].mean()
        selected_daily_by_model[model_name] = selected_daily
        rank_ic = [
            group["prediction_score"].rank().corr(group[target_column].rank())
            for _, group in scored.groupby("trade_date")
        ]
        selected_mean = float(selected[target_column].mean())
        universe_mean = float(scored[target_column].mean())
        metrics: dict[str, Any] = {
            "selected_mean_return": selected_mean,
            "universe_mean_return": universe_mean,
            "mean_return_spread": selected_mean - universe_mean,
            "selected_stock_win_rate": float((selected[target_column] > 0).mean()),
            "selected_daily_win_rate": float((selected_daily > 0).mean()),
            "selected_daily_t_stat": _t_stat(selected_daily),
            "mean_rank_ic": float(np.nanmean(rank_ic)),
            "rows": len(scored),
        }
        metrics["selected_mean_after_cost"] = {
            str(cost): selected_mean - cost / 10_000.0
            for cost in config.cost_bps
        }
        if "entry_capacity_5pct" in selected:
            capacity = selected["entry_capacity_5pct"].dropna()
            metrics["entry_capacity_5pct_median"] = float(capacity.median())
            metrics["entry_capacity_5pct_p10"] = float(capacity.quantile(0.10))
        metrics["prospective"] = _prospective_top_k_metrics(
            scored, selected, target_column, config.cost_bps
        )
        report["models"][model_name] = metrics
    base = report["models"]["base"]["selected_mean_return"]
    report["incremental_selected_return"] = {
        "auction_minus_base": report["models"]["auction"]["selected_mean_return"] - base,
        "combined_minus_base": report["models"]["combined"]["selected_mean_return"] - base,
        "combined_minus_auction": (
            report["models"]["combined"]["selected_mean_return"]
            - report["models"]["auction"]["selected_mean_return"]
        ),
    }
    if "context" in report["models"]:
        report["incremental_selected_return"]["context_minus_auction"] = (
            report["models"]["context"]["selected_mean_return"]
            - report["models"]["auction"]["selected_mean_return"]
        )
    report["incremental_daily_t_stat"] = {}
    comparisons = [
        ("auction", "base"),
        ("combined", "base"),
        ("combined", "auction"),
    ]
    if "context" in report["models"]:
        comparisons.append(("context", "auction"))
    for left, right in comparisons:
        paired = pd.concat(
            [selected_daily_by_model[left], selected_daily_by_model[right]],
            axis=1,
            join="inner",
        ).dropna()
        report["incremental_daily_t_stat"][f"{left}_minus_{right}"] = _t_stat(
            paired.iloc[:, 0] - paired.iloc[:, 1]
        )
    return report


def _auction_training_samples(
    feature_frame: pd.DataFrame,
    feature_names: tuple[str, ...],
    target_column: str,
    minimum_cross_section: int,
    probability_target_column: str | None = None,
) -> tuple[pd.DataFrame, list[date]]:
    required = list(feature_names) + [target_column, "target_next_date"]
    if probability_target_column:
        required.append(probability_target_column)
    samples = feature_frame.dropna(subset=required).copy()
    if target_column.startswith("target_0931_vwap_to_next_"):
        if "execution_tradable" not in samples:
            raise ValueError("real execution target requires execution_tradable")
        samples = samples[samples["execution_tradable"].fillna(False)]
    else:
        samples = samples[samples["auction_tradable"].fillna(False)]
    coverage = samples.groupby("trade_date")["security_id"].nunique()
    dates = sorted(coverage[coverage >= minimum_cross_section].index)
    if len(dates) < 60:
        raise ValueError(f"only {len(dates)} eligible auction training dates")
    return samples[samples["trade_date"].isin(dates)].copy(), dates


def fit_latest_auction(
    feature_frame: pd.DataFrame,
    config: AuctionBacktestConfig,
    target_column: str = "target_0931_vwap_to_next_close",
    feature_names: tuple[str, ...] = PRODUCTION_AUCTION_FEATURE_COLUMNS,
    probability_target_column: str | None = None,
    as_of_date: date | None = None,
) -> FittedAuctionModel:
    samples, dates = _auction_training_samples(
        feature_frame,
        feature_names,
        target_column,
        config.minimum_cross_section,
        probability_target_column,
    )
    if as_of_date is not None:
        samples = samples[samples["target_next_date"] < as_of_date]
        _assert_targets_known_before(samples, as_of_date)
        dates = sorted(samples["trade_date"].unique())
        if len(dates) < min(40, config.training_window_days):
            raise ValueError(f"only {len(dates)} known auction training dates")
    window_dates = dates[-config.training_window_days:]
    window = set(window_dates)
    samples = samples[samples["trade_date"].isin(window)]
    mean, std, returns, probability = _fit_ridge(
        samples,
        feature_names,
        target_column,
        config,
        probability_target_column,
    )
    return FittedAuctionModel(
        feature_names=feature_names,
        feature_mean=mean,
        feature_std=std,
        return_coefficients=returns,
        probability_coefficients=probability,
        train_start=window_dates[0],
        train_end=window_dates[-1],
        training_rows=len(samples),
        target_column=target_column,
        probability_target_column=probability_target_column,
    )


def fit_latest_auction_ensemble(
    feature_frame: pd.DataFrame,
    config: AuctionBacktestConfig,
    target_column: str = "target_0931_vwap_to_next_close",
    feature_names: tuple[str, ...] = CONTEXT_AUCTION_FEATURE_COLUMNS,
    windows: tuple[int, ...] = DEFAULT_ENSEMBLE_WINDOWS,
    probability_target_column: str | None = None,
    as_of_date: date | None = None,
) -> FittedAuctionEnsemble:
    normalized_windows = tuple(sorted({int(value) for value in windows if value >= 20}))
    if not normalized_windows:
        raise ValueError("auction ensemble requires at least one window of 20 days")
    samples, dates = _auction_training_samples(
        feature_frame,
        feature_names,
        target_column,
        config.minimum_cross_section,
        probability_target_column,
    )
    if as_of_date is not None:
        samples = samples[samples["target_next_date"] < as_of_date]
        _assert_targets_known_before(samples, as_of_date)
        dates = sorted(samples["trade_date"].unique())
    if len(dates) < min(40, min(normalized_windows)):
        raise ValueError(f"only {len(dates)} known auction training dates")
    return _fit_auction_ensemble_from_samples(
        samples,
        dates,
        config,
        target_column,
        feature_names,
        normalized_windows,
        probability_target_column,
    )


def _fit_auction_ensemble_from_samples(
    samples: pd.DataFrame,
    dates: list[date],
    config: AuctionBacktestConfig,
    target_column: str,
    feature_names: tuple[str, ...],
    windows: tuple[int, ...],
    probability_target_column: str | None = None,
) -> FittedAuctionEnsemble:
    models: list[FittedAuctionModel] = []
    for window_days in windows:
        model_dates = dates[-window_days:]
        model_samples = samples[samples["trade_date"].isin(set(model_dates))]
        model_config = replace(config, training_window_days=window_days)
        mean, std, returns, probability = _fit_ridge(
            model_samples,
            feature_names,
            target_column,
            model_config,
            probability_target_column,
        )
        models.append(FittedAuctionModel(
            feature_names=feature_names,
            feature_mean=mean,
            feature_std=std,
            return_coefficients=returns,
            probability_coefficients=probability,
            train_start=model_dates[0],
            train_end=model_dates[-1],
            training_rows=len(model_samples),
            target_column=target_column,
            probability_target_column=probability_target_column,
        ))
    return FittedAuctionEnsemble(
        models=tuple(models),
        windows=windows,
        target_column=target_column,
    )


def rank_auction_signal(
    model: FittedAuctionModel,
    feature_frame: pd.DataFrame,
    signal_date: date,
    minimum_coverage: int,
) -> pd.DataFrame:
    rows = feature_frame[feature_frame["trade_date"] == signal_date].copy()
    rows = rows[rows["auction_tradable"].fillna(False)]
    rows = rows.dropna(subset=list(model.feature_names))
    if len(rows) < minimum_coverage:
        raise RuntimeError(
            f"auction snapshot has only {len(rows)} complete tradable members; "
            f"requires {minimum_coverage}"
        )
    return _score(model.predict(rows)).sort_values("prediction_rank")


def rank_auction_strategy(
    return_ensemble: FittedAuctionEnsemble,
    excess_ensemble: FittedAuctionEnsemble,
    feature_frame: pd.DataFrame,
    signal_date: date,
    minimum_coverage: int,
    top_k: int = 5,
    cost_bps: int = 20,
    minimum_model_agreement: float = 0.5,
    max_per_industry: int = 2,
) -> pd.DataFrame:
    required_features = sorted({
        name
        for ensemble in (return_ensemble, excess_ensemble)
        for model in ensemble.models
        for name in model.feature_names
    })
    rows = feature_frame[feature_frame["trade_date"] == signal_date].copy()
    rows = rows[rows["auction_tradable"].fillna(False)]
    rows = rows.dropna(subset=required_features)
    if len(rows) < minimum_coverage:
        raise RuntimeError(
            f"auction snapshot has only {len(rows)} complete tradable members; "
            f"requires {minimum_coverage}"
        )

    absolute = return_ensemble.predict(rows)
    excess = excess_ensemble.predict(rows)
    out = rows.copy()
    out["predicted_return"] = absolute["predicted_return"].to_numpy()
    out["predicted_excess_return"] = excess["predicted_return"].to_numpy()
    out["up_probability"] = absolute["up_probability"].to_numpy()
    out["absolute_prediction_score"] = absolute["prediction_score"].to_numpy()
    out["excess_prediction_score"] = excess["prediction_score"].to_numpy()
    out["prediction_score"] = (
        out["absolute_prediction_score"] + out["excess_prediction_score"]
    ) / 2.0
    out["ensemble_score_std"] = (
        absolute["ensemble_score_std"].to_numpy()
        + excess["ensemble_score_std"].to_numpy()
    ) / 2.0
    out["ensemble_model_agreement"] = (
        absolute["ensemble_model_agreement"].to_numpy()
        + excess["ensemble_model_agreement"].to_numpy()
    ) / 2.0
    out["prediction_rank"] = out["prediction_score"].rank(
        ascending=False, method="first"
    )
    out["auction_market_regime"] = out.apply(
        classify_auction_market_regime, axis=1
    )

    base_expected_floor = cost_bps / 10_000.0
    out["minimum_expected_return"] = base_expected_floor
    out["minimum_up_probability"] = 0.50
    risk_off = out["auction_market_regime"].eq("risk_off")
    neutral = out["auction_market_regime"].eq("neutral")
    out.loc[risk_off, "minimum_expected_return"] += 0.002
    out.loc[risk_off, "minimum_up_probability"] = 0.55
    out.loc[neutral, "minimum_up_probability"] = 0.52
    out["passes_dynamic_threshold"] = (
        out["predicted_return"].gt(out["minimum_expected_return"])
        & out["predicted_excess_return"].gt(0)
        & out["up_probability"].ge(out["minimum_up_probability"])
        & out["ensemble_model_agreement"].ge(minimum_model_agreement)
    )

    out = out.sort_values("prediction_rank").copy()
    selected_indices: list[int] = []
    industry_counts: dict[str, int] = {}
    for index, row in out.iterrows():
        if not bool(row["passes_dynamic_threshold"]):
            continue
        industry = str(row.get("industry_name") or "UNKNOWN")
        if (
            industry != "UNKNOWN"
            and industry_counts.get(industry, 0) >= max_per_industry
        ):
            continue
        selected_indices.append(index)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected_indices) >= top_k:
            break
    out["strategy_selected"] = out.index.isin(selected_indices)
    out["strategy_rank"] = np.nan
    if selected_indices:
        out.loc[selected_indices, "strategy_rank"] = np.arange(
            1, len(selected_indices) + 1
        )
    return out.reset_index(drop=True)


def walk_forward_ensemble_strategy(
    feature_frame: pd.DataFrame,
    config: AuctionBacktestConfig,
    target_column: str = "target_0931_vwap_to_next_close",
    feature_names: tuple[str, ...] = CONTEXT_AUCTION_FEATURE_COLUMNS,
    windows: tuple[int, ...] = DEFAULT_ENSEMBLE_WINDOWS,
    cost_bps: int = 20,
    minimum_model_agreement: float = 0.5,
    max_per_industry: int = 2,
) -> dict[str, Any]:
    excess_target = f"{target_column}_universe_excess"
    required = list(feature_names) + [
        target_column,
        excess_target,
        "target_next_date",
    ]
    samples = feature_frame.dropna(subset=required).copy()
    samples = samples[samples["execution_tradable"].fillna(False)]
    coverage = samples.groupby("trade_date")["security_id"].nunique()
    eligible_dates = sorted(coverage[coverage >= config.minimum_cross_section].index)
    if len(eligible_dates) <= config.validation_days + 60:
        raise ValueError(f"only {len(eligible_dates)} eligible auction dates")
    validation_dates = eligible_dates[-config.validation_days:]
    normalized_windows = tuple(sorted({
        int(value) for value in windows if value >= 20
    }))
    if not normalized_windows:
        raise ValueError("auction ensemble requires at least one window of 20 days")

    predictions: list[pd.DataFrame] = []
    for test_date in validation_dates:
        known = samples[samples["target_next_date"] < test_date]
        _assert_targets_known_before(known, test_date)
        known_dates = sorted(known["trade_date"].unique())
        absolute = _fit_auction_ensemble_from_samples(
            known,
            known_dates,
            config,
            target_column,
            feature_names,
            normalized_windows,
        )
        excess = _fit_auction_ensemble_from_samples(
            known,
            known_dates,
            config,
            excess_target,
            feature_names,
            normalized_windows,
            target_column,
        )
        scored = rank_auction_strategy(
            absolute,
            excess,
            feature_frame,
            test_date,
            config.minimum_cross_section,
            top_k=config.top_k,
            cost_bps=cost_bps,
            minimum_model_agreement=minimum_model_agreement,
            max_per_industry=max_per_industry,
        )
        predictions.append(scored)

    scored = pd.concat(predictions, ignore_index=True)
    selected = scored[scored["strategy_selected"]].copy()
    selected_counts = selected.groupby("trade_date").size().reindex(
        validation_dates, fill_value=0
    )
    gross_daily = selected.groupby("trade_date")[target_column].mean().reindex(
        validation_dates, fill_value=0.0
    )
    net_daily = gross_daily.copy()
    active = selected_counts.gt(0)
    net_daily.loc[active] -= cost_bps / 10_000.0
    universe_daily = scored.groupby("trade_date")[target_column].mean().reindex(
        validation_dates
    )
    active_spread = gross_daily.loc[active] - universe_daily.loc[active]
    rank_ic = [
        group["prediction_score"].rank().corr(group[target_column].rank())
        for _, group in scored.groupby("trade_date")
    ]
    selected_mean = (
        float(selected[target_column].mean()) if not selected.empty else float("nan")
    )
    metrics: dict[str, Any] = {
        "selected_mean_return": selected_mean,
        "universe_mean_return": float(scored[target_column].mean()),
        "mean_return_spread": (
            float(active_spread.mean()) if not active_spread.empty else float("nan")
        ),
        "selected_stock_win_rate": (
            float((selected[target_column] > 0).mean())
            if not selected.empty else float("nan")
        ),
        "selected_daily_win_rate": (
            float((gross_daily.loc[active] > 0).mean()) if active.any() else float("nan")
        ),
        "selected_daily_t_stat": _t_stat(net_daily),
        "mean_rank_ic": float(np.nanmean(rank_ic)),
        "rows": len(scored),
        "trades": len(selected),
        "active_days": int(active.sum()),
        "active_day_rate": float(active.mean()),
        "average_selected_count": float(selected_counts.mean()),
        "portfolio_daily_mean_return": float(gross_daily.mean()),
        "portfolio_daily_mean_after_cost": float(net_daily.mean()),
        "selected_mean_after_cost": {
            str(cost): selected_mean - cost / 10_000.0
            for cost in config.cost_bps
        },
    }
    if "entry_capacity_5pct" in selected:
        capacity = selected["entry_capacity_5pct"].dropna()
        if not capacity.empty:
            metrics["entry_capacity_5pct_median"] = float(capacity.median())
            metrics["entry_capacity_5pct_p10"] = float(capacity.quantile(0.10))
    prospective_dates = [
        value for value in validation_dates
        if value >= PROSPECTIVE_EVALUATION_START
    ]
    if prospective_dates:
        prospective_selected = selected[
            selected["trade_date"].isin(prospective_dates)
        ]
        prospective_counts = prospective_selected.groupby("trade_date").size().reindex(
            prospective_dates, fill_value=0
        )
        prospective_gross = prospective_selected.groupby("trade_date")[
            target_column
        ].mean().reindex(prospective_dates, fill_value=0.0)
        prospective_active = prospective_counts.gt(0)
        prospective_net = prospective_gross.copy()
        prospective_net.loc[prospective_active] -= cost_bps / 10_000.0
        prospective_selected_mean = (
            float(prospective_selected[target_column].mean())
            if not prospective_selected.empty else float("nan")
        )
        metrics["prospective"] = {
            "start": PROSPECTIVE_EVALUATION_START.isoformat(),
            "end": prospective_dates[-1].isoformat(),
            "dates": len(prospective_dates),
            "active_days": int(prospective_active.sum()),
            "trades": len(prospective_selected),
            "selected_mean_return": prospective_selected_mean,
            "portfolio_daily_mean_return": float(prospective_gross.mean()),
            "portfolio_daily_mean_after_cost": float(prospective_net.mean()),
            "selected_daily_t_stat": _t_stat(prospective_net),
            "status": "frozen_policy_out_of_sample",
        }
    else:
        metrics["prospective"] = {
            "start": PROSPECTIVE_EVALUATION_START.isoformat(),
            "end": None,
            "dates": 0,
            "active_days": 0,
            "trades": 0,
            "status": "awaiting_prospective_labels",
        }
    return {
        "target": target_column,
        "validation_start": validation_dates[0].isoformat(),
        "validation_end": validation_dates[-1].isoformat(),
        "eligible_dates": len(eligible_dates),
        "refit_frequency": "daily",
        "refit_count": len(validation_dates),
        "point_in_time_rule": "target_next_date < prediction_date",
        "production_policy_version": PRODUCTION_POLICY_VERSION,
        "production_policy_frozen_at": PRODUCTION_POLICY_FROZEN_AT.isoformat(),
        "prospective_evaluation_start": PROSPECTIVE_EVALUATION_START.isoformat(),
        "ensemble_windows": list(windows),
        "feature_names": list(feature_names),
        "selection_policy": {
            "maximum_top_k": config.top_k,
            "cost_bps": cost_bps,
            "minimum_model_agreement": minimum_model_agreement,
            "maximum_per_industry": max_per_industry,
            "risk_off_extra_expected_return": 0.002,
            "risk_off_minimum_up_probability": 0.55,
            "neutral_minimum_up_probability": 0.52,
        },
        "metrics": metrics,
    }


def evaluate_optimized_execution_backtest(
    feature_frame: pd.DataFrame,
    config: AuctionBacktestConfig,
) -> dict[str, Any]:
    target = "target_0931_vwap_to_next_close"
    if target not in feature_frame:
        raise ValueError(
            "real execution backtest requires observed 09:31 VWAP labels; "
            "auction-price fallback is forbidden"
        )
    optimized = walk_forward_ensemble_strategy(feature_frame, config, target)
    return {
        "target": target,
        "validation_start": optimized["validation_start"],
        "validation_end": optimized["validation_end"],
        "eligible_dates": optimized["eligible_dates"],
        "refit_frequency": "daily",
        "refit_count": optimized["refit_count"],
        "models": {"optimized_ensemble": optimized["metrics"]},
        "optimized_strategy": {
            key: value for key, value in optimized.items() if key != "metrics"
        },
    }


def evaluate_execution_backtest(
    feature_frame: pd.DataFrame,
    config: AuctionBacktestConfig,
    include_alternate_exit: bool = True,
) -> dict[str, Any]:
    target = "target_0931_vwap_to_next_close"
    if target not in feature_frame:
        raise ValueError(
            "real execution backtest requires observed 09:31 VWAP labels; "
            "auction-price fallback is forbidden"
        )
    report = walk_forward_ablation(
        feature_frame,
        target,
        "target_next_date",
        config,
        feature_sets=LIVE_AUCTION_FEATURE_SETS,
        tradable_column="execution_tradable",
    )
    optimized = walk_forward_ensemble_strategy(feature_frame, config, target)
    report["models"]["optimized_ensemble"] = optimized["metrics"]
    report["optimized_strategy"] = {
        key: value for key, value in optimized.items() if key != "metrics"
    }
    alternate_target = "target_0931_vwap_to_next_0931_vwap"
    if include_alternate_exit and alternate_target in feature_frame:
        try:
            alternate = walk_forward_ensemble_strategy(
                feature_frame, config, alternate_target
            )
        except ValueError as exc:
            report["alternate_exit"] = {
                "target": alternate_target,
                "status": "insufficient_data",
                "error": str(exc),
            }
        else:
            report["alternate_exit"] = {
                "target": alternate_target,
                "status": "ok",
                "metrics": alternate["metrics"],
            }
    report["entry"] = {
        "bar": "09:30:00-09:31:00 Asia/Shanghai",
        "price": "turnover / volume VWAP",
        "label": "next trading-day close / 09:31 minute VWAP - 1",
        "fallback_to_auction_price": False,
        "cost_bps": list(config.cost_bps),
        "capacity_assumption": "5% of first continuous-auction minute notional",
    }
    report["exit_comparison"] = {
        "primary": "next trading-day close",
        "alternate": "next trading-day 09:30-09:31 VWAP when available",
    }
    return report
