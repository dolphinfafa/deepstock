"""Audit disclosed CN entitlements against independently captured factor steps.

No inferred prices or amounts: totals are sums of supplied disclosures only.
This resolves repeated/aggregate fiscal-period records without double counting.
"""
from itertools import combinations
import numpy as np
import pandas as pd


def reconcile_stock_actions(bars, declarations, first, last):
    active = declarations.loc[declarations.div_proc.eq("实施")].copy()
    for name in ["ann_date", "ex_date"]:
        active[name] = pd.to_datetime(active[name], errors="coerce")
    if "imp_ann_date" in active:
        # Supplied implementation disclosure is an alternative dated source,
        # not a guessed repair of the missing proposal-announcement field.
        imp = pd.to_datetime(active.imp_ann_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
        active["ann_date"] = imp.fillna(active.ann_date)
    if (active.ex_date.isna() & active.ann_date.between(first, last)).any():
        raise ValueError("Implemented recent dividend lacks ex-date")
    active = active.loc[active.ex_date.between(first, last)]
    events, audits = [], []
    for (symbol, day), group in active.groupby(["ts_code", "ex_date"]):
        if group.ann_date.isna().any() or group.ann_date.gt(day).any():
            raise ValueError(f"Ambiguous dividend announcement: {symbol} {day.date()}")
        supplied = group[["cash_div_tax", "stk_div"]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(supplied).all() or (supplied < 0).any() or len(supplied) > 12:
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
        candidates = set()
        for count in range(1, len(supplied) + 1):
            for subset in combinations(range(len(supplied)), count):
                cash, stock = supplied[list(subset)].sum(axis=0)
                theoretical = (prior.close - cash) / (1 + stock)
                if abs(theoretical - reference) <= max(.006, prior.close * 2e-6):
                    candidates.add((round(float(cash), 10), round(float(stock), 10)))
        if len(candidates) != 1:
            raise ValueError(f"Non-unique/unexplained disclosed factor reconciliation: {symbol} {day.date()} ({len(candidates)} totals)")
        cash, stock = candidates.pop()
        events.append({"ts_code": symbol, "ann_date": group.ann_date.max(), "ex_date": day, "div_proc": "实施",
                       "cash_div_tax": cash, "stk_div": stock})
        audits.append({"symbol": symbol, "ex_date": str(day.date()), "disclosure_count": len(group),
                       "verified_cash_per_share": cash, "verified_stock_per_share": stock,
                       "factor_reference_price": float(reference), "policy": "unique_disclosed_total_corroborated_by_factor_not_inferred_amount"})
    return pd.DataFrame(events, columns=["ts_code", "ann_date", "ex_date", "div_proc", "cash_div_tax", "stk_div"]), audits
