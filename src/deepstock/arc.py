"""Continuous ARC portfolio accounting and fixed walk-forward reporting."""

from __future__ import annotations

from dataclasses import asdict
import numpy as np
import pandas as pd

from deepstock.backtest import BacktestResult, TRADING_DAYS_PER_YEAR
from deepstock.risk import RiskConfig, apply_risk_constraints


def apply_turnover_controls(
    targets: pd.DataFrame,
    execution_routes: pd.Series,
    rebalance_band: float = 0.05,
    route_cooldown_days: int = 5,
) -> pd.DataFrame:
    """Apply fixed portfolio-level turnover controls to route targets."""
    if not targets.index.equals(execution_routes.index):
        raise ValueError("Targets and execution routes must share the exact index.")
    if rebalance_band < 0 or route_cooldown_days < 0:
        raise ValueError("Turnover controls must be non-negative.")
    out = targets.copy().fillna(0.0)
    previous = out.iloc[0].copy()
    route_age = route_cooldown_days
    for i, date in enumerate(out.index):
        candidate = out.loc[date]
        changed_route = i > 0 and execution_routes.iloc[i] != execution_routes.iloc[i - 1]
        if changed_route:
            route_age = 0
        else:
            route_age += 1
        distance = float((candidate - previous).abs().sum())
        if i > 0 and distance <= rebalance_band:
            candidate = previous.copy()
        elif i > 0 and route_age < route_cooldown_days:
            candidate = previous.copy()
        out.loc[date] = candidate
        previous = candidate
    return out


def run_arc_portfolio(
    prices: pd.DataFrame,
    strategy_routes: pd.Series,
    route_targets: dict[str, pd.DataFrame],
    safe_asset: str = "SHY",
    benchmark: str = "SPY",
    transaction_cost_bps: float = 5.0,
    risk_config: RiskConfig | None = None,
    sector_by_symbol: dict[str, str] | None = None,
    data_valid: pd.Series | None = None,
    rebalance_band: float = 0.05,
    route_cooldown_days: int = 5,
) -> BacktestResult:
    """Combine route targets into one continuous, next-session ARC account."""
    if not strategy_routes.index.equals(prices.index):
        raise ValueError("Strategy routes must cover the exact price index.")
    symbols = list(prices.columns)
    targets = pd.DataFrame(0.0, index=prices.index, columns=symbols)
    for route, route_frame in route_targets.items():
        if not route_frame.index.equals(prices.index):
            raise ValueError("Each route target frame must cover the exact price index.")
        common = [c for c in route_frame.columns if c in symbols]
        mask = strategy_routes.eq(route)
        targets.loc[mask, common] = route_frame.loc[mask, common]
    returns = prices.pct_change(fill_method=None).fillna(0.0)
    targets = apply_risk_constraints(targets, returns, risk_config, sector_by_symbol, safe_asset, data_valid)
    execution_routes = strategy_routes.astype(str).shift(1).fillna(strategy_routes.astype(str).iloc[0])
    targets = apply_turnover_controls(targets, execution_routes, rebalance_band, route_cooldown_days)
    executed = targets.shift(1).fillna(0.0)
    traded = executed.diff().abs().sum(axis=1).fillna(executed.iloc[0].abs().sum())
    costs = traded * transaction_cost_bps / 10_000
    # Costs are paid when the close signal is executed on the next session.
    route_changed = execution_routes.ne(execution_routes.shift()).fillna(False)
    if len(route_changed):
        route_changed.iloc[0] = False
    reentry = route_changed & execution_routes.duplicated(keep=False)
    route_switch_cost = costs.where(route_changed, 0.0)
    reentry_cost = costs.where(reentry, 0.0)
    gross = (executed * returns).sum(axis=1)
    net = gross - costs
    equity = (1 + net).cumprod()
    bench = (1 + returns[benchmark]).cumprod()
    daily = pd.DataFrame({"portfolio_gross_return": gross, "transaction_cost": costs, "route_switch": route_changed.astype(int), "route_switch_cost": route_switch_cost, "reentry": reentry.astype(int), "reentry_cost": reentry_cost, "route_switch_turnover": (traded / 2).where(route_changed, 0.0), "strategy_rebalance_turnover": (traded / 2).where(~route_changed, 0.0), "portfolio_net_return": net, "turnover": traded / 2, "portfolio_equity": equity, "benchmark_equity": bench, "execution_route": execution_routes})
    vol = net.std(ddof=0)
    route_contribution = (
        daily.assign(route_gross_return=gross)
        .groupby("execution_route", observed=True)
        .agg(sessions=("route_gross_return", "size"), gross_return=("route_gross_return", "sum"), transaction_cost=("transaction_cost", "sum"))
        .reset_index()
        .to_dict("records")
    )
    return BacktestResult(daily, targets, executed, {"start": prices.index[0].date().isoformat(), "end": prices.index[-1].date().isoformat(), "trading_days": len(prices), "total_return": float(equity.iloc[-1]-1), "annualized_return": float(equity.iloc[-1] ** (TRADING_DAYS_PER_YEAR/len(prices))-1), "sharpe_ratio": float(net.mean()/vol*np.sqrt(TRADING_DAYS_PER_YEAR)) if vol else None, "maximum_drawdown": float((equity/equity.cummax()-1).min()), "total_turnover": float((traded/2).sum()), "total_transaction_cost": float(costs.sum()), "state_switches": int(route_changed.sum()), "state_switch_transaction_cost": float(route_switch_cost.sum()), "reentry_transaction_cost": float(reentry_cost.sum()), "route_contribution": route_contribution, "benchmark_total_return": float(bench.iloc[-1]-1), "risk_config": asdict(risk_config or RiskConfig())})


def fixed_walk_forward_windows(index: pd.DatetimeIndex, train_days: int = 504, test_days: int = 252, step_days: int = 252) -> list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]:
    """Create deterministic rolling windows; no parameter selection is done."""
    windows = []
    start = 0
    while start + train_days + test_days <= len(index):
        windows.append((index[start:start+train_days], index[start+train_days:start+train_days+test_days]))
        start += step_days
    return windows


def route_conditioned_performance(signals: pd.DataFrame, returns: pd.Series) -> pd.DataFrame:
    """Report each route only on sessions where ARC allows that route."""
    if not signals.index.equals(returns.index):
        raise ValueError("Signals and returns must share the exact index.")
    frame = pd.DataFrame({"route": signals["strategy_route"].astype(str), "return": returns})
    grouped = frame.dropna().groupby("route", observed=True)["return"]
    return grouped.agg(
        sessions="size",
        average_return="mean",
        cumulative_return=lambda values: float((1 + values).prod() - 1),
    ).reset_index()


def summarize_walk_forward(result: BacktestResult, windows: list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]) -> pd.DataFrame:
    """Summarize fixed test windows from one continuous causal run."""
    rows: list[dict[str, object]] = []
    for number, (_, test_dates) in enumerate(windows, start=1):
        daily = result.daily.loc[test_dates]
        net = daily["portfolio_net_return"]
        equity = (1 + net).cumprod()
        vol = net.std(ddof=0)
        route_switch = daily["route_switch"] if "route_switch" in daily else pd.Series(0, index=daily.index)
        route_switch_cost = daily["route_switch_cost"] if "route_switch_cost" in daily else pd.Series(0.0, index=daily.index)
        reentry_cost = daily["reentry_cost"] if "reentry_cost" in daily else pd.Series(0.0, index=daily.index)
        if "execution_route" in daily:
            route_contribution = daily.assign(route_gross_return=daily["portfolio_gross_return"]).groupby("execution_route", observed=True)["route_gross_return"].sum().to_dict()
        else:
            route_contribution = {"standalone": float(daily["portfolio_gross_return"].sum())}
        rows.append({
            "window": number,
            "start": test_dates[0].date().isoformat(),
            "end": test_dates[-1].date().isoformat(),
            "total_return": float(equity.iloc[-1] - 1),
            "annualized_return": float(equity.iloc[-1] ** (TRADING_DAYS_PER_YEAR / len(test_dates)) - 1),
            "sharpe_ratio": float(net.mean() / vol * np.sqrt(TRADING_DAYS_PER_YEAR)) if vol > 0 else None,
            "maximum_drawdown": float((equity / equity.cummax() - 1).min()),
            "transaction_cost": float(daily["transaction_cost"].sum()),
            "turnover": float(daily["turnover"].sum()),
            "state_switches": int(route_switch.sum()),
            "state_switch_transaction_cost": float(route_switch_cost.sum()),
            "reentry_transaction_cost": float(reentry_cost.sum()),
            "route_contribution": route_contribution,
        })
    return pd.DataFrame(rows)


def assess_walk_forward(
    table: pd.DataFrame,
    severe_loss_threshold: float = -0.20,
    maximum_drawdown_threshold: float = -0.30,
    turnover_threshold: float = 10.0,
) -> dict[str, object]:
    """Apply fixed research gates to a Walk-Forward report.

    This is an acceptance report only. It never searches, ranks, or modifies
    strategy parameters based on test-period values.
    """
    required = {"total_return", "maximum_drawdown", "turnover", "start", "end"}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError(f"Walk-forward table missing columns: {sorted(missing)}")
    failures: list[dict[str, object]] = []
    for row in table.to_dict("records"):
        if float(row["total_return"]) <= severe_loss_threshold:
            failures.append({"window": row.get("window"), "check": "severe_negative_return", "value": row["total_return"]})
        if float(row["maximum_drawdown"]) <= maximum_drawdown_threshold:
            failures.append({"window": row.get("window"), "check": "maximum_drawdown", "value": row["maximum_drawdown"]})
        if float(row["turnover"]) > turnover_threshold:
            failures.append({"window": row.get("window"), "check": "turnover", "value": row["turnover"]})
    return {"accepted": not failures, "window_count": int(len(table)), "failures": failures, "policy": "fixed thresholds; no OOS parameter selection"}
