"""Read-only attribution of retained US fills; never generate strategy orders."""
from __future__ import annotations

import numpy as np
import pandas as pd

from deepstock.strategies.both.granville import summarize
from deepstock.strategies.both.granville_diagnostics import ledger_diagnostics

CENT = .01
EXIT_REASONS = ("time_exit", "signal_exit", "close_stop")


def money_equal(actual, expected, label):
    if not np.isfinite(actual) or not np.isfinite(expected) or abs(actual - expected) > CENT:
        raise ValueError(f"{label} reconciliation exceeds USD0.01")


def verify_metrics(daily, expected):
    observed = summarize(daily)
    if set(observed) != set(expected):
        raise ValueError("Summary metric schema differs")
    for key, actual in observed.items():
        wanted = expected[key]
        if isinstance(actual, float) and wanted is not None:
            if not np.isclose(actual, wanted, rtol=0, atol=1e-9):
                raise ValueError("Summary metric differs: " + key)
        elif actual != wanted:
            raise ValueError("Summary metric differs: " + key)


def exit_summary(cycles):
    result = {}
    for reason in EXIT_REASONS:
        group = [c for c in cycles if c["exit_reason"] == reason]
        count = len(group)
        result[reason] = {
            "closed_positions": count,
            "mean_holding_sessions": float(np.mean([c["held_sessions"] for c in group])) if count else None,
            "median_holding_sessions": float(np.median([c["held_sessions"] for c in group])) if count else None,
            "gross_win_fraction": sum(c["gross_realized_pnl"] > 0 for c in group) / count if count else None,
            "net_win_fraction": sum(c["net_realized_pnl"] > 0 for c in group) / count if count else None,
            "gross_realized_pnl": sum(c["gross_realized_pnl"] for c in group),
            "net_realized_pnl": sum(c["net_realized_pnl"] for c in group),
            "commission": sum(c["commission"] for c in group),
            "slippage": sum(c["slippage"] for c in group),
            "gross_profit_to_nonpositive_net_count": sum(c["gross_realized_pnl"] > 0 and c["net_realized_pnl"] <= 0 for c in group),
        }
    return result


def audit_ledger(daily, trades, initial_capital, *, marks=None, cost_rule=None):
    """Allocate buy costs by actual sold units, close a cycle once, check cash/NAV.

    Marks are retained adjusted closes, aligned without padding. In the native
    entry point they are mandatory; synthetic callers may test cash alone.
    """
    daily = daily.copy()
    daily.index = pd.DatetimeIndex(pd.to_datetime(daily.index))
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades.date)
    if (daily.empty or daily.index.has_duplicates or not daily.index.is_monotonic_increasing or
            trades.date.isna().any() or not trades.date.is_monotonic_increasing or
            not trades.date.isin(daily.index).all()):
        raise ValueError("Ledger dates/calendar invalid")
    numeric = ["analytical_units", "analytical_execution_price", "charges", "slippage", "cash_after", "held_sessions"]
    if not np.isfinite(trades[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite trade fields")
    if (trades.analytical_units.le(0).any() or trades.analytical_execution_price.le(0).any() or
            trades.charges.lt(0).any() or trades.slippage.lt(0).any() or
            not trades.action.isin(["BUY", "SELL"]).all()):
        raise ValueError("Invalid trade action/quantity/price/cost")
    # US economic total-return units have no separately allocated dividend tax.
    if not daily.dividend_tax.eq(0).all() or not daily.receivable.eq(0).all():
        raise ValueError("Unexpected US tax/receivable; no invented allocation")
    required = ["nav", "cash", "quantity", "position_count", "commission", "slippage", "portfolio_net_return", "transaction_cost"]
    if not np.isfinite(daily[required].to_numpy(dtype=float)).all() or daily.nav.le(0).any():
        raise ValueError("Invalid daily account values")
    grouped = {day: group for day, group in trades.groupby("date", sort=False)}
    cash = previous_nav = float(initial_capital)
    positions, completed = {}, []
    realized = 0.
    for day, account in daily.iterrows():
        fee = slip = buys = sells = 0
        for row in grouped.get(day, trades.iloc[:0]).itertuples(index=False):
            qty, price = row.analytical_units, row.analytical_execution_price
            notional = qty * price
            is_buy = row.action == "BUY"
            reference_notional = notional - row.slippage if is_buy else notional + row.slippage
            if reference_notional <= 0:
                raise ValueError("Nonpositive pre-slippage notional")
            if cost_rule is not None:
                money_equal(row.slippage, reference_notional * cost_rule["slippage_bps"] / 10000, "Slippage contract")
                money_equal(row.charges, max(cost_rule["minimum_commission"], notional * cost_rule["commission_bps"] / 10000), "Commission contract")
            fee += row.charges
            slip += row.slippage
            if is_buy:
                if row.symbol in positions:
                    raise ValueError("Unexpected duplicate entry/pyramiding")
                positions[row.symbol] = {
                    "symbol": row.symbol, "entry_date": str(day.date()), "entry_index": daily.index.get_loc(day),
                    "entry_units": qty, "remaining_units": qty, "reference_entry_price": reference_notional / qty,
                    "entry_commission": row.charges, "entry_slippage": row.slippage,
                    "commission": row.charges, "slippage": row.slippage,
                    "gross_realized_pnl": 0., "net_realized_pnl": 0., "sell_fills": 0,
                }
                cash -= notional + row.charges
                buys += 1
            else:
                if row.symbol not in positions or row.reason not in EXIT_REASONS:
                    raise ValueError("Sell without position or unknown exit reason")
                cycle = positions[row.symbol]
                if qty > cycle["remaining_units"] + 1e-10:
                    raise ValueError("Sell exceeds remaining units")
                fraction = qty / cycle["entry_units"]
                gross = reference_notional - qty * cycle["reference_entry_price"]
                charges = fraction * (cycle["entry_commission"] + cycle["entry_slippage"]) + row.charges + row.slippage
                cycle["gross_realized_pnl"] += gross
                cycle["net_realized_pnl"] += gross - charges
                cycle["commission"] += row.charges
                cycle["slippage"] += row.slippage
                cycle["remaining_units"] -= qty
                cycle["sell_fills"] += 1
                realized += gross - charges
                held = daily.index.get_loc(day) - cycle["entry_index"]
                if row.held_sessions != held:
                    raise ValueError("Holding sessions differ from actual calendar")
                cash += notional - row.charges
                sells += 1
                if cycle["remaining_units"] <= 1e-10:
                    cycle.update(exit_date=str(day.date()), exit_reason=row.reason, held_sessions=int(held))
                    money_equal(cycle["net_realized_pnl"], cycle["gross_realized_pnl"] - cycle["commission"] - cycle["slippage"], "Cycle PnL")
                    completed.append(cycle)
                    del positions[row.symbol]
            money_equal(cash, row.cash_after, "Fill cash")
        money_equal(fee, account.commission, "Daily commission")
        money_equal(slip, account.slippage, "Daily slippage")
        money_equal(cash, account.cash, "Daily cash")
        if not np.isclose(sum(p["remaining_units"] for p in positions.values()), account.quantity, rtol=0, atol=1e-8):
            raise ValueError("Daily quantity differs")
        if account.position_count != len(positions) or account.entry_fill != buys or account.exit_fill != sells:
            raise ValueError("Daily positions/fill counts differ")
        money_equal(account.nav, previous_nav * (1 + account.portfolio_net_return), "NAV/return")
        money_equal(account.transaction_cost * previous_nav, fee + slip, "Daily cost ratio")
        if marks is not None:
            value = 0.
            for symbol, cycle in positions.items():
                mark = marks.loc[day, symbol]
                if not np.isfinite(mark) or mark <= 0:
                    raise ValueError("Held mark missing; no valuation padding")
                value += cycle["remaining_units"] * mark
            money_equal(account.nav, cash + value, "Marked NAV")
        previous_nav = account.nav
    legacy = ledger_diagnostics(daily, trades, initial_capital)
    open_cycles = list(positions.values())
    for cycle in open_cycles:
        fraction = cycle["remaining_units"] / cycle["entry_units"]
        cycle["unallocated_entry_commission"] = fraction * cycle["entry_commission"]
        cycle["unallocated_entry_slippage"] = fraction * cycle["entry_slippage"]
        if marks is not None:
            cycle["gross_unrealized_pnl"] = cycle["remaining_units"] * (marks.loc[daily.index[-1], cycle["symbol"]] - cycle["reference_entry_price"])
    for name in ["commission", "slippage"]:
        money_equal(sum(c[name] for c in completed + open_cycles), daily[name].sum(), "Exit/open fee conservation")
    if marks is not None:
        gross = sum(c["gross_realized_pnl"] for c in completed + open_cycles) + sum(c["gross_unrealized_pnl"] for c in open_cycles)
        money_equal(gross - daily.commission.sum() - daily.slippage.sum(), daily.nav.iloc[-1] - initial_capital, "Realized/unrealized account PnL")
    segments = {}
    for name, part in [("full", daily), ("2025", daily.loc[daily.index.year == 2025]), ("2026_ytd", daily.loc[daily.index.year == 2026])]:
        if part.empty:
            continue
        start, end = part.index[0], part.index[-1]
        prior = initial_capital if start == daily.index[0] else daily.nav.iloc[daily.index.get_loc(start) - 1]
        closed = [c for c in completed if str(start.date()) <= c["exit_date"] <= str(end.date())]
        segments[name] = {"account": summarize(part), "opening_nav": float(prior), "closing_nav": float(part.nav.iloc[-1]),
                          "account_pnl": float(part.nav.iloc[-1] - prior),
                          "fees_paid": {k: float(part[k].sum()) for k in ["commission", "slippage", "dividend_tax"]},
                          "closed_positions": len(closed), "exit_reasons": exit_summary(closed)}
    aggregate = {"periods": segments, "cash_identity": legacy["cash_identity"],
                 "buy_fills": legacy["buy_fills"], "sell_fills_including_partial": legacy["sell_fills_including_partial"],
                 "same_episode_repeat_buys": legacy["same_episode_repeat_buys"],
                 "closed_positions": len(completed), "open_positions_at_end": len(open_cycles),
                 "all_sells_net_realized_pnl": realized,
                 "open_positions": {"count": len(open_cycles), "sell_fills": sum(c["sell_fills"] for c in open_cycles),
                                    **{k: sum(c.get(k, 0.) for c in open_cycles) for k in ["commission", "slippage", "gross_realized_pnl", "net_realized_pnl", "unallocated_entry_commission", "unallocated_entry_slippage", "gross_unrealized_pnl"]}},
                 "scope": "Completed cycles grouped by final exit date/reason; entire entry and exit costs allocated once. Period fees are cash-date costs. Open cycles are separate, not forced closed. Realized PnL is not account return.",
                 "verification": {"cash_tolerance_usd": CENT, "marks_verified": marks is not None}}
    return aggregate, completed + open_cycles
