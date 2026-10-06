# -*- coding: utf-8 -*-
"""Rolling ridge baseline for the short-horizon A-share research MVP."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from deepstock.strategies.cn.auction.features import FEATURE_COLUMNS


@dataclass(frozen=True)
class ShortTermConfig:
    training_window_days: int = 756
    validation_days: int = 252
    refit_half_life_days: int = 252
    ridge_alpha: float = 10.0
    target_clip: float = 0.12
    minimum_cross_section: int = 200
    top_k: int = 5


@dataclass
class FittedShortTermModel:
    feature_names: tuple[str, ...]
    feature_mean: np.ndarray
    feature_std: np.ndarray
    return_coefficients: np.ndarray
    probability_coefficients: np.ndarray
    train_start: date
    train_end: date
    training_rows: int

    def _matrix(self, frame: pd.DataFrame) -> np.ndarray:
        values = frame.loc[:, self.feature_names].to_numpy(dtype=float)
        standardized = (values - self.feature_mean) / self.feature_std
        return np.column_stack([np.ones(len(standardized)), standardized])

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        matrix = self._matrix(out)
        out["predicted_return"] = matrix @ self.return_coefficients
        out["up_probability"] = np.clip(
            matrix @ self.probability_coefficients, 0.05, 0.95
        )
        return out

    def explain_return(self, row: pd.Series, limit: int = 3) -> list[dict[str, float | str]]:
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
class BacktestResult:
    config: ShortTermConfig
    validation_start: date
    validation_end: date
    refit_count: int
    universe_rows: int
    selected_rows: int
    metrics: dict[str, float | int]
    predictions: pd.DataFrame

    def summary(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "refit_count": self.refit_count,
            "universe_rows": self.universe_rows,
            "selected_rows": self.selected_rows,
            "metrics": self.metrics,
        }


def _complete_feature_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.dropna(subset=list(FEATURE_COLUMNS)).copy()


def training_samples(
    feature_frame: pd.DataFrame,
    minimum_cross_section: int,
) -> tuple[pd.DataFrame, list[date]]:
    """Return tradable samples whose target is the next eligible trading day."""
    complete = _complete_feature_rows(feature_frame)
    market_coverage = complete.groupby("trade_date")["security_id"].nunique()
    market_dates = sorted(
        market_coverage[market_coverage >= minimum_cross_section].index.tolist()
    )
    next_date = dict(zip(market_dates[:-1], market_dates[1:]))

    frame = complete[
        complete["target_return"].notna()
        & complete["target_date"].notna()
        & complete["historically_buyable"].fillna(False)
    ].copy()
    frame = frame[frame["trade_date"].isin(next_date)].copy()
    expected_target = frame["trade_date"].map(next_date)
    frame = frame[frame["target_date"] == expected_target]

    sample_coverage = frame.groupby("trade_date")["security_id"].nunique()
    eligible_dates = sorted(
        sample_coverage[sample_coverage >= minimum_cross_section].index.tolist()
    )
    if len(eligible_dates) < 3:
        raise ValueError(
            f"only {len(eligible_dates)} dates meet minimum cross-section "
            f"{minimum_cross_section}"
        )
    frame = frame[frame["trade_date"].isin(eligible_dates)].copy()
    return frame.reset_index(drop=True), eligible_dates


def _fit(samples: pd.DataFrame, config: ShortTermConfig) -> FittedShortTermModel:
    if samples.empty:
        raise ValueError("no training samples")
    signal_dates = sorted(samples["trade_date"].unique())
    if len(signal_dates) < 60:
        raise ValueError("at least 60 training dates are required")

    mean = samples.loc[:, FEATURE_COLUMNS].mean().to_numpy(dtype=float)
    std_series = samples.loc[:, FEATURE_COLUMNS].std().replace(0, 1.0).fillna(1.0)
    std = std_series.to_numpy(dtype=float)
    values = samples.loc[:, FEATURE_COLUMNS].to_numpy(dtype=float)
    matrix = np.column_stack([np.ones(len(values)), (values - mean) / std])

    date_rank = {value: index for index, value in enumerate(signal_dates)}
    last_rank = len(signal_dates) - 1
    half_life = max(config.refit_half_life_days, 1)
    weights = np.array([
        0.5 ** ((last_rank - date_rank[value]) / half_life)
        for value in samples["trade_date"]
    ])

    regularization = np.eye(matrix.shape[1]) * config.ridge_alpha
    regularization[0, 0] = 0.0
    normal = matrix.T @ (matrix * weights[:, None]) + regularization

    clipped_return = np.clip(
        samples["target_return"].to_numpy(dtype=float),
        -config.target_clip,
        config.target_clip,
    )
    up_label = (samples["target_return"].to_numpy(dtype=float) > 0).astype(float)
    return_coefficients = np.linalg.solve(normal, matrix.T @ (clipped_return * weights))
    probability_coefficients = np.linalg.solve(normal, matrix.T @ (up_label * weights))

    return FittedShortTermModel(
        feature_names=FEATURE_COLUMNS,
        feature_mean=mean,
        feature_std=std,
        return_coefficients=return_coefficients,
        probability_coefficients=probability_coefficients,
        train_start=signal_dates[0],
        train_end=signal_dates[-1],
        training_rows=len(samples),
    )


def _score_cross_section(predictions: pd.DataFrame) -> pd.DataFrame:
    out = predictions.copy()
    for name in ("predicted_return", "up_probability"):
        grouped = out.groupby("trade_date")[name]
        std = grouped.transform("std").replace(0, 1.0).fillna(1.0)
        out[f"{name}_z"] = (out[name] - grouped.transform("mean")) / std
    out["prediction_score"] = (
        out["predicted_return_z"] + 0.5 * out["up_probability_z"]
    )
    out["prediction_rank"] = out.groupby("trade_date")["prediction_score"].rank(
        ascending=False, method="first"
    )
    return out


def fit_latest(feature_frame: pd.DataFrame, config: ShortTermConfig) -> FittedShortTermModel:
    samples, _ = training_samples(feature_frame, config.minimum_cross_section)
    dates = sorted(samples["trade_date"].unique())
    window = set(dates[-config.training_window_days:])
    return _fit(samples[samples["trade_date"].isin(window)], config)


def rank_signal(
    model: FittedShortTermModel,
    feature_frame: pd.DataFrame,
    signal_date: date,
) -> pd.DataFrame:
    rows = _complete_feature_rows(feature_frame)
    rows = rows[rows["trade_date"] == signal_date].copy()
    if rows.empty:
        raise ValueError(f"no complete signal rows for {signal_date}")
    return _score_cross_section(model.predict(rows)).sort_values("prediction_rank")


def walk_forward_backtest(
    feature_frame: pd.DataFrame,
    config: ShortTermConfig,
) -> BacktestResult:
    """Refit at each month boundary and evaluate the latest holdout period."""
    samples, eligible_dates = training_samples(
        feature_frame, config.minimum_cross_section
    )
    if len(eligible_dates) <= config.validation_days + 60:
        raise ValueError("not enough eligible dates for the requested validation window")

    validation_dates = eligible_dates[-config.validation_days:]
    months = sorted({(value.year, value.month) for value in validation_dates})
    predictions: list[pd.DataFrame] = []

    for year, month in months:
        test_dates = [
            value for value in validation_dates
            if value.year == year and value.month == month
        ]
        test_start = min(test_dates)
        past_dates = [value for value in eligible_dates if value < test_start]
        train_window = set(past_dates[-config.training_window_days:])
        train = samples[
            samples["trade_date"].isin(train_window)
            & (samples["target_date"] < test_start)
        ]
        test = samples[samples["trade_date"].isin(test_dates)]
        model = _fit(train, config)
        batch = model.predict(test)
        batch["model_train_start"] = model.train_start
        batch["model_train_end"] = model.train_end
        predictions.append(batch)

    scored = _score_cross_section(pd.concat(predictions, ignore_index=True))
    selected = scored[scored["prediction_rank"] <= config.top_k].copy()
    selected_daily = selected.groupby("trade_date")["target_return"].mean()
    universe_daily = scored.groupby("trade_date")["target_return"].mean()

    rank_correlations = []
    for _, group in scored.groupby("trade_date"):
        rank_correlations.append(
            group["prediction_score"].rank().corr(group["target_return"].rank())
        )

    selected_mean = float(selected["target_return"].mean())
    universe_mean = float(scored["target_return"].mean())
    metrics: dict[str, float | int] = {
        "top_k": config.top_k,
        "selected_mean_return": selected_mean,
        "selected_stock_win_rate": float((selected["target_return"] > 0).mean()),
        "selected_daily_win_rate": float((selected_daily > 0).mean()),
        "universe_mean_return": universe_mean,
        "universe_stock_win_rate": float((scored["target_return"] > 0).mean()),
        "mean_return_spread": selected_mean - universe_mean,
        "mean_rank_ic": float(np.nanmean(rank_correlations)),
        "positive_rank_ic_days": float(np.mean(np.asarray(rank_correlations) > 0)),
        "selected_daily_volatility": float(selected_daily.std()),
        "universe_daily_volatility": float(universe_daily.std()),
    }
    return BacktestResult(
        config=config,
        validation_start=validation_dates[0],
        validation_end=validation_dates[-1],
        refit_count=len(months),
        universe_rows=len(scored),
        selected_rows=len(selected),
        metrics=metrics,
        predictions=scored,
    )
