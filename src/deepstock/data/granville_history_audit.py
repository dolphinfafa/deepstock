"""Coverage and proposed windows only: no returns, price filling or admission."""
from __future__ import annotations

import numpy as np
import pandas as pd

GAP_KINDS = ("member_missing", "pre_member_warmup_missing", "other_internal_missing")


def planned_windows(calendar):
    calendar = pd.DatetimeIndex(calendar)
    if calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Unique ordered history calendar required")
    def scope(part):
        return {"start": str(part[0].date()) if len(part) else None,
                "end": str(part[-1].date()) if len(part) else None, "sessions": len(part)}
    result = {"warmup": scope(calendar[:252]), "warmup_sessions": 252,
              "history_sessions": 504, "test_sessions": 252, "step_sessions": 252,
              "windows": [], "tail": None, "strategy_run": False}
    for first in range(252 + 504, len(calendar), 252):
        test = calendar[first:first + 252]
        record = {"history": scope(calendar[first - 504:first]), "test": scope(test)}
        if len(test) < 252:
            result["tail"] = record
        else:
            result["windows"].append(record)
    if len(calendar) < 252 + 504 + 252:
        result["status"] = "insufficient_for_complete_window"
    else:
        result["status"] = "coverage_plan_only"
    return result


def security_coverage(calendar, metadata, members, price_dates):
    """Partition gaps within verified quote life, never infer a missing member 0."""
    dates = pd.DatetimeIndex(calendar)
    first, last = metadata.get("first_quote"), metadata.get("last_quote")
    if not first or (last and pd.Timestamp(last) < pd.Timestamp(first)):
        raise ValueError("Unverified/invalid quote lifetime")
    alive = (dates >= pd.Timestamp(first)) & (dates <= pd.Timestamp(last) if last else True)
    expected = dates[alive]
    observed = pd.DatetimeIndex(pd.to_datetime(members.date)) if members is not None else pd.DatetimeIndex([])
    if observed.has_duplicates or not observed.is_monotonic_increasing or len(observed.difference(dates)):
        raise ValueError("Invalid effective member dates")
    if members is not None and not members.weight.isin([0, 1]).all():
        raise ValueError("Unknown/nonbinary observed effective member values")
    unknown = expected.difference(observed)
    active_dates = observed[members.weight.to_numpy() == 1] if members is not None else observed
    if len(active_dates.difference(expected)):
        raise ValueError("Positive member dates outside verified quote life")
    active = dates.isin(active_dates)
    prices = pd.DatetimeIndex(pd.to_datetime(price_dates))
    if prices.has_duplicates or not prices.is_monotonic_increasing or len(prices.difference(expected)):
        raise ValueError("Quote dates outside verified lifetime/calendar")
    missing = alive & ~dates.isin(prices)
    # Each newly active spell needs 252 preceding observed sessions. Warmup
    # gaps outside verified life are counted separately, never fabricated.
    warming = np.zeros(len(dates), dtype=bool)
    starts = np.flatnonzero(active & ~np.r_[False, active[:-1]])
    for start in starts:
        warming[max(0, start - 252):start] = True
    warming &= ~active
    masks = {"member_missing": missing & active,
             "pre_member_warmup_missing": missing & warming,
             "other_internal_missing": missing & ~active & ~warming,
             "unknown_membership": dates.isin(unknown)}
    row = {"symbol": metadata["symbol"], "assetid": metadata["assetid"], "first_quote": first, "last_quote": last,
           "quote_lifetime_outside_scope": not bool(alive.any()),
           "outside_quote_lifetime_sessions": int((~alive).sum()),
           "pre_member_warmup_outside_lifetime_sessions": int((warming & ~alive).sum()),
           "effective_member_sessions": int(active.sum()), "observed_price_sessions": len(prices),
           "missing_price_sessions": int(missing.sum()),
           **{name: int(mask.sum()) for name, mask in masks.items()}}
    return row, masks, active


def attach_window_coverage(plan, calendar, counters):
    dates = pd.DatetimeIndex(calendar)
    for record in [*plan["windows"], *([plan["tail"]] if plan["tail"] else [])]:
        for label in ["history", "test"]:
            scope = record[label]
            chosen = (dates >= scope["start"]) & (dates <= scope["end"])
            scope["coverage"] = {name: int(values[chosen].sum()) for name, values in counters.items()}
        record["status"] = "research_input_blocked" if any(
            record[label]["coverage"].get(name, 0) for label in ["history", "test"]
            for name in ["member_missing", "unknown_membership", "verification_failures"]
        ) else "coverage_checked_execution_evidence_pending"
        record["strategy_run"] = False
        record["terminal_holdings_verified"] = False
    return plan
