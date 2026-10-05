"""Compatibility exports for the A-share-only afternoon-momentum candidate."""
from .strategies.cn.tail_momentum import (
    MARKET, PATHS, STRATEGY_ID, VERSION, TailMomentumConfig, TailMomentumResult,
    cost_cases, prepare_sessions, run_tail_momentum, summarize, walk_forward,
)
