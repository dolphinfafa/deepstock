# Deepstock Project Overview

## Scope

Deepstock is a US-equities quantitative research and paper-trading project.
The current phase is project initialization. Live trading is excluded until a
separate execution and risk-control design is approved.

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
