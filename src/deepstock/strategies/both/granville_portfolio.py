"""Causal multi-stock research: free-slot rotation, no broker/order imports."""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .granville import generate_signals, summarize, VARIANTS
from deepstock.data.stock_actions import reconcile_stock_actions


class PortfolioDataError(ValueError):
    pass


@dataclass
class StockPanel:
    dates: pd.DatetimeIndex
    symbols: list[str]
    values: dict[str, np.ndarray]
    signals: dict[str, np.ndarray]
    eligible: np.ndarray
    tradable: np.ndarray
    terminal: dict[str, str]
    documented_suspension: np.ndarray
    diagnostics: dict


def make_panel(bars, calendar, membership, market, signal_rule, portfolio_rule, *, suspensions=None, dividends=None, terminal=None):
    """Missing executable bars stay NaN. Stale valuation is separate state."""
    dates = pd.DatetimeIndex(calendar)
    if dates.empty or dates.has_duplicates or dates.tz is not None or not dates.is_monotonic_increasing:
        raise PortfolioDataError("Unique ordered session calendar required")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars.date)
    if bars.duplicated(["date", "symbol"]).any() or len(pd.DatetimeIndex(bars.date.unique()).difference(dates)):
        raise PortfolioDataError("Conflicting/unexpected stock sessions")
    symbols = sorted(membership.symbol.unique())
    if set(symbols).difference(bars.symbol):
        raise PortfolioDataError("Historical constituent has no bars; cannot replace it with a survivor")
    fields = ["open", "high", "low", "close", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume", "turnover"]
    if set(fields).difference(bars):
        raise PortfolioDataError("Raw/adjusted OHLCV/turnover required")
    prices = fields[:8]
    if not np.isfinite(bars[fields]).all().all() or (bars[prices] <= 0).any().any() or (bars[["volume", "turnover"]] < 0).any().any():
        raise PortfolioDataError("Invalid executable/reference prices or activity")
    for prefix in ["", "adjusted_"]:
        if (bars[prefix + "high"] < bars[[prefix + "open", prefix + "close", prefix + "low"]].max(axis=1)).any() or (bars[prefix + "low"] > bars[[prefix + "open", prefix + "close", prefix + "high"]].min(axis=1)).any():
            raise PortfolioDataError("Inconsistent OHLC")
    if market == "CN":
        if {"up_limit", "down_limit", "adj_factor"}.difference(bars) or not np.isfinite(bars[["up_limit", "down_limit", "adj_factor"]]).all().all():
            raise PortfolioDataError("Actual CN limits and factors required")
        fields += ["up_limit", "down_limit", "adj_factor"]
    panels = {name: bars.pivot(index="date", columns="symbol", values=name).reindex(index=dates, columns=symbols) for name in fields}
    eligible = pd.DataFrame(False, index=dates, columns=symbols)
    if market == "US":
        for row in membership.itertuples():
            eligible.loc[(dates >= pd.Timestamp(row.date)) & (dates <= pd.Timestamp(row.end)), row.symbol] = True
    else:
        snap = membership.copy()
        snap["date"] = pd.to_datetime(snap.date)
        snapshots = sorted(snap.date.unique())
        for i, date in enumerate(snapshots):
            members = snap.loc[snap.date.eq(date), "symbol"]
            stop = pd.Timestamp(snapshots[i + 1]) if i + 1 < len(snapshots) else dates[-1]
            # Do not assume the snapshot was public at its own closing instant.
            eligible.loc[(dates > date) & (dates <= stop), members] = True
    tradable = panels["volume"].gt(0) & panels["open"].notna()
    suspension_dates = {symbol: set() for symbol in symbols}
    if suspensions is not None and not suspensions.empty:
        events = suspensions.copy()
        events["trade_date"] = pd.to_datetime(events.trade_date)
        for symbol, group in events.groupby("ts_code"):
            if symbol not in suspension_dates:
                continue
            observed = panels["open"][symbol].dropna().index
            for event in group.sort_values("trade_date").itertuples():
                if event.suspend_type == "S":
                    resume = observed[observed > event.trade_date]
                    stop = resume[0] if len(resume) else dates[-1] + pd.Timedelta(days=1)
                    suspension_dates[symbol].update(dates[(dates >= event.trade_date) & (dates < stop)])
                    # Intraday suspension: daily open may be a later executable
                    # price. Conservatively do not fill this whole event day.
                    if event.trade_date in dates:
                        tradable.loc[event.trade_date, symbol] = False
    missing_count = 0
    pre_eligibility_gaps = []
    for symbol in symbols:
        observed = panels["close"][symbol].dropna().index
        terminal_date = (terminal or {}).get(symbol)
        expected_end = min(dates[-1], pd.Timestamp(terminal_date)) if terminal_date else dates[-1]
        internal = dates[(dates >= observed[0]) & (dates <= expected_end)].difference(observed)
        unsupported = set(internal).difference(suspension_dates[symbol])
        if unsupported:
            first_eligible = pd.to_datetime(membership.loc[membership.symbol.eq(symbol), "date"]).min()
            if market == "US" and max(unsupported) < first_eligible and max(unsupported) < pd.Timestamp(portfolio_rule["evaluation_start"]):
                # Historical pre-index/OTC quotes can be sparse. They remain
                # NaN, and rolling readiness is invalid until sufficient
                # continuous history is observed. Never fill/drop sessions or
                # exclude the security from its later eligible stock pool.
                pre_eligibility_gaps.append({"symbol": symbol, "missing_sessions": len(unsupported),
                                             "first": str(min(unsupported).date()), "last": str(max(unsupported).date()),
                                             "first_eligible": str(first_eligible.date()), "policy": "unheld_pre_eligibility_warmup_NaN_no_signal_until_continuous_history"})
            else:
                raise PortfolioDataError(f"Unexplained internal quote gaps: {symbol}: {sorted(unsupported)[0].date()}")
        missing_count += len(set(internal).intersection(suspension_dates[symbol]))
    signal_frames = {name: pd.DataFrame(False, index=dates, columns=symbols) for name in [*VARIANTS, "ready", "trend_exit", "reversion_exit"]}
    for symbol in symbols:
        frame = pd.DataFrame({name: panels[name][symbol] for name in prices})
        signals = generate_signals(frame, signal_rule)
        for name in signal_frames:
            signal_frames[name][symbol] = signals[name]
    lookback = portfolio_rule["relative_strength_sessions"]
    panels["strength"] = panels["adjusted_close"] / panels["adjusted_close"].shift(lookback) - 1
    panels["average_turnover"] = panels["turnover"].rolling(portfolio_rule["liquidity_sessions"], min_periods=portfolio_rule["liquidity_sessions"]).mean()
    # Close-indexed; entry reads i-1, never the fill day's return.
    panels["sizing_volatility"] = panels["adjusted_close"].pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=0)
    tax = pd.DataFrame(0., index=dates, columns=symbols)
    action_audit = []
    rounding_moves = []
    if market == "CN":
        if dividends is None:
            raise PortfolioDataError("CN dividend evidence required")
        try:
            declarations, action_audit = reconcile_stock_actions(bars, dividends, dates[0], dates[-1], allow_unresolved_entitlements=True)
        except ValueError as error:
            raise PortfolioDataError(str(error)) from error
        declarations["ex_date"] = pd.to_datetime(declarations.ex_date, errors="coerce")
        declarations["ann_date"] = pd.to_datetime(declarations.ann_date, errors="coerce")
        unresolved = declarations.ex_date.isna() & declarations.ann_date.between(pd.Timestamp(portfolio_rule["source_start"]), pd.Timestamp(portfolio_rule["evaluation_end"]))
        if unresolved.any():
            raise PortfolioDataError("Implemented recent dividend lacks ex-date")
        active = declarations.loc[declarations.ex_date.between(dates[0], dates[-1])].copy()
        event_key = ["ts_code", "ex_date"] + (["end_date"] if "end_date" in active else [])
        active = active.drop_duplicates(event_key)
        amounts = pd.to_numeric(active.cash_div_tax, errors="coerce").to_numpy(dtype=float)
        if np.isinf(amounts).any() or (amounts < 0).any() or active.ann_date.isna().any() or (active.ann_date > active.ex_date).any():
            raise PortfolioDataError("Ambiguous dividend amount/announcement date")
        for event in active.itertuples():
            if event.ex_date in dates and event.ts_code in symbols:
                tax.loc[event.ex_date, event.ts_code] += float(event.cash_div_tax) * portfolio_rule["markets"][market]["dividend_tax_rate"]
        for symbol in symbols:
            factor = panels["adj_factor"][symbol].dropna()
            changes = factor.index[factor.pct_change(fill_method=None).abs().gt(1e-6)]
            events = pd.DatetimeIndex(active.loc[active.ts_code.eq(symbol), "ex_date"])
            for changed in changes:
                prior = factor.index[factor.index < changed][-1]
                if not len(events[(events > prior) & (events <= changed)]):
                    a, b = factor.loc[prior], factor.loc[changed]
                    qa = 10. ** (-max(3, len(str(a).partition(".")[2]))) / 2
                    qb = 10. ** (-max(3, len(str(b).partition(".")[2]))) / 2
                    source_rows = bars.loc[bars.symbol.eq(symbol)].set_index("date")
                    unchanged_reference = "pre_close" in source_rows and abs(source_rows.loc[changed, "pre_close"] - source_rows.loc[prior, "close"]) <= .005
                    inactive_warmup = changed < pd.Timestamp(portfolio_rule["evaluation_start"])
                    bounded_precision = abs(b - a) <= max(qa + qb, .001) + 1e-10 and abs(b / a - 1) <= .001
                    if inactive_warmup and unchanged_reference and bounded_precision:
                        rounding_moves.append({"symbol": symbol, "date": str(changed.date()), "relative_factor_move": float(b / a - 1),
                                               "policy": "small_source_factor_revision_in_unheld_warmup_unchanged_raw_ex_reference_no_price_repair"})
                        continue
                    raise PortfolioDataError(f"Unexplained factor change: {symbol}: {changed.date()}")
    panels["cash_dividend_tax_per_raw_share"] = tax
    documented = pd.DataFrame(False, index=dates, columns=symbols)
    for symbol, missing in suspension_dates.items():
        if missing:
            documented.loc[sorted(missing), symbol] = True
    return StockPanel(dates, symbols, {k: v.to_numpy(dtype=float) for k, v in panels.items()},
                      {k: v.to_numpy(dtype=bool) for k, v in signal_frames.items()}, eligible.to_numpy(), tradable.to_numpy(), terminal or {},
                      documented.to_numpy(),
                      {"symbols": len(symbols), "documented_internal_suspension_sessions": missing_count,
                       "corporate_action_audit": action_audit,
                       "pre_eligibility_incomplete_warmup": pre_eligibility_gaps,
                       "rounding_only_factor_movements": rounding_moves,
                       "adjustment_accounting": "analytical_total_return_units_not_actual_broker_shares"})


def signal_episodes(signal):
    """Number continuous true runs from past/present closes; gaps re-arm."""
    prior = np.vstack([np.zeros((1, signal.shape[1]), dtype=bool), signal[:-1]])
    return np.cumsum(signal & ~prior, axis=0)


def sizing_reference(panel, config, market, close_index):
    """Point-in-time pool median, not ranked/ultimately bought names."""
    v = panel.values
    vol = v["sizing_volatility"][close_index]
    eligible = panel.eligible[close_index] & panel.signals["ready"][close_index]
    eligible &= v["average_turnover"][close_index] >= config["markets"][market]["minimum_average_turnover"]
    eligible &= np.isfinite(vol) & (vol > 0)
    return (float(np.median(vol[eligible])) if eligible.any() else np.nan), int(eligible.sum())


def run_stock_portfolio(panel, config, signal_rule, market, variant="trend_pullback", exit_policy="time_7", stress=False, *, entry_policy="signal_level", sizing_policy="fixed_16pct"):
    if variant not in VARIANTS or exit_policy not in {"time_7", "trend_only"} or market not in {"US", "CN"}:
        raise ValueError("Unknown fixed candidate")
    if entry_policy not in {"signal_level", "once_per_episode"}:
        raise ValueError("Unknown entry episode policy")
    if sizing_policy not in {"fixed_16pct", "volatility_shrink_only"} or (sizing_policy != "fixed_16pct" and market != "US"):
        raise ValueError("Unknown sizing policy or non-US experiment")
    cfg = config["markets"][market]
    if not 0 < config["initial_position_target"] <= config["single_entry_ceiling"] <= config["gross_entry_ceiling"] <= 1 or config["max_positions"] != 5:
        raise ValueError("Invalid fixed portfolio entry caps")
    if config["cash_return"] != 0 or config["paper_authorized"] or config["live_authorized"]:
        raise ValueError("No cash-yield/authorization changes in this fixed research")
    v, sig = panel.values, panel.signals
    n = len(panel.symbols)
    episodes = signal_episodes(sig[variant])
    consumed_episode = np.full(n, -1)
    entry_episode = np.full(n, -1)
    units = np.zeros(n)
    entry_price = np.zeros(n)
    entry_day = np.full(n, -1)
    last_exit = np.full(n, -10000)
    previous_mark = np.full(n, np.nan)
    previous_raw_ratio = np.full(n, np.nan)
    pending = {}
    cash = previous_nav = float(config["initial_capital"])
    slip = (cfg["stress_slippage_bps"] if stress else cfg["slippage_bps"]) / 10000
    dates = panel.dates
    chosen = np.flatnonzero((dates >= pd.Timestamp(config["evaluation_start"])) & (dates <= pd.Timestamp(config["evaluation_end"])))
    if not len(chosen) or chosen[0] == 0:
        raise PortfolioDataError("Evaluation/warm-up unavailable")
    daily, trades, sizing_audit = [], [], []
    for i in chosen:
        day = dates[i]
        fee = slippage_cash = tax = traded = 0.
        entries = exits = blocked = delayed = stale = 0
        sold_today = set()
        held = np.flatnonzero(units > 1e-10)
        for j in held:
            ex_tax = v["cash_dividend_tax_per_raw_share"][i, j]
            if not np.isfinite(ex_tax):
                raise PortfolioDataError(f"Held dividend cash/tax entitlement unspecified: {panel.symbols[j]} at {day.date()}")
            if ex_tax:
                if not np.isfinite(previous_raw_ratio[j]):
                    raise PortfolioDataError("Dividend tax lacks preceding share-equivalent evidence")
                charge = units[j] * previous_raw_ratio[j] * ex_tax
                cash -= charge
                tax += charge
            if not np.isfinite(v["adjusted_close"][i, j]):
                symbol = panel.symbols[j]
                final_date = panel.terminal.get(symbol)
                if final_date and day > pd.Timestamp(final_date):
                    raise PortfolioDataError(f"Held terminal security lacks verified proceeds: {symbol} at {day.date()}")
                if not panel.documented_suspension[i, j]:
                    raise PortfolioDataError(f"Held quote gap lacks suspension evidence: {symbol} at {day.date()}")
                stale += 1
        marks_open = np.where(np.isfinite(v["adjusted_open"][i]), v["adjusted_open"][i], previous_mark)
        holding_open = float(np.dot(units[held], marks_open[held])) if len(held) else 0.
        opening_nav = cash + holding_open
        # A buy list is frozen from the immediately preceding close. Failed
        # sales do not free slots/cash; later ranks are fill alternatives only.
        for j in sorted(list(pending), key=lambda idx: panel.symbols[idx]):
            if units[j] <= 1e-10:
                pending.pop(j)
                continue
            legal = panel.tradable[i, j] and np.isfinite(v["adjusted_open"][i, j]) and (market != "CN" or i > entry_day[j])
            if market == "CN" and legal:
                legal = v["open"][i, j] > v["down_limit"][i, j] + .005
            prior_capacity = v["volume"][i - 1, j] * config["prior_volume_participation"]
            ratio = v["adjusted_open"][i, j] / v["open"][i, j] if legal else np.nan
            qty = min(units[j], prior_capacity / ratio) if legal and np.isfinite(prior_capacity) else 0.
            if qty <= 1e-10:
                delayed += 1
                continue
            execution = v["adjusted_open"][i, j] * (1 - slip)
            notional = qty * execution
            commission = max(cfg["minimum_commission"], notional * cfg["commission_bps"] / 10000)
            charges = commission + notional * (cfg["sell_stamp_duty_bps"] + cfg["transfer_bps"]) / 10000
            cost_slip = qty * v["adjusted_open"][i, j] * slip
            cash += notional - charges
            units[j] -= qty
            fee += charges
            slippage_cash += cost_slip
            traded += qty * v["adjusted_open"][i, j]
            exits += 1
            reason, date_signal = pending[j]
            trades.append({"date": str(day.date()), "symbol": panel.symbols[j], "action": "SELL", "signal_date": str(date_signal.date()),
                           "reason": reason, "analytical_units": qty, "entry_raw_shares_reference": None, "analytical_execution_price": execution,
                           "charges": charges, "slippage": cost_slip, "held_sessions": i - entry_day[j], "cash_after": cash})
            trades[-1]["signal_episode"] = int(entry_episode[j])
            sold_today.add(j)
            if units[j] <= 1e-10:
                units[j] = 0.
                last_exit[j] = i
                pending.pop(j)
            else:
                delayed += 1
        candidate = sig[variant][i - 1] & sig["ready"][i - 1] & panel.eligible[i - 1] & (units <= 1e-10)
        candidate &= np.isfinite(v["strength"][i - 1]) & (v["average_turnover"][i - 1] >= cfg["minimum_average_turnover"])
        candidate &= i - last_exit > signal_rule["cooldown_sessions"]
        repeated = candidate & (episodes[i - 1] == consumed_episode)
        if entry_policy == "once_per_episode":
            candidate &= ~repeated
        ranked = sorted(np.flatnonzero(candidate), key=lambda j: (-v["strength"][i - 1, j], panel.symbols[j]))
        reference, reference_count = sizing_reference(panel, config, market, i - 1) if sizing_policy == "volatility_shrink_only" else (np.nan, 0)
        invalid_sizing = 0
        for j in ranked:
            if (units > 1e-10).sum() >= config["max_positions"]:
                break
            if j in sold_today:
                continue
            legal = panel.tradable[i, j] and np.isfinite(v["adjusted_open"][i, j])
            if market == "CN" and legal:
                legal = v["open"][i, j] < v["up_limit"][i, j] - .005
            if not legal or not np.isfinite(v["volume"][i - 1, j]):
                blocked += 1
                continue
            current_held = np.flatnonzero(units > 1e-10)
            invested = float(np.dot(units[current_held], marks_open[current_held])) if len(current_held) else 0.
            target = config["initial_position_target"]
            vol = v["sizing_volatility"][i - 1, j] if sizing_policy == "volatility_shrink_only" else np.nan
            if sizing_policy == "volatility_shrink_only":
                if not np.isfinite(reference) or reference <= 0 or not np.isfinite(vol) or vol <= 0:
                    blocked += 1
                    invalid_sizing += 1
                    sizing_audit.append({"date": str(day.date()), "symbol": panel.symbols[j], "signal_date": str(dates[i - 1].date()),
                                         "status": "blocked_invalid_volatility", "target_weight": None,
                                         "volatility": float(vol) if np.isfinite(vol) else None,
                                         "pool_median": float(reference) if np.isfinite(reference) else None, "pool_count": reference_count})
                    continue
                target *= min(1., reference / vol)
            budget = min(cash, target * opening_nav, config["gross_entry_ceiling"] * opening_nav - invested)
            raw_execution = v["open"][i, j] * (1 + slip)
            buy_rate = (cfg["commission_bps"] + cfg["transfer_bps"]) / 10000
            affordable = max(0., (budget - cfg["minimum_commission"]) / (raw_execution * (1 + buy_rate)))
            capacity = v["volume"][i - 1, j] * config["prior_volume_participation"]
            shares = np.floor(min(affordable, capacity) / cfg["lot_size"]) * cfg["lot_size"]
            if shares <= 0:
                blocked += 1
                continue
            sizing_audit.append({"date": str(day.date()), "symbol": panel.symbols[j], "signal_date": str(dates[i - 1].date()),
                                 "status": "filled", "target_weight": target,
                                 "volatility": float(vol) if np.isfinite(vol) else None,
                                 "pool_median": float(reference) if np.isfinite(reference) else None, "pool_count": reference_count,
                                 "opening_nav": opening_nav, "budget": budget})
            ratio = v["adjusted_open"][i, j] / v["open"][i, j]
            qty = shares / ratio
            execution = v["adjusted_open"][i, j] * (1 + slip)
            notional = qty * execution
            charges = max(cfg["minimum_commission"], notional * cfg["commission_bps"] / 10000) + notional * cfg["transfer_bps"] / 10000
            cost_slip = qty * v["adjusted_open"][i, j] * slip
            cash -= notional + charges
            units[j] = qty
            entry_day[j], entry_price[j] = i, execution
            entry_episode[j] = consumed_episode[j] = episodes[i - 1, j]
            fee += charges
            slippage_cash += cost_slip
            traded += qty * v["adjusted_open"][i, j]
            entries += 1
            trades.append({"date": str(day.date()), "symbol": panel.symbols[j], "action": "BUY", "signal_date": str(dates[i - 1].date()),
                           "reason": variant, "analytical_units": qty, "entry_raw_shares_reference": float(shares), "analytical_execution_price": execution,
                           "charges": charges, "slippage": cost_slip, "held_sessions": 0, "cash_after": cash})
            trades[-1]["signal_episode"] = int(entry_episode[j])
        present = np.isfinite(v["adjusted_close"][i])
        previous_mark[present] = v["adjusted_close"][i, present]
        previous_raw_ratio[present] = v["adjusted_close"][i, present] / v["close"][i, present]
        held = np.flatnonzero(units > 1e-10)
        value = float(np.dot(units[held], previous_mark[held])) if len(held) else 0.
        nav = cash + value
        if cash < -1e-6 or not np.isfinite(nav) or nav <= 0 or len(held) > config["max_positions"]:
            raise ValueError("Portfolio insolvency/position breach")
        for j in held:
            if j in pending or not np.isfinite(v["adjusted_close"][i, j]):
                continue
            holding = i - entry_day[j] + 1
            stop = v["adjusted_close"][i, j] / entry_price[j] - 1 <= -signal_rule["stop_loss"]
            normal = sig["reversion_exit" if variant == "deviation_reversal" else "trend_exit"][i, j]
            reason = "close_stop" if stop else "time_exit" if exit_policy == "time_7" and holding >= signal_rule["maximum_hold_sessions"] else "signal_exit" if normal and holding >= signal_rule["minimum_hold_sessions"] else ""
            if reason:
                pending[j] = (reason, day)
        daily.append({"date": day, "portfolio_net_return": nav / previous_nav - 1, "portfolio_equity": nav / config["initial_capital"],
                      "nav": nav, "cash": cash, "exposure": value / nav, "quantity": float(units.sum()), "position_count": len(held),
                      "commission": fee, "slippage": slippage_cash, "dividend_tax": tax, "transaction_cost": (fee + slippage_cash + tax) / previous_nav,
                      "turnover": traded / (2 * previous_nav), "entry_fill": entries, "exit_fill": exits, "entry_signal": int(candidate.sum()),
                      "blocked_entry": blocked, "delayed_exit": delayed, "stale_position_marks": stale, "receivable": 0.,
                      "same_episode_candidates": int(repeated.sum()),
                      "suppressed_same_episode_candidates": int(repeated.sum()) if entry_policy == "once_per_episode" else 0,
                      "maximum_holding_sessions": max((i - entry_day[j] + 1 for j in held), default=0), "pending_exits": len(pending)})
        # Preserve all historical columns and the baseline trade schema.
        daily[-1].update(invalid_sizing_candidates=invalid_sizing, cash_fraction=cash / nav)
        previous_nav = nav
    frame = pd.DataFrame(daily).set_index("date")
    summary = summarize(frame)
    summary.update({"maximum_positions": int(frame.position_count.max()), "mean_positions": float(frame.position_count.mean()),
                    "stale_position_marks": int(frame.stale_position_marks.sum()), "dividend_tax": float(frame.dividend_tax.sum()),
                    "maximum_holding_sessions": int(frame.maximum_holding_sessions.max()), "pending_exits_at_end": int(frame.pending_exits.iloc[-1]),
                    "quantity_unit": "analytical_total_return_units_not_broker_shares"})
    summary.update(sizing_policy=sizing_policy, sizing_audit=sizing_audit,
                   average_cash_fraction=float(frame.cash_fraction.mean()), invalid_sizing_candidates=int(frame.invalid_sizing_candidates.sum()))
    return frame, pd.DataFrame(trades, columns=["date", "symbol", "action", "signal_date", "reason", "analytical_units", "entry_raw_shares_reference", "analytical_execution_price", "charges", "slippage", "held_sessions", "cash_after", "signal_episode"]), summary
