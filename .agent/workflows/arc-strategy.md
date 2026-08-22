# Deepstock ARC

Deepstock ARC means **Adaptive Regime Controller**. It is the research
controller for routing between multiple strategy modules; it is not an order
engine and has no broker-side permissions.

## Fixed Regime Rules

The controller uses daily adjusted-close data and emits a close-time signal.
Consumers must apply the route from the next trading session.

- `crisis`: SPY is at or below its 200-day average and 20-day annualized
  volatility is at least 2.0 times its 252-day baseline.
- `defensive`: SPY is at or below its 200-day average, or the volatility ratio
  is at least 1.5.
- `bull`: SPY is above both its 50-day and 200-day averages and at least 50%
  of the configured risk assets are above their 200-day averages.
- `range`: all other valid observations, including the warm-up period.

The order of evaluation is crisis, defensive, bull, then range. Thresholds are
fixed for the initial research phase; no state-specific threshold grid is
authorized.

## Strategy Routes

| Regime | Route | Status |
| --- | --- | --- |
| `crisis` | Defensive ETF, crisis exposure | Existing research module |
| `defensive` | Defensive ETF | Existing research module |
| `range` | Bounded SPY grid, max 40% exposure | Fixed validation complete; module-only WF passed |
| `bull` | Point-in-time stock Turtle | Six fixed candidates compared; combined route WF still fails turnover |

The controller is intentionally separate from each strategy's risk model. A
route cannot bypass global exposure, liquidity, drawdown, stale-data, or kill-
switch checks.

## Initial Historical Check

On the Norgate defensive ETF history, the next-session SPY returns by route
were:

- `crisis`: 113 sessions, average `-0.14%`
- `defensive`: 1,070 sessions, average `+0.07%`
- `range`: 923 sessions, average `+0.09%`
- `bull`: 3,366 sessions, average `+0.04%`

This validates that the crisis label identifies weaker short-term benchmark
conditions. The fixed grid module returned 54.98% total, 2.04% annualized,
Sharpe 1.09, and -7.14% maximum drawdown over 2004-2026, with 19 fixed
walk-forward windows and four abnormal-move exits. It is a drawdown-control
module, not a return replacement for equity exposure.

The Bull route now has six fixed candidates using point-in-time membership and
ADV controls. The IS-ranked 55/20, five-position, 25M ADV candidate returned
60.59% standalone OOS (Sharpe 0.60, max drawdown -23.48%) and 39.06% when
ARC-routed (Sharpe 0.53, max drawdown -16.76%). No OOS value selected it.

The first complete ARC combination returned 110.30% total with 3.14%
annualized return, Sharpe 0.39, and -26.34% maximum drawdown under the 3/5
controller and fixed 10%/10-session turnover controls. Its 22-window
Walk-Forward failed one turnover window. The 5/10 controller also failed its
fixed turnover gate. Both remain research-only.

## Execution Boundary

Current status is `research_only_no_orders` and `paper_authorized` is false. The
ARC scripts write signals and research summaries only. They do not import an
order-submission client and do not connect to TWS. The research dashboard is
served at `https://dev-cn-01.yios.cn/deepstock` through NGINX to local port
15001.
