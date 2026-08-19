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
`.env`, and returns four read-only datasets: server time, account summary,
positions, and open orders. Order placement is explicitly disabled inside the
probe class.

The research surface is `scripts/run_defensive_etf_backtest.py`, which accepts
local, adjusted-close CSV data and writes daily results, target weights,
executed weights, and summary metrics. It has no broker dependency at runtime
and no order-submission capability.

The required data schema and provenance rules are in `data/README.md`. A market
data provider has now been selected: Massive supplies split- and
dividend-adjusted daily research data; IBKR provides execution-time prices and
account state. No unlicensed or unadjusted data may be used to judge strategy
performance.

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
