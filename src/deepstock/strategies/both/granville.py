"""Independent causal daily ETF swing research, with market-specific ledgers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np
import pandas as pd

STRATEGY_ID = "granville_ma_swing"
VARIANTS = ("ma_cross", "trend_pullback", "deviation_reversal")


@dataclass
class GranvilleResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame


def validate_bars(bars: pd.DataFrame, calendar: pd.DatetimeIndex) -> None:
    fields = ["open", "high", "low", "close", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]
    if set(fields).difference(bars):
        raise ValueError("Raw/adjusted daily OHLC required")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.empty or bars.index.tz is not None or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Unique ordered exchange-session dates required")
    if not np.isfinite(bars[fields].to_numpy(dtype=float)).all() or (bars[fields] <= 0).any().any():
        raise ValueError("Finite positive OHLC required; no gap imputation")
    for prefix in ["", "adjusted_"]:
        if (bars[prefix + "high"] < bars[[prefix + "open", prefix + "close", prefix + "low"]].max(axis=1)).any() or (bars[prefix + "low"] > bars[[prefix + "open", prefix + "close", prefix + "high"]].min(axis=1)).any():
            raise ValueError("Inconsistent OHLC")
    calendar = pd.DatetimeIndex(calendar)
    if calendar.empty or calendar.tz is not None or calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Explicit unique ordered exchange calendar required")
    expected = calendar[(calendar >= bars.index[0]) & (calendar <= bars.index[-1])]
    if not expected.equals(bars.index):
        raise ValueError("Missing/unexpected exchange sessions; never assume zero returns")


def audit_cn_actions(bars: pd.DataFrame, dividends: pd.DataFrame) -> None:
    if "adj_factor" not in bars or "volume" not in bars or "pre_close" not in bars:
        raise ValueError("CN prices require factors, volume and exchange reference price")
    if not np.isfinite(bars[["adj_factor", "volume", "pre_close"]]).all().all() or (bars.adj_factor <= 0).any() or (bars.volume < 0).any() or (bars.pre_close <= 0).any():
        raise ValueError("Invalid CN factor/activity/reference price")
    dates = pd.to_datetime(dividends.ex_date)
    pays = pd.to_datetime(dividends.pay_date)
    if dates.isna().any() or pays.isna().any() or dates.duplicated().any() or (pays < dates).any():
        raise ValueError("Ambiguous dividend dates")
    if not np.isfinite(dividends.cash_per_share.to_numpy(dtype=float)).all() or dividends.cash_per_share.lt(0).any():
        raise ValueError("Invalid cash dividend")
    changes = bars.index[bars.adj_factor.pct_change(fill_method=None).abs().gt(1e-6)]
    if len(changes.difference(pd.DatetimeIndex(dates))):
        raise ValueError("Unexplained adjustment factor change/split; action audit required")
    in_span = pd.DatetimeIndex(dates[(dates >= bars.index[0]) & (dates <= bars.index[-1])])
    if len(in_span.difference(bars.index)):
        raise ValueError("Ex-date is not a priced exchange session")
    if "record_date" in dividends:
        for event in dividends.itertuples():
            ex = pd.Timestamp(event.ex_date)
            if ex in bars.index and bars.index.get_loc(ex) > 0 and pd.Timestamp(event.record_date) != bars.index[bars.index.get_loc(ex) - 1]:
                raise ValueError("Dividend entitlement cannot be inferred from prior close")
    for event in dividends.itertuples():
        ex = pd.Timestamp(event.ex_date)
        if ex not in bars.index or bars.index.get_loc(ex) == 0:
            continue
        loc = bars.index.get_loc(ex)
        prior = float(bars.close.iloc[loc - 1])
        cash = float(event.cash_per_share)
        if cash >= prior:
            raise ValueError("Dividend exceeds preceding price")
        # A dividend-only forward adjustment must reconcile to the reference
        # cash entitlement; tolerate factor precision and 0.001 ETF ticks.
        expected = prior / (prior - cash)
        actual = float(bars.adj_factor.iloc[loc] / bars.adj_factor.iloc[loc - 1])
        if not np.isclose(actual, expected, rtol=.001, atol=1e-5):
            raise ValueError("Factor magnitude inconsistent with cash dividend; possible split")


def generate_signals(bars: pd.DataFrame, rule: dict) -> pd.DataFrame:
    """All values at t use observations no later than close(t)."""
    close, high, low = (bars["adjusted_" + c] for c in ["close", "high", "low"])
    fast = close.rolling(rule["fast_days"]).mean()
    medium = close.rolling(rule["medium_days"]).mean()
    slow = close.rolling(rule["slow_days"]).mean()
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(rule["atr_days"]).mean().replace(0, np.nan)
    slope = (fast - fast.shift(rule["slope_days"])) / atr
    deviation = (close - fast) / atr
    ready = pd.concat([fast, medium, slow, atr, slope], axis=1).notna().all(axis=1)
    environment = ready & close.gt(slow) & medium.gt(slow)
    upward = slope.ge(rule["slope_min_atr"])
    above = close.gt(fast)
    confirmation = above.rolling(rule["confirmation_days"]).sum().eq(rule["confirmation_days"])
    touch = low.le(fast + rule["touch_band_atr"] * atr)
    touched = touch.rolling(rule["recovery_window"]).max().eq(1)
    below_buffer = close.lt(fast - rule["exit_buffer_atr"] * atr)
    trend_exit = below_buffer.rolling(rule["confirmation_days"]).sum().eq(rule["confirmation_days"]) | ~environment
    return pd.DataFrame({"ready": ready, "trend_environment": environment, "ma_fast": fast, "ma_medium": medium,
                         "ma_slow": slow, "atr": atr, "deviation_atr": deviation, "slope_atr": slope,
                         "ma_cross": environment & upward & confirmation & ~confirmation.shift(1, fill_value=False),
                         "trend_pullback": environment & upward & confirmation & touched & deviation.le(rule["recovery_max_atr"]),
                         "deviation_reversal": environment & deviation.le(rule["deviation_entry_atr"]),
                         "trend_exit": ready & trend_exit, "reversion_exit": ready & (deviation.ge(0) | ~environment)})


def round_quantity(value: float, lot: int) -> float:
    return float(np.floor(value / lot) * lot) if lot else float(value)


def run_granville(bars: pd.DataFrame, calendar: pd.DatetimeIndex, experiment: dict, market: str,
                 variant="trend_pullback", *, stress=False, dividends: pd.DataFrame | None = None,
                 benchmark_exposure: float | None = None) -> GranvilleResult:
    validate_bars(bars, calendar)
    if market not in {"US", "CN"} or variant not in (*VARIANTS, "buy_hold"):
        raise ValueError("Unknown market/variant")
    cfg, rule = experiment["markets"][market], experiment["signal"]
    for name in ["commission_bps", "minimum_commission", "slippage_bps", "stress_slippage_bps"]:
        if not np.isfinite(cfg[name]) or cfg[name] < 0:
            raise ValueError("Invalid cost contract")
    if not np.isfinite([experiment["initial_capital"], experiment["initial_target_exposure"]]).all() or min(experiment["initial_capital"], experiment["initial_target_exposure"]) <= 0 or experiment["initial_target_exposure"] > 1:
        raise ValueError("Invalid capital/exposure")
    for name in ["fast_days", "medium_days", "slow_days", "atr_days", "slope_days", "recovery_window", "confirmation_days", "minimum_hold_sessions", "maximum_hold_sessions", "cooldown_sessions"]:
        if isinstance(rule[name], bool) or not isinstance(rule[name], int) or rule[name] < (0 if name == "cooldown_sessions" else 1):
            raise ValueError("Invalid integer signal/holding period")
    if not rule["fast_days"] < rule["medium_days"] < rule["slow_days"] or not np.isfinite(list(rule.values())).all():
        raise ValueError("Invalid moving-average/threshold contract")
    if not 1 <= rule["minimum_hold_sessions"] <= rule["maximum_hold_sessions"] or rule["cooldown_sessions"] < 0 or not 0 < rule["stop_loss"] < 1:
        raise ValueError("Invalid holding/risk contract")
    dividends = dividends if dividends is not None else pd.DataFrame(columns=["ex_date", "pay_date", "cash_per_share"])
    if market == "CN":
        if cfg["lot_size"] != 100 or not 0 < cfg["prior_volume_participation"] <= 1 or not 0 < cfg["daily_limit"] < 1 or cfg["stamp_duty_bps"] != 0:
            raise ValueError("Invalid CN ETF mechanics")
        audit_cn_actions(bars, dividends)
    signals = generate_signals(bars, rule)
    selected = bars.loc[experiment["evaluation_start"]:experiment["evaluation_end"]]
    if selected.empty or not signals.loc[selected.index, "ready"].all():
        raise ValueError("Insufficient pre-evaluation indicator warm-up")
    events = {pd.Timestamp(r.ex_date): {"pay": pd.Timestamp(r.pay_date), "cash": float(r.cash_per_share)} for r in dividends.itertuples()}
    slip = cfg["stress_slippage_bps"] if stress else cfg["slippage_bps"]
    fraction = slip / 10000
    if fraction >= 1:
        raise ValueError("Slippage must be below 100%")
    target = experiment["initial_target_exposure"] if benchmark_exposure is None else benchmark_exposure
    if not 0 < target <= 1:
        raise ValueError("Invalid benchmark exposure")
    cash = nav_previous = float(experiment["initial_capital"])
    quantity = 0.0
    receivables: list[dict] = []
    pending, pending_reason, pending_signal = "", "", None
    entry_day, entry_adjusted, last_exit = -1, 0.0, -10_000
    records, trades = [], []
    first_loc = bars.index.get_loc(selected.index[0])
    if variant == "buy_hold":
        pending, pending_reason = "BUY", "benchmark_initial"
    elif first_loc > 0 and bool(signals.iloc[first_loc - 1][variant]):
        pending, pending_reason, pending_signal = "BUY", variant, bars.index[first_loc - 1]

    for i, (day, row) in enumerate(selected.iterrows()):
        loc = bars.index.get_loc(day)
        signal = signals.loc[day]
        fee = slippage_cash = traded_notional = dividend_accrual = 0.0
        entry_fill = exit_fill = blocked_entry = delayed_exit = 0
        if day in events and quantity > 0:
            amount = quantity * events[day]["cash"]
            receivables.append({"pay": events[day]["pay"], "amount": amount})
            dividend_accrual += amount  # Entitlement precedes ex-date open trades.
        opening = float(row.open)
        opening_nav = cash + quantity * opening + sum(r["amount"] for r in receivables)
        capacity = float("inf")
        if market == "CN":
            capacity = round_quantity(float(bars.volume.iloc[loc - 1]) * cfg["prior_volume_participation"], cfg["lot_size"]) if loc > 0 else 0
        if pending:
            action = pending
            limit_block = False
            if market == "CN":
                upper = round(float(row.pre_close) * (1 + cfg["daily_limit"]), 3)
                lower = round(float(row.pre_close) * (1 - cfg["daily_limit"]), 3)
                limit_block = opening >= upper - .0005 if action == "BUY" else opening <= lower + .0005
            allowed = not limit_block and capacity > 0 and (action != "SELL" or market != "CN" or i > entry_day)
            if allowed:
                execution = opening * (1 + fraction if action == "BUY" else 1 - fraction)
                if action == "BUY":
                    budget = min(cash, target * opening_nav)
                    qty = min(capacity, round_quantity((budget - cfg["minimum_commission"]) / (execution * (1 + cfg["commission_bps"] / 10000)), cfg["lot_size"]))
                    qty = max(0.0, qty)
                else:
                    qty = min(quantity, capacity)
                if qty > 0:
                    notional = qty * execution
                    fee = max(cfg["minimum_commission"], notional * cfg["commission_bps"] / 10000)
                    slippage_cash = qty * abs(execution - opening)
                    traded_notional = qty * opening
                    if action == "BUY":
                        cash -= notional + fee
                        quantity += qty
                        entry_day, entry_adjusted, entry_fill = i, float(row.adjusted_open) * (1 + fraction), 1
                    else:
                        cash += notional - fee
                        quantity -= qty
                        exit_fill = 1
                        if quantity < 1e-9:
                            quantity = 0.0
                            last_exit = i
                    trades.append({"date": day.date().isoformat(), "signal_date": pending_signal.date().isoformat() if pending_signal is not None else None,
                                   "action": action, "reason": pending_reason, "quantity": qty, "open_reference": opening,
                                   "execution_price": execution, "commission": fee, "slippage": slippage_cash,
                                   "held_sessions": i - entry_day if action == "SELL" else 0, "cash_after": cash})
                else:
                    blocked_entry = int(action == "BUY")
                    delayed_exit = int(action == "SELL")
            else:
                blocked_entry = int(action == "BUY")
                delayed_exit = int(action == "SELL")
            if (action == "BUY" and (variant != "buy_hold" or quantity > 0)) or (action == "SELL" and quantity == 0):
                pending = ""
            elif action == "SELL":
                delayed_exit = int(quantity > 0)  # Partial/blocked exits retain intent.
        # Dividend cash becomes usable after the pay-date session, never at an
        # unknown intraday payment time before the opening fill.
        for claim in list(receivables):
            if claim["pay"] <= day:
                cash += claim["amount"]
                receivables.remove(claim)
        receivable = sum(r["amount"] for r in receivables)
        nav = cash + quantity * float(row.close) + receivable
        if cash < -1e-6 or not np.isfinite(nav) or nav <= 0:
            raise ValueError("Ledger insolvency/invalid mark; no leverage allowed")
        holding = i - entry_day + 1 if quantity > 0 else 0
        risk_stop = quantity > 0 and float(row.adjusted_close) / entry_adjusted - 1 <= -rule["stop_loss"]
        if variant != "buy_hold" and not pending:
            if quantity > 0:
                normal_exit = bool(signal.reversion_exit if variant == "deviation_reversal" else signal.trend_exit)
                reason = "close_stop" if risk_stop else "time_exit" if holding >= rule["maximum_hold_sessions"] else "signal_exit" if normal_exit and holding >= rule["minimum_hold_sessions"] else ""
                if reason:
                    pending, pending_reason, pending_signal = "SELL", reason, day
            elif bool(signal[variant]) and i - last_exit > rule["cooldown_sessions"]:
                pending, pending_reason, pending_signal = "BUY", variant, day
        cost_ratio = (fee + slippage_cash) / nav_previous
        net = nav / nav_previous - 1
        records.append({"date": day, "portfolio_net_return": net, "portfolio_gross_return": net + cost_ratio,
                        "portfolio_equity": nav / experiment["initial_capital"], "nav": nav, "cash": cash,
                        "quantity": quantity, "receivable": receivable, "dividend_accrual": dividend_accrual,
                        "commission": fee, "slippage": slippage_cash, "transaction_cost": cost_ratio,
                        "turnover": traded_notional / (2 * nav_previous), "exposure": quantity * float(row.close) / nav,
                        "entry_fill": entry_fill, "exit_fill": exit_fill, "blocked_entry": blocked_entry,
                        "delayed_exit": delayed_exit, "held_sessions": holding,
                        "entry_signal": int(bool(signal[variant])) if variant != "buy_hold" else 0,
                        "pending_action": pending, "pending_reason": pending_reason if pending else ""})
        nav_previous = nav
    daily = pd.DataFrame(records).set_index("date")
    return GranvilleResult(daily, pd.DataFrame(trades), signals.loc[selected.index])


def summarize(daily: pd.DataFrame) -> dict[str, Any]:
    if daily.empty:
        raise ValueError("Empty evaluation period")
    returns = daily.portfolio_net_return
    if not np.isfinite(returns).all() or (returns < -1).any():
        raise ValueError("Invalid continuous daily returns")
    equity = (1 + returns).cumprod()
    vol = float(returns.std(ddof=0))
    return {"start": daily.index[0].date().isoformat(), "end": daily.index[-1].date().isoformat(), "trading_days": len(daily),
            "total_return": float(equity.iloc[-1] - 1), "annualized_return": float(equity.iloc[-1] ** (252 / len(daily)) - 1),
            "sharpe_ratio": float(returns.mean() / vol * np.sqrt(252)) if vol > 0 else None,
            "maximum_drawdown": float((equity / equity.cummax().clip(lower=1) - 1).min()),
            "total_turnover": float(daily.turnover.sum()), "annualized_turnover": float(daily.turnover.sum() * 252 / len(daily)),
            "total_transaction_cost": float(daily.transaction_cost.sum()), "commission": float(daily.commission.sum()),
            "slippage": float(daily.slippage.sum()), "average_exposure": float(daily.exposure.mean()),
            "maximum_observed_exposure": float(daily.exposure.max()), "entry_fills": int(daily.entry_fill.sum()),
            "exit_fills": int(daily.exit_fill.sum()), "entry_signals": int(daily.entry_signal.sum()),
            "blocked_entries": int(daily.blocked_entry.sum()), "delayed_exit_sessions": int(daily.delayed_exit.sum()),
            "cash_sessions": int(daily.quantity.eq(0).sum()), "unclosed_quantity": float(daily.quantity.iloc[-1]),
            "unpaid_dividends": float(daily.receivable.iloc[-1])}


def walk_forward(daily: pd.DataFrame, benchmark: pd.DataFrame, rule: dict) -> list[dict]:
    if not daily.index.equals(benchmark.index):
        raise ValueError("Walk-forward benchmark needs the identical session scope")
    records = []
    for first in range(rule["history_sessions"], len(daily) - rule["test_sessions"] + 1, rule["step_sessions"]):
        segment = daily.iloc[first:first + rule["test_sessions"]]
        records.append({"history_start": daily.index[first - rule["history_sessions"]].date().isoformat(),
                        "history_end": daily.index[first - 1].date().isoformat(), **summarize(segment),
                        "benchmark": summarize(benchmark.loc[segment.index])})
    return records
