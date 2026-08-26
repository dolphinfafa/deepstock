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

The independent SPY mean-reversion baseline is implemented in
`src/deepstock/mean_reversion.py`. It is long-only SPY/SHY research using a
fixed 20-session Z-score entry, a 200-session trend filter, next-session
execution, and 5 bps modeled costs. The fixed 2021-08-23 OOS period returned
15.00% versus SPY at 84.79%; seven of its 22 fixed Walk-Forward windows were
negative. It is therefore `research_only_no_orders` and has no Paper, live, or
ARC routing authority. Its fixed specification and research gate are recorded
in `mean-reversion-strategy.md`.

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

A separate Windows data node is now reachable by verified SSH. It runs TWS
Paper and Norgate Data Updater locally. The project is cloned at
`F:\\workspace\\deepstock`; Conda environment `deepstock` uses the existing
Miniconda installation (Windows Python 3.12.1) and contains `norgatedata
1.0.77`, `ibapi 9.81.1.post1`, and the project dependencies. Windows pip could
not complete a TLS handshake with PyPI, so wheels were downloaded on the
server with normal certificate verification and installed offline on Windows;
certificate verification was not disabled. Imports succeeded and the project
test suite passed (`21 passed`, 2 pandas deprecation warnings).

Its TWS API listener currently binds to all interfaces on port 7497; the user
has configured the TWS trusted-IP allowlist to `127.0.0.1`. Keep this allowlist
in place, verify it with a local read-only probe before use, and never forward
or expose the port.

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

After downloading `FB` and merging it into `META` at the 2022-06-09 ticker
switch, the complete 16-stock universe had 22,572 rows and zero missing values.
The same six fixed-split combinations still all underperformed `SPY`; the
in-sample-ranked 20/10 with five positions returned 20.30% out of sample,
Sharpe 0.76, maximum drawdown -11.02%, turnover 68.06, and modeled costs 6.81%
versus `SPY` at 39.72%. The ticker correction does not change the decision to
block paper orders.

An additional static sector-cap baseline grouped the 16 stocks into technology,
internet, consumer, financial, energy, and health, with at most two positions
per group. Its in-sample-ranked 20/10 plus five-position configuration returned
5.90% out of sample, Sharpe 0.29, maximum drawdown -12.67%, turnover 72.62, and
modeled costs 7.26%, versus `SPY` at 39.72%. The cap reduced concentration but
materially reduced returns in this sample; sector labels are a research
snapshot, not point-in-time classifications.

The adaptive defensive ETF grid evaluated 32 predeclared combinations of
126/252-day momentum, 150/200-day trend, top 2/3 assets, and 80%/100% above-
trend versus 20%/40% below-trend exposure. The in-sample-ranked winner was
252-day momentum, 200-day trend, top 2, and 80%/40% exposure; out of sample it
returned 27.17%, Sharpe 1.87, maximum drawdown -5.79%, turnover 2.06, and
modeled costs 0.21%, versus `SPY` at 39.72%. This improves on the original
defensive result but still does not beat the benchmark or establish paper-order
readiness.

Rolling evaluation of that fixed configuration used a 504-session training
history and five 126-session test windows. Strategy maximum drawdown was
-3.30% to -1.36%; it underperformed `SPY` in the first four windows and
outperformed only in the final window (12.38% versus 6.69%). This is consistent
with a defensive regime-dependent profile, not persistent benchmark alpha.

The Windows Norgate Python connection was verified on 2026-08-21. The updater
reported a healthy status and exposed 14,633 symbols in `US Equities` plus
1,413 in `US Equities Delisted`. Under the current US Stock Data Trial,
`AAPL` returned daily history only from 2024-08-21 through 2026-08-20; this is
shorter than the long-history tiers and is insufficient for the planned
survivorship-free long-horizon backtest. The user has since purchased Norgate
Platinum, but the local updater has not yet reflected the change: on
2026-08-22 `AAPL` still returned history only from 2024-08-21 through
2026-08-21 (502 rows). Refresh/re-authenticate the updater and repeat the
history check before any bulk download.

On a repeat check after the user requested another verification, the same
Windows node returned empty quote dates for `AAPL` and Norgate rejected
`price_timeseries` with `Access is denied`. Treat this as an updater
authorization/session issue, not as evidence that Platinum lacks history.

After the database-folder change and updater restart, Platinum entitlement was
verified: `AAPL` history begins on 1990-01-02 (9,210 daily rows). The local
database refresh remains incomplete: AAPL ends on 2026-07-29 and the delisted
database is not yet exposed by the Python API. Wait for the updater to finish
before downloading the research universe.

The refresh subsequently completed on 2026-08-22. `AAPL` has 9,227 daily bars
from 1990-01-02 through 2026-08-21, while `US Equities Delisted` has 21,134
symbols and returns historical prices. The Windows node is ready for a staged,
resumable download of the point-in-time research universe.

The stock-Turtle data preparation now includes a resumable Norgate exporter for
the `S&P 500 Current & Past` watchlist. It stores adjusted price chunks and
historical constituent intervals separately, so a future engine can enforce
point-in-time membership instead of treating all current/past symbols as
always eligible. The initial pilot must be validated before a full download.

The pilot succeeded and the full export completed on 2026-08-22: 1,301 S&P 500
current-and-past symbols, 14 price chunks, approximately 105 MB, with 261
symbols recorded as unavailable. The raw chunks are local Git-ignored research
inputs. The existing Turtle engine is not yet valid for this matrix because it
requires complete daily columns; membership-aware, missing-price handling must
be implemented before ranking any stock-Turtle result.

The membership-aware Turtle engine was then validated on the downloaded
universe. A correctness bug that discarded existing positions when a stronger
new breakout appeared was fixed; positions now persist until exit and only
vacant slots accept new entries. The first run using the buggy behavior was
discarded. With the corrected engine, the in-sample-ranked 20/10 configuration
with three positions returned 12.83% out of sample, Sharpe 0.23, maximum
drawdown -23.28%, and turnover 43.26 versus SPY at 84.79%. All six predeclared
combinations underperformed SPY, so stock-Turtle paper trading remains blocked.

The refreshed stock export contains adjusted close, volume, and turnover:
1,301 requested symbols across 14 chunks and 4,108,688 rows, with 261 symbols
recorded as unavailable. Norgate's Python API exposes current classifications
but no historical sector time series, so liquidity filters are now possible but
point-in-time sector caps remain blocked. Missing risk prices currently trigger
a next-session safe-asset exit, which is an explicit but optimistic
zero-slippage delisting assumption. The detailed research gate and next data
requirements are recorded in `stock-turtle-research.md`; no Paper or live
order is authorized from this baseline.

The first fixed-ADV experiment uses a USD 10 million prior 20-session average
turnover requirement for new entries and preserves the original six-parameter
grid and split. The in-sample-ranked 55/20, five-position configuration
returned 60.59% out of sample (9.98% annualized, Sharpe 0.60, maximum drawdown
-23.48%) versus SPY at 84.79%. A different configuration had a higher OOS
return but was not selected in sample, so it is not used retrospectively; the
stock strategy remains research-only.

A fixed three-state regime overlay was also tested on the defensive ETF history:
80% exposure in normal conditions, 40% in alert conditions, and 20% in crisis
conditions using the SPY 200-day trend plus fixed 20/252-day volatility ratios.
On the 2021-08-23 split it returned 39.30% OOS, annualized 6.88%, Sharpe 1.18,
and maximum drawdown -11.50%, versus the frozen 80%/40% baseline at 38.96%,
6.83%, Sharpe 1.17, and -11.70%. The small difference does not authorize
changing the observed ETF configuration.

The multi-strategy controller is named Deepstock ARC (Adaptive Regime
Controller). Its fixed four-state routing sends `crisis` and `defensive` to the
defensive ETF module, `range` to a pending grid adapter, and `bull` to a pending
point-in-time stock-Turtle adapter. The initial Norgate history check found
next-session SPY average returns of -0.14% in crisis, +0.07% in defensive,
+0.09% in range, and +0.04% in bull. This is a regime diagnostic, not a
combined-strategy result; ARC remains research-only with no order capability.

The ARC range-route grid adapter is now implemented as a bounded, long-only
SPY module. Its fixed 50/20-day anchor-volatility rules, four levels, and 40%
maximum exposure produced 63.37% total return, 2.29% annualized, Sharpe 1.25,
and -6.12% maximum drawdown over the 2004-2026 Norgate ETF history, versus SPY
at 860.23%. This is a drawdown-control result with substantial opportunity
cost, not evidence that the grid should run outside range states.

The ARC bull route now gates the point-in-time stock Turtle engine: entries are
allowed only in `bull`, and leaving that state moves targets to SHY on the next
session. With the fixed ADV rule and six predeclared configurations, the
in-sample-selected 20/10, three-position route returned -5.77% OOS,
annualized -1.19%, Sharpe -0.03, and maximum drawdown -33.36%, versus SPY at
84.79%. This route fails the current acceptance bar and remains research-only.

The first Norgate ETF long-history baseline downloaded the seven defensive ETF
symbols into a Git-ignored local file (45,596 rows, common coverage
2004-11-18 through 2026-08-21). The frozen 252/200, top-2, 80%/40% adaptive
configuration was evaluated without reselecting parameters. A 504/252/252
walk-forward produced 19 test windows and remained regime-dependent, including
one -9.81% test window. Full-period cost stress produced Sharpe 1.02 at 0 bps,
1.00 at 5 bps, 0.98 at 10 bps, and 0.94 at 20 bps, with maximum drawdown from
-11.56% to -12.12%. This is a robustness baseline, not a go-live result.

An order-free eight-week observation workflow now exists for the frozen ETF
configuration. On the Windows Norgate node it exports total-return ETF data
with a provenance manifest; the plan generator then produces a deterministic
`plan_id` and target weights, while the observer appends one idempotent JSONL
record. These utilities have no `ibapi` import and cannot submit orders. The
acceptance criteria and daily procedure are in
`defensive-etf-observation.md`.

An AHL-inspired global futures trend program is planned, but has no historical
futures dataset or backtest result. It is not a replication claim for Man AHL:
the proprietary model, instruments, execution, portfolio construction, and
risk controls are not public. Before any engine is run, a licensed daily
settlement dataset with individual contracts, documented continuous-series and
roll treatment, contract metadata, and coverage manifest is required. ETF
proxies and IBKR historical snapshots are not substitutes. The written
pre-registration is in `ahl-global-futures-trend.md`; it is research-only, has
no IBKR order authority, and cannot replace ARC's Bull route without its own
fixed OOS and Walk-Forward validation.

Strategy selection now has an order-free shadow-governance layer. Its registry
contains every current strategy, but only the frozen Defensive ETF strategy may
submit an observation snapshot; all other strategies remain research-only. A
fixed policy evaluates data freshness, costs, rolling OOS evidence,
Walk-Forward consistency, turnover, drawdown, and completed shadow sessions.
It writes one append-only recommendation per strategy and as-of date, cannot
authorize paper/live execution, and requires a separate human review even when
it returns `eligible_for_paper_review`. The operating contract is in
`strategy-governance.md`.

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
