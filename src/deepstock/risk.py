"""Strategy-independent portfolio risk constraints for ARC research."""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class RiskConfig:
    max_total_exposure: float = 1.0
    max_symbol_weight: float = 0.20
    max_sector_exposure: float = 0.40
    daily_loss_limit: float = 0.05
    drawdown_deleveraging: float = 0.15

    def __post_init__(self) -> None:
        if not 0 < self.max_total_exposure <= 1 or not 0 < self.max_symbol_weight <= 1:
            raise ValueError("Exposure limits must be in (0, 1].")
        if not 0 < self.max_sector_exposure <= 1 or self.daily_loss_limit <= 0 or self.drawdown_deleveraging <= 0:
            raise ValueError("Risk limits must be positive.")


def apply_risk_constraints(
    targets: pd.DataFrame,
    returns: pd.DataFrame,
    config: RiskConfig | None = None,
    sector_by_symbol: dict[str, str] | None = None,
    safe_asset: str = "SHY",
    data_valid: pd.Series | None = None,
) -> pd.DataFrame:
    """Clip targets without changing their signal timing.

    Loss and drawdown gates are based only on returns observed before the
    target date. Missing/invalid data routes the portfolio to the safe asset.
    """
    config = config or RiskConfig()
    if not targets.index.equals(returns.index):
        raise ValueError("Targets and returns must share the exact index.")
    if safe_asset not in targets.columns or safe_asset not in returns.columns:
        raise ValueError("Safe asset must be present in targets and returns.")
    if data_valid is not None:
        if not data_valid.index.equals(targets.index):
            raise ValueError("Data-validity flags must cover the exact target index.")
        data_valid = data_valid.astype(bool)
    sectors = sector_by_symbol or {}
    out = pd.DataFrame(0.0, index=targets.index, columns=targets.columns)
    equity = 1.0
    peak = 1.0
    previous_loss = False
    for i, date in enumerate(targets.index):
        if i:
            realized = float((out.iloc[i - 1] * returns.iloc[i]).sum())
            equity *= 1 + realized
            peak = max(peak, equity)
            previous_loss = realized <= -config.daily_loss_limit
        row = targets.loc[date].fillna(0.0).clip(lower=0.0)
        invalid = (
            not row.index.isin(returns.columns).all()
            or not returns.loc[date].replace([float("inf"), float("-inf")], pd.NA).notna().all()
            or (data_valid is not None and not bool(data_valid.at[date]))
        )
        drawdown = equity / peak - 1.0
        if invalid or previous_loss or drawdown <= -config.drawdown_deleveraging:
            out.at[date, safe_asset] = 1.0
            continue
        row = row.clip(upper=config.max_symbol_weight)
        sector_totals: dict[str, float] = {}
        for symbol, weight in row.items():
            if symbol == safe_asset:
                continue
            sector = sectors.get(symbol, symbol)
            sector_totals[sector] = sector_totals.get(sector, 0.0) + float(weight)
        for symbol, weight in row.items():
            if symbol == safe_asset:
                continue
            sector = sectors.get(symbol, symbol)
            if sector_totals[sector] > config.max_sector_exposure:
                row.loc[symbol] = float(weight) * config.max_sector_exposure / sector_totals[sector]
        risk_sum = float(row.drop(labels=safe_asset, errors="ignore").sum())
        risk_sum = min(risk_sum, config.max_total_exposure)
        risk = row.drop(labels=safe_asset, errors="ignore")
        if risk.sum() > 0:
            row.loc[risk.index] = risk * risk_sum / risk.sum()
        row.loc[safe_asset] = max(0.0, 1.0 - float(row.drop(labels=safe_asset, errors="ignore").sum()))
        out.loc[date] = row
    return out
