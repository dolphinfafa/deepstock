"""Compatibility exports; implementation is in strategies.us.backtest."""
from .strategies.us.backtest import (
    TRADING_DAYS_PER_YEAR, BacktestResult, SegmentedBacktestResult, StrategyConfig,
    run_backtest, run_segmented_backtest, validate_prices,
)
