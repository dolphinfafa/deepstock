"""Compatibility exports for the US ARC controller; no A-share adoption."""
from .strategies.us.regime import (
    ARC_EXECUTION_STATUS, ADXARCConfig, ARCConfig, MarketRegime, StrategyRoute,
    apply_regime_hysteresis, calculate_adx, classify_adx_market_regime,
    classify_market_regime, regime_statistics,
)
