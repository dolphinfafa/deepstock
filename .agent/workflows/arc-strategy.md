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

## Controller Diagnostic: 2026-08-25

The controller was audited without changing the active rule. The current 3/5
controller made 161 state switches (7.41 per 252 sessions), including 96
`bull`/`range` switches. A predeclared 5-confirmation/10-hold/20-session
re-entry-cooldown diagnostic candidate reduced this to 98 switches (4.51 per
252 sessions), 55 `bull`/`range` switches, and a 55-session average state
duration. This is a diagnostic candidate only; it is not selected for ARC.

Forward returns expose a separate timing weakness: the controlled `crisis`
state had positive average SPY returns at 5, 20, and 60 sessions in all three
fixed controller variants. Confirmation can reduce churn but can also label a
market after its initial shock. The next permitted research is a predeclared
state-feature and timing experiment; no OOS result may choose a controller.

The first predeclared timing experiment used two-session risk-off confirmation
that bypasses the hold and re-entry cooldown, with five-session recovery
confirmation, ten-session minimum hold, and a 20-session re-entry cooldown.
Using the same point-in-time Bull candidate, data, cost model, and 22-window
Walk-Forward evaluation, it improved the continuous result to 177.26% total,
4.33% annualized, Sharpe 0.53, and -18.22% maximum drawdown. Executed route
switches fell from 153 to 96. However, two Walk-Forward windows exceeded the
fixed turnover threshold, versus one for the prior controller. The candidate
therefore remains unselected and research-only.

## ADX Controller Diagnostic: 2026-09-30

The article thresholds were tested as one predeclared controller candidate,
not treated as a complete trading rule. Standard 14-session Wilder ADX/DMI was
calculated from SPY high, low, and close. The fixed mapping was:

- ADX below 20: range;
- ADX 20 to below 25: chaos, still routed to range;
- ADX at least 25 with `+DI > -DI`: bull;
- ADX at least 25 with `-DI >= +DI`: defensive; and
- bearish ADX at least 40: crisis.

The candidate used the existing fixed 3-session confirmation and 5-session
minimum hold. It was compared with the current controller on identical dates,
route modules, point-in-time Bull candidate, 5 bps costs, risk layer, 10%
rebalance band, and 10-session route cooldown. Massive supplied only 1,254 SPY
OHLC bars, so the common post-warm-up sample was limited to 1,202 sessions from
2021-11-05 through 2026-08-21.

| Controller | Total return | Sharpe | Max drawdown | Controller state changes | Execution route switches | Average state duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current ARC 3/5 | 24.31% | 0.38 | -16.21% | 35 | 34 | 33.4 sessions |
| ADX 14 with 3/5 | 14.66% | 0.35 | -9.38% | 45 | 45 | 26.1 sessions |

ADX reduced drawdown but also reduced return, increased controller state changes,
and classified 704 sessions as range versus only 294 as bull. Bull/range
switches were nearly unchanged at 19 versus 20. The controlled ADX defensive
state was followed by an average 20-session SPY return of +2.85%, versus +1.01%
after its bull state, indicating that the bearish label often persisted into a
rebound. Only two raw crisis sessions occurred and none survived the fixed
confirmation rule.

Both fixed Walk-Forward test windows for ADX were positive and passed the
per-window loss, drawdown, and turnover checks, but the project requires at
least six windows. The candidate therefore cannot be selected. Full-history
OHLC from the licensed Norgate node is required before deciding whether ADX is
useful as a secondary feature rather than the primary ARC state controller.

The Windows-only Norgate exporter and source-manifest validation were prepared
on 2026-09-30. The exporter writes licensed `TOTALRETURN` open, high, low, and
close locally; the comparison script rejects missing or mismatched coverage
metadata and records the provider in its research manifest.

### Licensed Full-History Result

The study computer subsequently came online and exported 8,474 SPY sessions
from 1993-01-29 through 2026-09-29. The common ARC evaluation range was 5,473
sessions from 2004-11-18 through 2026-08-21, producing 19 fixed Walk-Forward
windows. Results on identical routes, risk controls, and costs were:

| Controller | Total return | Sharpe | Max drawdown | State changes | Route switches | Bull/range switches | WF result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Current ARC 3/5 | 268.19% | 0.56 | -25.15% | 161 | 152 | 96 | Failed: 5 turnover windows |
| ADX 14 with 3/5 | 289.64% | 0.86 | -11.41% | 205 | 190 | 86 | Failed: 1 turnover window |
| ADX 14 with predeclared 5/10/20 anti-churn | 263.59% | 0.82 | -12.58% | 152 | 141 | 65 | Passed 19-window thresholds |

The fixed 5/10/20 rule was already used in the project's controller research;
it was not chosen by searching these ADX results. It reduced the ADX raw 271
state changes to 152, reduced Bull/range switches from 105 to 65, and increased
average state duration to 35.8 sessions. Two windows remained negative, with
the worst at -10.05%, but neither crossed the predeclared severe-loss threshold.

This is a retained research candidate, not a selected production controller.
Selecting it because it passed this OOS report would violate the no-OOS-
selection policy. It also remains far below SPY's 860.23% total return, and the
controlled defensive state was followed by +2.16% average 20-session SPY
return versus +0.72% after bull, so timing lag remains visible. Independent
holdout or prospective shadow validation is required before any rule change.

## Execution Boundary

Current status is `research_only_no_orders` and `paper_authorized` is false. The
ARC scripts write signals and research summaries only. They do not import an
order-submission client and do not connect to TWS. The research dashboard is
served at `https://dev-cn-01.yios.cn/deepstock` through NGINX to local port
15001.
