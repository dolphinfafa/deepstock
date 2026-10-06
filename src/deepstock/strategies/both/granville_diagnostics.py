"""Ledger diagnostics and fixed single-change validation, no model selection."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ledger_diagnostics(daily, trades, initial_capital):
    costs = {name: float(daily[name].sum()) for name in ["commission", "slippage", "dividend_tax"]}
    buys = trades.loc[trades.action.eq("BUY")]
    sells = trades.loc[trades.action.eq("SELL")]
    costs_cash = sum(costs.values())
    net_return = float(daily.nav.iloc[-1] / initial_capital - 1)
    # This is a cash identity at the actual fills, NOT a zero-cost strategy,
    # reinvested gross CAGR, or an assumption that stress keeps the same trades.
    cash_identity = {"net_return": net_return, "direct_cost_cash": costs_cash,
                     "direct_cost_initial_capital_ratio": costs_cash / initial_capital,
                     "gross_market_pnl_initial_capital_ratio": net_return + costs_cash / initial_capital}
    completed = []
    open_positions = {}
    for row in trades.itertuples():
        if row.action == "BUY":
            if row.symbol in open_positions:
                raise ValueError("Unexpected pyramiding in diagnostic ledger")
            open_positions[row.symbol] = {"units": row.analytical_units, "price": row.analytical_execution_price,
                                          "entry_charges_per_unit": row.charges / row.analytical_units,
                                          "pnl": 0., "reason": row.reason}
        else:
            entry = open_positions[row.symbol]
            entry["pnl"] += row.analytical_units * (row.analytical_execution_price - entry["price"] - entry["entry_charges_per_unit"]) - row.charges
            entry["units"] -= row.analytical_units
            if entry["units"] <= 1e-10:
                completed.append({"symbol": row.symbol, "reason": row.reason, "held_sessions": row.held_sessions,
                                  "net_realized_pnl_excluding_dividend_tax": entry["pnl"]})
                del open_positions[row.symbol]
    closed = pd.DataFrame(completed)
    by_exit = {}
    if len(closed):
        for reason, group in closed.groupby("reason"):
            pnl = group.net_realized_pnl_excluding_dividend_tax
            by_exit[reason] = {"closed_positions": len(group), "mean_holding_sessions": float(group.held_sessions.mean()),
                               "win_fraction_excluding_dividend_tax": float(pnl.gt(0).mean()),
                               "net_realized_pnl_excluding_dividend_tax": float(pnl.sum())}
    repeat_episode_buys = int(buys.duplicated(["symbol", "signal_episode"]).sum()) if "signal_episode" in buys else None
    return {"buy_fills": len(buys), "sell_fills_including_partial": len(sells),
            "unique_bought_symbols": int(buys.symbol.nunique()), "same_episode_repeat_buys": repeat_episode_buys,
            "closed_positions": len(completed), "open_positions_at_end": len(open_positions),
            "exit_reasons": by_exit, "cost_cash_components": costs, "cash_identity": cash_identity,
            "holding_median_sessions": float(closed.held_sessions.median()) if len(closed) else None,
            "holding_mean_sessions": float(closed.held_sessions.mean()) if len(closed) else None,
            "suppressed_same_episode_candidate_sessions": int(daily.suppressed_same_episode_candidates.sum()),
            "realized_pnl_scope": "Completed positions only; execution slippage and charges included, dividend tax not allocated per stock; open positions not forced liquidated."}


def cost_path_attribution(base, stress):
    b, s = base["cash_identity"], stress["cash_identity"]
    delta_net = s["net_return"] - b["net_return"]
    delta_cost = s["direct_cost_initial_capital_ratio"] - b["direct_cost_initial_capital_ratio"]
    delta_gross = s["gross_market_pnl_initial_capital_ratio"] - b["gross_market_pnl_initial_capital_ratio"]
    if not np.isclose(delta_net, delta_gross - delta_cost, atol=1e-10):
        raise ValueError("Cost/path cash identity failed")
    return {"stress_minus_base_net_return": delta_net,
            "additional_direct_cost_initial_capital_ratio": delta_cost,
            "changed_position_path_gross_pnl_initial_capital_ratio": delta_gross,
            "interpretation": "Cash identity at actual fills: delta net = delta marked gross market PnL - delta direct costs. Different sizing/fill/stop/replacement paths are retained; not a zero-cost/reinvestment counterfactual."}


def validate_optimization(value):
    cfg = value["experiment"]
    if (cfg["strategy_id"] != "granville_stock_portfolio" or cfg["experiment_id"] != "granville-entry-episodes-v2-20261006"
        or cfg["variant"] != "trend_pullback" or cfg["exit_policy"] != "time_7"
        or cfg["entry_policies"] != ["signal_level", "once_per_episode"] or cfg["cost_cases"] != ["base", "stress"]
        or cfg["principal_entry_policy"] != "signal_level" or cfg["paper_authorized"] or cfg["live_authorized"]
        or set(value["market_results"]) != {"US", "CN"}):
        raise ValueError("Fixed single-change experiment boundary changed")
    for result in value["market_results"].values():
        opt = result["optimization"]
        cases = {(c["entry_policy"], c["cost_case"]): c for c in opt["cases"]}
        if set(cases) != {(p, c) for p in cfg["entry_policies"] for c in cfg["cost_cases"]} or len(opt["cases"]) != 4:
            raise ValueError("All fixed optimization cases must be retained")
        for key, case in cases.items():
            if case["status"] not in {"completed", "blocked"}:
                raise ValueError("Invalid optimization case status")
            if case["status"] == "blocked":
                if not case.get("blocking_reason") or case["periods"]:
                    raise ValueError("Blocked optimization case cannot fabricate performance")
            elif key[0] == "signal_level":
                if not case.get("baseline_verified"):
                    raise ValueError("Baseline replay must be independently verified")
                original = next(c for c in result["cases"] if (c["variant"], c["exit_policy"], c["cost_case"]) == (cfg["variant"], cfg["exit_policy"], key[1]))
                for name in ["total_return", "annualized_return", "maximum_drawdown", "annualized_turnover"]:
                    if not np.isclose(case["periods"]["full"][name], original["periods"]["full"][name], rtol=1e-10, atol=1e-10):
                        raise ValueError("Replayed baseline differs from original metrics")
        baseline = cases[("signal_level", "base")]
        if baseline["status"] != "completed" or result["metrics"] != baseline["periods"].get("full"):
            raise ValueError("Do not replace baseline display with an optimization winner")
