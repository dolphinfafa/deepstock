# AHL-Inspired Global Futures Trend Research

## Status

`planned_research_only_no_orders`. This is an independently designed,
transparent trend-following research program inspired by public descriptions of
managed futures. It does not claim to replicate Man AHL or any proprietary
strategy. There is no valid futures historical dataset in the project, so no
backtest, paper plan, IBKR permission, or ARC routing change is authorized.

## Pre-Registered Initial Universe

The first validated dataset must support all selected contracts and their full
metadata. The initial pool is deliberately small and liquid:

| Asset class | Research symbols | Intended contracts |
| --- | --- | --- |
| Equity index | `ES`, `NQ` | S&P 500 and Nasdaq-100 futures |
| Rates | `ZN`, `ZB` | 10-year and 30-year US Treasury futures |
| Commodities | `CL`, `GC` | WTI crude oil and gold futures |
| FX | `6E`, `6J` | EUR/USD and JPY/USD futures |

No contract enters a simulation until its settlement, expiry, multiplier,
currency, tick, margin, trading calendar, and roll data satisfy the futures
data contract. A missing or stale required input makes the affected contract
ineligible; it is not forward-filled.

## Fixed First Candidate

This candidate is frozen before access to the new dataset:

- Signal: equal-weighted 63-, 126-, and 252-trading-day excess-return trend
  signs, computed only from information available at the close.
- Position direction: long for a positive composite signal, short for a
  negative composite signal, flat for zero or unavailable signals.
- Volatility estimate: trailing 63 daily returns, lagged one session; target
  10% annualized portfolio volatility, using 252 sessions per year.
- Risk allocation: equal risk among eligible contracts, subject to a 35% risk
  cap for each asset class and a 15% risk cap for a single contract.
- Rebalance: next-session execution after each daily signal; do not trade when
  target change is below 10% of the existing contract-equivalent exposure.
- Leverage/margin: targets are capped by documented maintenance-margin usage;
  a run must report gross and net notional, volatility forecast, and margin
  usage every session.

Execution must use actual expiring-contract prices and model the two sides of
each roll. Initial costs are predeclared as provider-supported commissions and
exchange fees plus one tick of adverse slippage per side. If the selected data
source cannot support a stated input, the run is blocked rather than silently
substituting an ETF or generic basis-point estimate.

## Validation Gate

The data source, actual coverage and costs are recorded before examining
results. Evaluation uses fixed 504-session training, 252-session test, and
252-session forward steps. It reports every test window, total return,
annualized return, Sharpe, maximum drawdown, turnover, rolling costs, margin
use, contract and asset-class risk contribution, and performance before/after
rolls. No OOS metric may choose the candidate.

The candidate may be considered as an independent ARC Bull replacement only if
it has valid continuous-contract evidence, no severe negative Walk-Forward
window under its predefined loss gate, controlled turnover/margin, and a risk
review. It cannot inherit authorization from ARC, Defensive ETF, or any stock
strategy.

## Next Required Decision

Run the documented supplier acceptance procedure in
`futures-data-source-evaluation.md`, then select and license a provider capable
of delivering the contract-level history above. Only then should a futures
research engine and synthetic unit tests be added. IBKR remains an execution
venue candidate and is not the default historical research source.
