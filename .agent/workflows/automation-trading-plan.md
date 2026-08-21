# US Equities Automated Trading Plan

## Status

This plan is in Phase 1 research validation. It does not authorize live trading
or order submission.

## Objectives and Constraints

- Target: liquid US-listed equities and ETFs.
- Style: low-turnover, long-only, diversified, and risk-first.
- Initial execution: paper trading only.
- Primary measures: drawdown, volatility, turnover, fill quality, and reliability;
  return is not the only acceptance criterion.

## Recommended Platform Approach

Use a broker adapter so strategy code is independent of the selected broker.

| Need | Recommendation | Reason |
| --- | --- | --- |
| Initial paper execution | IBKR TWS API with an IBKR paper account | The user has an opened and funded IBKR account; paper mode validates the same broker workflow |
| Potential live execution | Interactive Brokers, subject to paper-trading gates | Uses the selected broker while keeping live execution separately controlled |
| Historical research data | Massive adjusted daily aggregates | Reproducible adjusted-price history from the user's subscription |
| Market calendar | Exchange calendar source | Avoid orders on holidays and early closes |
| Persistent state | SQLite initially; upgrade only if concurrent workloads require it | Simple, auditable, recoverable |
| Scheduling | System scheduler invoking a Python entry point | Fewer moving parts for a once-daily system |
| Alerts | Email or approved messaging webhook | Makes failed jobs, order rejections, and circuit breakers visible |

The server is the research and control-plane host. Because TWS runs on the
user's laptop, a future laptop execution agent will make the local TWS API
connection and poll the server for approved work. The TWS API port must never
be exposed from the laptop to this server or the internet.

Broker configuration and any future secrets remain local in `.env`. The broker
adapter must expose only account, market-data, order, position, and order-status
operations. A future broker change must not modify strategy logic. Read
`ibkr-onboarding.md` before configuring an IBKR connection.

## First Strategy: Defensive ETF Trend Allocation

Start with highly liquid US-listed ETFs rather than individual stocks. This
reduces single-name, earnings-gap, borrow, and corporate-action complexity
while validating the full automation path.

Proposed initial universe: `SPY`, `QQQ`, `IWM`, `TLT`, `IEF`, `GLD`, and `SHY`.
The exact universe is subject to approval and backtesting.

1. Run signals after each US market close using adjusted daily data.
2. For each risk asset, require positive 12-month total return and price above
   its 200-day moving average.
3. Allocate only to qualifying assets; move unallocated capital to `SHY`.
4. Rebalance monthly, with no intraday discretionary trading.
5. Size positions by inverse trailing volatility, then apply caps.

This is a deliberately simple starting point. It must be compared against a
buy-and-hold benchmark and tested across several market regimes before paper
execution. A later US-stock strategy can reuse the same risk and execution
framework after point-in-time universe data is available.

## Risk Controls

All thresholds below are initial controls to be calibrated by backtesting, not
investment advice or final parameters.

| Control | Proposed initial rule |
| --- | --- |
| Direction | Long-only; no margin, leverage, options, or short selling |
| Total exposure | At most 80% of account equity |
| Single position | At most 20% of account equity |
| Trade size | At most 10% of a security's average daily dollar volume |
| Spread check | Do not submit an order when bid-ask spread exceeds its configured limit |
| Drawdown guard | At 10% portfolio drawdown, halve target risk; at 15%, halt new orders and require review |
| Order guard | Limit orders with price bands; no duplicate open orders |
| Kill switch | A local configuration flag blocks all order submission |

## Automated Daily Workflow

All schedule times use `America/New_York` and an exchange calendar controls
whether a job runs.

```text
After close: ingest and validate adjusted daily data
                  -> calculate signals and proposed targets
                  -> run risk checks and persist an immutable decision record
Rebalance day: preflight account, positions, cash, market status, and kill switch
                  -> reconcile broker state with local state
                  -> create idempotent order plan
                  -> submit guarded limit orders
                  -> poll fills/rejections, reconcile, alert, and archive run
Every run: write structured logs, metrics, broker responses, and error alerts
```

Implementation requirements:

- Use a deterministic run ID and client order IDs so retries cannot duplicate
  an order.
- Reconcile positions, cash, and open orders before planning and after fills.
- Stop submission on stale/missing data, failed reconciliation, market closure,
  or a tripped risk control.
- Keep audit records for signal inputs, target weights, risk decisions, orders,
  fills, and errors.
- Send alerts for failed jobs, rejected orders, stale data, reconciliation
  differences, and every circuit-breaker event.

## Delivery Phases and Gates

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| 0. Design | Approved broker, data source, strategy parameters, risk policy | Written approval of choices |
| 1. Research | Reproducible backtest and bias checks | Out-of-sample results and cost model reviewed |
| 2. Paper trading | Full unattended daily workflow | At least 8 weeks of reconciled, alert-tested paper operation |
| 3. Live readiness | Runbook, monitoring, recovery test, capital limits | Explicit approval after a dry-run review |
| 4. Limited live | Small, capped capital deployment | Ongoing reconciliation and drawdown review |

## Decisions Required Before Implementation

1. Review the completed fixed-split parameter-grid result, including the
   strategy's 14.14% to 19.22% out-of-sample return range versus `SPY` at
   39.72%, before selecting further research work.
2. Test the strategy across explicitly defined market regimes and review the
   consequence of its low-beta design before any paper-order capability is
   considered.
3. Confirm the runtime host and approved alert channel before unattended paper
   execution is implemented.
