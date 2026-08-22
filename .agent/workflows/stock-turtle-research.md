# Stock Turtle Research Gate

## Current Baseline

The point-in-time stock universe uses Norgate's `S&P 500 Current & Past`
watchlist, historical constituent intervals, and total-return close prices.
The downloaded input contains 1,301 symbols in 14 chunks; 261 symbols have
no usable price history and remain listed in the manifest failure section.
Raw chunks are local, Git-ignored licensed research data.

The corrected engine keeps existing positions until their exit condition and
uses historical membership only for new entries. Under the fixed split at
2021-08-23, six predeclared combinations (20/55 entry, 10/20 exit, and 3/5
positions) all underperformed SPY. The in-sample-ranked 20/10, three-position
configuration returned 12.83% out of sample, Sharpe 0.23, maximum drawdown
-23.28%, and turnover 43.26, versus SPY at 84.79%.

With the predeclared 20-session average-turnover filter of USD 10 million, the
in-sample-ranked configuration changed to 55/20 with five positions. It
returned 60.59% out of sample, annualized 9.98%, Sharpe 0.60, and maximum
drawdown -23.48%, versus SPY's 84.79% over the same dates. The OOS-best 55/20
three-position result (110.34%) is not a valid selection because it was not
chosen in sample; this is precisely why the fixed selection policy is retained.

The earlier run that returned approximately -97% used a position-selection
bug and is invalid. It must not be used for comparison or tuning.

## Required Data Gates

The refreshed price export now contains daily `volume` and `turnover` in
addition to adjusted close: 14 chunks, 4,108,688 rows, and 1,301 requested
symbols. The manifest records 261 symbols without usable history. Norgate's
Python API exposes current `classification()` values but no historical sector
classification time series. Therefore point-in-time sector caps cannot yet be
evaluated and current classifications must not be backfilled into history.

Liquidity fields are now available for a fixed ADV control, but no threshold
has been selected or tested yet. The next experiment must predeclare the
threshold and validation windows before inspecting results.

The engine treats a missing risk price as an exit and moves the target to the
safe asset on the next session. This is an explicit zero-slippage liquidation
assumption at the last available price; it is optimistic for delisted names.
Before any strategy comparison, add a delisting-return policy and report the
assumption separately from normal backtest returns.

## Safety Decision

Stock Turtle is research-only. No Paper or live order may be enabled from this
baseline. The next permitted experiment is a single fixed-rule robustness run
after obtaining point-in-time volume and sector data, with predeclared
liquidity thresholds, delisting treatment, transaction costs, and validation
windows. Parameter selection remains fixed to the existing six combinations.
