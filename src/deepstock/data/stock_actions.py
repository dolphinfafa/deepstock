"""Audit disclosed CN entitlements against independently captured factor steps.

No inferred prices or amounts: totals are sums of supplied disclosures only.
This resolves repeated/aggregate fiscal-period records without double counting.
"""
from itertools import combinations
import numpy as np
import pandas as pd


def reconcile_stock_actions(bars, declarations, first, last, *, allow_unresolved_entitlements=False):
    active = declarations.loc[declarations.div_proc.eq("实施")].copy()
    for name in ["ann_date", "ex_date"]:
        active[name] = pd.to_datetime(active[name], errors="coerce")
    if "imp_ann_date" in active:
        # Supplied implementation disclosure is an alternative dated source,
        # not a guessed repair of the missing proposal-announcement field.
        imp = pd.to_datetime(active.imp_ann_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
        active["ann_date"] = imp.fillna(active.ann_date)
    if "end_date" in active:
        # Same fiscal entitlement and same implementation disclosure can have
        # multiple proposal records. Distinct fiscal periods are NOT collapsed.
        keys = [c for c in ["ts_code", "end_date", "ann_date", "ex_date", "record_date", "pay_date", "cash_div_tax", "stk_div"] if c in active]
        active = active.drop_duplicates(keys)
    if (active.ex_date.isna() & active.ann_date.between(first, last)).any():
        raise ValueError("Implemented recent dividend lacks ex-date")
    active = active.loc[active.ex_date.between(first, last)]
    events, audits = [], []
    def retain_unknown(symbol, day, group, reason):
        events.append({"ts_code": symbol, "ann_date": group.ann_date.max(), "ex_date": day, "div_proc": "实施", "cash_div_tax": np.nan, "stk_div": np.nan})
        audits.append({"symbol": symbol, "ex_date": str(day.date()), "disclosure_count": len(group),
                       "verified_cash_per_share": None, "verified_stock_per_share": None,
                       "unspecified_cash_entitlement_blocks_held_strategy": True,
                       "unresolved_reason": reason, "policy": "retain_unknown_not_zero_any_held_case_must_block"})
    for (symbol, day), group in active.groupby(["ts_code", "ex_date"]):
        if group.ann_date.isna().any() or group.ann_date.gt(day).any():
            raise ValueError(f"Ambiguous dividend announcement: {symbol} {day.date()}")
        supplied = group[["cash_div_tax", "stk_div"]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        unknown_cash = bool(len(supplied) == 1 and np.isnan(supplied[0, 0]) and np.isfinite(supplied[0, 1]) and supplied[0, 1] > 0)
        if (not np.isfinite(supplied).all() and not unknown_cash) or (supplied < 0).any() or len(supplied) > 12:
            if allow_unresolved_entitlements and not (supplied < 0).any():
                retain_unknown(symbol, day, group, "Missing supplied event economics")
                continue
            raise ValueError(f"Unresolved dividend economics: {symbol} {day.date()}")
        history = bars.loc[bars.symbol.eq(symbol)].sort_values("date")
        before = history.loc[history.date.lt(day)]
        after = history.loc[history.date.ge(day)]
        if before.empty or after.empty:
            # No position can precede the first available quote. Events after
            # the last quote still need terminal/suspension entrance checks.
            if before.empty:
                continue
            raise ValueError(f"Dividend lacks following factor evidence: {symbol} {day.date()}")
        prior, following = before.iloc[-1], after.iloc[0]
        # Multiple separate ex-dates during one suspension need an explicit
        # compound action audit; do not attribute the whole step twice.
        between = active.loc[active.ts_code.eq(symbol) & active.ex_date.gt(prior.date) & active.ex_date.le(following.date)]
        if between.ex_date.nunique() != 1:
            raise ValueError(f"Multiple actions across quote gap: {symbol} {day.date()}")
        reference = prior.close * prior.adj_factor / following.adj_factor
        # Tushare exports rounded factor values (often only three decimals).
        # Bound that input quantization explicitly, rather than treating the
        # rounded ratio as exact and rejecting otherwise verified dividends.
        prior_quantum = 10. ** (-max(3, len(str(prior.adj_factor).partition(".")[2]))) / 2
        next_quantum = 10. ** (-max(3, len(str(following.adj_factor).partition(".")[2]))) / 2
        lower = prior.close * (prior.adj_factor - prior_quantum) / (following.adj_factor + next_quantum)
        upper = prior.close * (prior.adj_factor + prior_quantum) / (following.adj_factor - next_quantum)
        tolerance = max(.01, reference - lower + .01, upper - reference + .01)
        nominal_warning = False
        if "pre_close" in history and following.date == day:
            if abs(following.pre_close - reference) > tolerance:
                raise ValueError(f"Factor disagrees with supplied ex-reference: {symbol} {day.date()}")
            # A single disclosed entitlement is unambiguous even when nominal
            # cash/bonus rights differ slightly from the exchange's diluted
            # ex-reference (treasury shares and source rounding). Flag, do not
            # replace that nominal amount with an inferred dividend. This is
            # an analytical-unit diagnostic, not a broker-share action replay.
            if len(supplied) == 1:
                cash, stock = supplied[0]
                residual = abs((prior.close - (0 if unknown_cash else cash)) / (1 + stock) - reference)
                if residual > tolerance:
                    if residual > .005 * prior.close:
                        if allow_unresolved_entitlements:
                            retain_unknown(symbol, day, group, "Nominal disclosures do not explain ex-reference; no inferred amount")
                            continue
                        raise ValueError(f"Large nominal/ex-reference discrepancy: {symbol} {day.date()}")
                    nominal_warning = True
                    tolerance = residual + 1e-10
        candidates = set()
        if unknown_cash:
            # A reported stock-only action with unspecified cash stays unknown.
            # Validate its stock-factor compatibility but do not fill cash=0;
            # any strategy held through this event must block its tax ledger.
            supplied = supplied.copy()
            supplied[0, 0] = 0
        for count in range(1, len(supplied) + 1):
            for subset in combinations(range(len(supplied)), count):
                cash, stock = supplied[list(subset)].sum(axis=0)
                theoretical = (prior.close - cash) / (1 + stock)
                if abs(theoretical - reference) <= tolerance:
                    candidates.add((round(float(cash), 10), round(float(stock), 10)))
        if len(candidates) != 1:
            if allow_unresolved_entitlements:
                retain_unknown(symbol, day, group, f"Disclosed factor totals not unique: {len(candidates)}")
                continue
            raise ValueError(f"Non-unique/unexplained disclosed factor reconciliation: {symbol} {day.date()} ({len(candidates)} totals)")
        cash, stock = candidates.pop()
        if unknown_cash:
            cash = np.nan
        events.append({"ts_code": symbol, "ann_date": group.ann_date.max(), "ex_date": day, "div_proc": "实施",
                       "cash_div_tax": cash, "stk_div": stock})
        audits.append({"symbol": symbol, "ex_date": str(day.date()), "disclosure_count": len(group),
                       "verified_cash_per_share": None if unknown_cash else cash, "verified_stock_per_share": stock,
                       "factor_reference_price": float(reference), "factor_price_tolerance": float(tolerance),
                       "factor_price_residual": float((prior.close - (0 if unknown_cash else cash)) / (1 + stock) - reference),
                       "nominal_ex_reference_difference": nominal_warning,
                       "unspecified_cash_entitlement_blocks_held_strategy": unknown_cash,
                       "policy": "unique_disclosed_total_corroborated_by_rounded_factor_not_inferred_amount"})
    return pd.DataFrame(events, columns=["ts_code", "ann_date", "ex_date", "div_proc", "cash_div_tax", "stk_div"]), audits
