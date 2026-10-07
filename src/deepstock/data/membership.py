"""Index membership coverage is not inferred from an absence of intervals."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .store import DataQualityError


def audit_us_membership(calendar, membership, evaluation_start, evaluation_end):
    """A necessary presence check, not proof of full point-in-time completeness.

    A positive interval ending at a capture cutoff is censored. Outside that
    capture it must not silently become a known non-member observation.
    Include the prior close needed to trade the first evaluation open.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(calendar))
    if dates.empty or dates.has_duplicates or dates.tz is not None or not dates.is_monotonic_increasing:
        raise DataQualityError("Unique ordered membership session calendar required")
    required = {"date", "end", "symbol"}
    if required.difference(membership) or membership.empty:
        raise DataQualityError("Historical membership intervals required")
    first = pd.Timestamp(evaluation_start)
    last = pd.Timestamp(evaluation_end)
    sessions = dates[(dates >= first) & (dates <= last)]
    if sessions.empty or first > last:
        raise DataQualityError("Evaluation membership sessions required")
    first_index = dates.get_indexer([sessions[0]])[0]
    signal_dates = dates[max(0, first_index - 1):dates.get_indexer([sessions[-1]])[0] + 1]
    events = np.zeros(len(dates) + 1, dtype=np.int64)
    starts = pd.to_datetime(membership.date, errors="coerce")
    ends = pd.to_datetime(membership.end, errors="coerce")
    if starts.isna().any() or ends.isna().any() or starts.gt(ends).any() or membership.symbol.isna().any() or membership.symbol.astype(str).str.strip().eq("").any():
        raise DataQualityError("Invalid membership interval dates")
    # Count unique symbols, not duplicate/overlapping rows. A symbol can enter
    # the index more than once, but overlapping intervals are not new evidence.
    for _, group in membership.assign(_start=starts, _end=ends).groupby("symbol"):
        ranges = sorted((int(dates.searchsorted(start, side="left")),
                         int(dates.searchsorted(end, side="right")))
                        for start, end in zip(group["_start"], group["_end"]))
        merged = []
        for left, right in ranges:
            if left >= right:
                continue
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        for left, right in merged:
            events[left] += 1
            events[right] -= 1
    counts = pd.Series(np.cumsum(events[:-1]), index=dates)
    empty = signal_dates[counts.loc[signal_dates].eq(0)]
    evaluation_empty = sessions[counts.loc[sessions].eq(0)]
    return {
        "status": "blocked" if len(empty) else "presence_check_passed_not_complete_universe_proof",
        "evaluation_start": str(sessions[0].date()), "evaluation_end": str(sessions[-1].date()),
        "last_positive_interval_end": str(ends.max().date()),
        "required_close_sessions": len(signal_dates), "zero_member_close_sessions": len(empty),
        "zero_member_evaluation_sessions": len(evaluation_empty),
        "first_zero_member_close": str(empty[0].date()) if len(empty) else None,
        "last_zero_member_close": str(empty[-1].date()) if len(empty) else None,
        "members_at_evaluation_end": int(counts.loc[sessions[-1]]),
        "reason": "Index membership coverage missing; unknown eligibility cannot become an all-cash trading decision" if len(empty) else "Positive membership intervals exist; raw indicator coverage and full universe still require independent verification",
        "policy": "No forward fill, interval extension, fake removals or sample trimming",
    }


def require_us_membership(calendar, membership, evaluation_start, evaluation_end):
    result = audit_us_membership(calendar, membership, evaluation_start, evaluation_end)
    if result["status"] == "blocked":
        raise DataQualityError(f"{result['reason']}: {result['first_zero_member_close']} through {result['last_zero_member_close']}")
    return result
