# Deepstock Project Overview

## Scope

Deepstock is a US-equities quantitative research and paper-trading project.
The current phase is reproducible strategy research and backtest validation.
Live trading is excluded until a separate execution and risk-control design is
approved.

## Architecture and Data Flow

The proposed initial architecture is documented in `automation-trading-plan.md`.
It uses a broker adapter, adjusted daily data, persisted run state, a system
scheduler, and an alert channel. These are proposals pending user approval. The
intended workflow is:

```text
Market data -> Research -> Backtest -> Paper trading -> Evaluation
```

The current deployment split is server-side research and laptop-side IBKR
execution. The laptop execution agent will connect to its local TWS instance;
the server does not receive TWS API access.

Each stage must preserve the assumptions and input data needed to reproduce its
output. Strategy evaluation must address look-ahead bias, survivorship bias,
data leakage, transaction costs, slippage, liquidity, and corporate actions.

## Interfaces and Data Storage

No project API, database schema, or persistence layer exists yet. Document
request/response contracts, authentication, table design, indices, and data
licenses here as they are introduced.

The current executable integration surface is the local script
`scripts/ibkr_read_only_check.py`. It connects only to a laptop-local TWS or IB
Gateway session in paper mode, loads connection settings from the untracked
`.env`, and returns server time plus counts for account-summary rows, positions,
and open orders. Account identifiers, balances, quantities, and order details
are redacted from console and JSON output. Order placement is explicitly
disabled inside the probe class.

The research surface is `scripts/run_defensive_etf_backtest.py`, which accepts
local, adjusted-close CSV data and writes daily results, target weights,
executed weights, and summary metrics. It has no broker dependency at runtime
and no order-submission capability.

The required data schema and provenance rules are in `data/README.md`. A market
data provider has now been selected: Massive supplies split-adjusted daily bars
and dividend factors, which the project combines into total-return research
data; IBKR provides execution-time prices and account state. No unlicensed or
unadjusted data may be used to judge strategy performance.

The first real backtest used 1,254 aligned sessions from 2021-08-23 through
2026-08-20, the full history returned by the current Massive subscription. It
is an initial in-sample result, not a go-live decision: the defensive strategy
returned 34.63% versus 82.47% for `SPY`, with a 6.25% maximum drawdown.

The first robustness run fixed its split before examining out-of-sample results:
2021-08-23 through 2024-08-20 is in sample and 2024-08-21 through 2026-08-20
is out of sample. It evaluated all 18 combinations of 150/200/250-day trend,
126/252-day momentum, and 42/63/126-day volatility windows. Results are ranked
only by in-sample Sharpe ratio. Across the out-of-sample period, strategy
returns ranged from 14.14% to 19.22% with maximum drawdowns from -5.12% to
-4.42%; `SPY` returned 39.72%. The defensive outcome is stable within this
narrow grid but materially underperforms the benchmark, so paper-order work
remains blocked pending further research review.

A separate Turtle-style breakout module is available for research only. It
uses configurable entry/exit channels, a close-based ATR proxy for risk sizing,
2-ATR stops, 80% total exposure, and next-session execution. On the same full
history, 55/20 returned 20.10% with -14.46% maximum drawdown, Sharpe 0.59,
turnover 27.87, and modeled costs 2.79%; 20/10 returned 19.79% with -13.39%
drawdown, Sharpe 0.55, turnover 64.38, and costs 6.44%. `SPY` returned 82.47%.
These exploratory results do not authorize execution.

The stock variant reuses the Turtle engine with a caller-supplied stock
universe, a maximum number of concurrent positions, and breakout-strength
ranking when more names qualify than the cap. It still requires a safe asset
column (normally `SHY`) and remains long-only. No stock history has been
downloaded or evaluated yet; the current ETF dataset must not be reused as a
stock result.

The server-side paper boundary is `scripts/generate_paper_plan.py`. It reads
local research data, emits deterministic target weights and a `plan_id`, and
supports a kill switch. It has no broker dependency and never submits an order;
the laptop is the only paper TWS connection point.

The first server stock-Turtle download contained 18 symbols and 22,482 rows
(2021-08-23 through 2026-08-20; the subscription did not provide the requested
2015 start). `META` had 90 missing sessions because its pre-2022 ticker was
`FB`, so the initial backtest excluded it rather than filling or silently
renaming the gap. With the remaining 15 stocks, 55/20 returned 64.32% with
-15.49% maximum drawdown, Sharpe 0.84, turnover 89.36, and modeled costs 8.94%;
20/10 returned 101.33% with -12.39% drawdown, Sharpe 1.12, turnover 167.47,
and costs 16.75%. `SPY` returned 82.47%. These are full-sample, survivor-prone
exploratory results and do not authorize paper orders.

The fixed stock-Turtle split uses 2021-08-23 through 2024-08-20 in sample and
2024-08-21 through 2026-08-20 out of sample. Six predeclared combinations of
20/55 entry, 10/20 exit, and 3/5 maximum positions were evaluated. The
in-sample-ranked winner was 20/10 with five positions; out of sample it returned
20.60%, Sharpe 0.77, maximum drawdown -11.46%, turnover 62.51, and modeled
costs 6.25%, versus `SPY` at 39.72%. All six combinations underperformed `SPY`.
This invalidates the apparent full-sample 20/10 outperformance as a selection
basis and keeps paper orders blocked.

## Key Decisions

| Decision | Status | Rationale |
| --- | --- | --- |
| Target market | US equities | User-defined scope |
| Execution mode | Paper trading only | Limits financial risk during initial development |
| Python runtime | Conda `deepstock`, Python 3.12.13 | Established project environment |
| Secrets | Local `.env`, Git ignored | Prevents credential disclosure |
| Initial automation design | Proposed, pending approval | Broker adapter and paper-first workflow |
| Initial strategy | Proposed, pending approval | Defensive, low-frequency ETF trend allocation |
| Execution broker | IBKR selected for paper-integration planning | User account opened and funded; no API or live trading enabled |
| Execution host | User laptop | TWS is installed there; server has no graphical environment |
| IBKR validation path | Read-only probe script only | Approved scope excludes any order-submission capability |
| First research strategy | Defensive ETF trend allocation | Implemented as a reproducible, no-order backtest |
| Historical research data | Massive adjusted daily bars | User subscription; key remains in local `.env` |
| IBKR market data | Fee-waived account entitlement | Reserved for execution-time validation, not research history |
| Initial real backtest | Completed, not approved for trading | Five-year window requires robustness and out-of-sample review |
