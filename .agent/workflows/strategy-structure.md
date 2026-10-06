# Strategy Structure and Market Classification

## Mandatory Scope Rules

Every registered strategy declares exactly one category: `US` (美股), `CN`
(A股), or `Both` (explicit US and A-share research coverage). Never infer
`Both` from a portable indicator or a generic engine. Each market needs its own
calendar, instrument rules, transaction costs, data evidence and validation.
Category is not an execution permission or a passed research gate.

An explicit research pause is recorded separately from execution status.
`research_control.py` reads the registry and prevents the tail downloader,
backtest CLI and new-result ingestion from running while `paused_missing_data`.
Do not resume automatically when a provider recovers or new data arrives.

ARC is one peer strategy. New research is independent by default. The afternoon
momentum strategy is **CN only**; its frozen first instrument is `510300.SH`.
Do not extend it to US instruments or route it through ARC without a separate
user decision.

## Canonical Organisation

```text
src/deepstock/
  strategies/
    us/      defensive ETF, stock Turtle, SPY mean reversion, grid, ARC
    cn/      ETF afternoon momentum / T+1
    both/    independent Granville ETF MA swing, separate US/CN ledgers
  markets.py              US / CN / Both category contract
  risk.py                 shared risk infrastructure
  strategy_governance.py  shared gates, not a strategy
  web/                    shared authenticated control plane
scripts/
  cn/                     canonical ETF-minute downloader and backtest CLI
  *.py / *.cmd            stable existing scheduled/operational entry points
config/
  strategy_catalog.json   strategy descriptions and progress, explicit market
  strategy_registry.json  execution status and explicit matching market
```

The legacy `deepstock.backtest`, `arc`, `bull`, `defensive`, `grid`,
`mean_reversion`, `regime`, `turtle` and `tail_momentum` modules only re-export
the canonical implementation. They preserve old imports, command entry points
and Windows schedules; they contain no duplicate strategy logic. Existing
artifact paths, licensed data and historical decisions are not moved or reset.
New strategy code belongs under its market package. Common data, risk,
governance and execution infrastructure stays outside those strategy packages.

## Current Classification

| Category | Strategies |
| --- | --- |
| US | Defensive ETF; ARC/ADX; stock Turtle; grid; SPY mean reversion; frozen AHL candidate |
| CN | CSI300 opening auction; ETF afternoon momentum / T+1 |
| Both | Granville-inspired ETF MA swing (SPY / 510300.SH) |

The frozen AHL candidate's first eight contracts are US-exchange futures
(`ES/NQ/ZN/ZB/CL/GC/6E/6J`), hence the US venue bucket. “Global” in its name
describes asset exposures, not verified A-share coverage. It remains archived
and paused; this metadata cleanup neither resumes its research nor changes its
universe. Asset class remains `Futures`, separate from the market category.

The October 6 user decision supersedes the old import-only boundary: CSI300
owns collection, models, fixed backtests and observation under `cn/auction/`,
with Deepstock-local data/environment/scheduling. Shared cleaning/versioning
remains in `deepstock.data`; Darwen is only a retained migration source, never
a runtime dependency or duplicate writer. Entire `handoff/` stays Git ignored.

## Database and UI

The existing `strategies.market` column stores the category; no schema change
or evidence-table rebuild is needed. Catalog ingestion rejects invalid market
values before writing. The registry must agree with the catalog. The API
`GET /api/strategies?market=US|CN|Both` filters exact categories (including a
separate `archived=true` option); invalid values return 422. The homepage and
frozen archive expose All / 美股 / A股 / Both tabs, counts and bookmarkable
`?market=...` links. Details return to their selected category.

Market filtering never changes freeze status, parameters, holdings or order
authority. The server → tests → GitHub → clean Windows pull sequence remains
mandatory for every project change.
