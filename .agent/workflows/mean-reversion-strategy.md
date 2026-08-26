# SPY Mean-Reversion Research Gate

## Fixed Baseline

This is an independent, long-only `SPY` / `SHY` research baseline. It is not
an ARC route and has no paper or live execution authority.

- Reference window: prior 20 trading sessions of SPY adjusted closes.
- Trend filter: SPY must be above its prior 200-session average.
- Entry: close-time Z-score is at or below `-2.0`; target is 100% SPY for the
  next trading session.
- Exit: Z-score is at or above `0.0`, or the trend filter fails; target is 100%
  SHY for the next trading session.
- Costs: 5 bps per traded weight, no leverage or short positions.

The rolling means and standard deviation are shifted one session before they
are compared with the current close. This makes the information set explicit
and prevents using future data.

## Fixed Validation

- Data: Norgate adjusted ETF history, common complete sample 2002-07-26 through
  2026-08-21.
- IS/OOS split: 2021-08-23 is the first OOS session.
- Walk-Forward reporting: 504 trading-day history, 252-session test, 252-session
  step. No test result may select or change a parameter.

## Result and Decision

The fixed rule returned 15.00% OOS, 2.85% annualized, Sharpe 0.50, and -12.72%
maximum drawdown versus SPY total return of 84.79%. Seven of 22 fixed
Walk-Forward windows were negative, with a worst return of -6.63%.

The baseline does not pass the stable-window research gate and remains
`research_only_no_orders`. Do not tune its thresholds against these results.
Any RSI, Bollinger, multi-ETF, or ARC-range experiment must be separately
predeclared with an independent OOS evaluation.
