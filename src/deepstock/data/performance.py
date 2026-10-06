from __future__ import annotations
import numpy as np
import pandas as pd


def annualize_net_returns(returns: pd.Series, scope: str, cost_basis: str) -> dict:
    values = returns.to_numpy(dtype=float)
    if not len(values) or not np.isfinite(values).all() or (values < -1).any():
        raise ValueError("Annualization needs finite continuous net returns")
    if not isinstance(returns.index, pd.DatetimeIndex) or not returns.index.is_monotonic_increasing or returns.index.has_duplicates:
        raise ValueError("Annualization needs unique ordered session dates")
    growth = float(np.prod(1 + values))
    return {"value": growth ** (252 / len(values)) - 1, "scope": scope, "sessions": len(values),
            "data_start": returns.index[0].date().isoformat(), "data_end": returns.index[-1].date().isoformat(),
            "short_sample": len(values) < 252, "source": "continuous_net_returns", "cost_basis": cost_basis,
            "reason": "短样本参考年化，不作为准入依据" if len(values) < 252 else "成本后复合年化，包含空仓交易日"}
