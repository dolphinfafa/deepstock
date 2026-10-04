# CSI 300 Opening-Auction Research

## Status

- Strategy ID: `csi300_opening_auction`
- Policy version: `auction_context_v1_frozen_20260829`
- Execution status: `research_only_no_orders`
- Broker integration: none
- Current data collection owner: the existing Darwen scheduler until a
  separate Deepstock migration is tested and explicitly cut over
- Deepstock artifact synchronization: local read-only ingestion from Darwen at
  12:50 and 23:58 daily, plus 18:20 on weekdays
- Local handoff material: `handoff/` (ignored by Git and never redistributed)
- Deepstock local research copy: `artifacts/auction_history*`,
  `artifacts/auction_probe`, and `artifacts/short_term_forward` (ignored by Git)

This is an independent short-horizon A-share research system. It is not an ARC
route, not part of the Darwen long-term funnel, and not authorized for live
orders.

## Fixed Research Definition

- Universe: point-in-time CSI 300 constituents reconstructed from monthly
  Tushare `index_weight` snapshots.
- Signal time: after the call-auction result is available and before 09:30
  Asia/Shanghai.
- Entry label: observed 09:30-09:31 first continuous-auction minute VWAP,
  calculated as amount divided by volume.
- Exit label: next trading-day close. Suspensions and suspected limit-down
  exits remain open rather than assuming a fill.
- Target: `next_close / entry_0931_vwap - 1`.
- Cost assumption: 20 bps round trip.
- Capacity: no more than 5% of first-minute notional per stock.
- Portfolio: at most five stocks and at most two stocks per industry.

Historical minute labels are accepted only when the bar is exactly 09:31,
prices and volume are valid, VWAP is consistent with the high-low range, and
point-in-time CSI 300 coverage is at least 90%. There is no fallback to the
auction price or to a later minute.

## Point-in-Time and Freeze Rules

All daily-bar features must use the previous trading day or earlier. The
current signal day's close, high, low, volume, and market capitalization are
not available to the pre-open model.

Every training path must enforce:

```text
target_next_date < prediction_date
```

Equality is also disallowed. Assertion failures are data errors and must never
be bypassed.

The policy has been frozen since 2026-08-29. The prospective period begins on
2026-08-31. Existing prospective results may not be used to select features,
windows, thresholds, costs, or portfolio limits. Frozen parameters include:

- context feature set;
- ensemble windows 40, 60, 80, and 120 days;
- 60-day research fallback window;
- 20-day half-life;
- ridge alpha 10;
- 12% target clipping;
- Top 5 and maximum two stocks per industry;
- 20 bps round-trip cost;
- model-agreement threshold 0.5;
- neutral up-probability threshold 52%;
- risk-off up-probability threshold 55% and expected-return threshold 40 bps.

Daily coefficient refits may use only labels already realized before the
prediction date. They do not authorize rule selection.

## Health and Execution Gates

The dynamic strategy is healthy only when all fixed checks pass:

- return after 20 bps is positive;
- return spread over the full cross-section is positive;
- daily t-statistic is at least 1.0;
- active-day share is at least 20%.

When health fails, the system may emit a context-model research ranking, but it
must set `candidate_mode=context_research_fallback` and `actionable=false`.
That output is an observation, not a trade instruction.

Forward signals may be created only from 09:25 inclusive to 09:30 exclusive.
Late auction snapshots must not be inserted into official signals or orders.
Explicitly approved late observations belong only in isolated counterfactual
tables with `official_forward_sample=false`.

## Data Sources and Storage

- CSI 300 membership: Tushare `index_weight`.
- Auction history and live result: Tushare `stk_auction`.
- Historical 09:31 labels: Tushare `stk_mins`; current entitlement has been
  observed to allow two calls per day.
- Live 09:31 snapshots: Tushare `rt_min`, accepted only after repeated stable
  observations of the completed 09:31 bar.
- Daily prices: Tushare plus the existing Darwen `MarketBar` adapter.
- Announcements: Tushare `anns_d`, with immutable first-seen timestamps and
  strict `live`/`backfill` separation.

Tushare access was verified on 2026-10-04 with a non-minute `trade_cal` query;
the restricted `stk_mins` quota was not consumed. FTShare MCP initialization
also succeeds, but its current service exposes versioned tools such as
`ft_v2_stock_candlesticks_batch`, `ft_v2_stock_minutes_batch`, and
`ft_v3_semantic_search_news`. The inherited adapter still references legacy
unversioned tool names and must be updated and retested before new FTShare
downloads. Existing FTShare caches remain usable without a new request.

The local Tushare token is a secret and must never be placed in Git, reports,
logs, or chat. Licensed/raw handoff files remain under the ignored `handoff/`
directory.

Forward state is held in a single-writer SQLite database. The original database
must not be deleted, rebuilt, or copied while being written. Any future
migration must use SQLite backup semantics and retain the source until a full
signal, 09:31 fill, and D+1 settlement cycle succeeds on the target.

## Verified State on 2026-10-04

- Handoff artifacts match the active Darwen copies by SHA-256 for the minute
  manifest, frozen baseline report, forward database, and latest report.
- The complete handoff artifact tree was copied into Deepstock's ignored local
  `artifacts/` tree: 856 files, 61,240,357 bytes, with a matching full SHA-256
  manifest and an empty checksum-based rsync diff.
- SQLite `quick_check` returns `ok`.
- The six related offline test files pass: 51 tests.
- Python compilation passes for the research, forward, API, and scheduler
  modules.
- The source guards for label timing, decision cutoff, strict 09:31 bars,
  prospective isolation, and `actionable` state are present.
- The active scheduler remains only in Darwen. Deepstock has no duplicate
  Tushare auction or forward collector. Deepstock runs a local artifact sync
  that makes a consistent SQLite backup and does not call a data provider.

Historical backfill for 2026-08-31 through 2026-09-29 is 8/21 complete, with 13
dates remaining. The final
`execution_backtest_20260929_frozen.json` must not be generated until all 21
dates pass the 90% coverage gate.

Frozen pre-prospective baseline:

- context model gross mean per selected trade: approximately +0.373%;
- context model after 20 bps: approximately +0.173%;
- context daily t-statistic: 0.82;
- optimized ensemble portfolio mean after cost: approximately -0.162% per day.

Latest formal forward report through 2026-10-02:

- 20 completed baseline cohorts;
- baseline cumulative return approximately -4.60%;
- mean daily return approximately -0.232%;
- daily win rate 30%;
- daily t-statistic -1.37;
- zero actionable or health-passed signal days.

These results do not establish a profitable strategy. The system remains
research-only, and the frozen prospective sample must not be used for
post-hoc tuning.

## Takeover Boundary and Next Steps

The implementation currently lives in an uncommitted Darwen worktree and
depends on Darwen `Company`, `Security`, `MarketBar`, database configuration,
Tushare client, FastAPI API, and Vue frontend. Deepstock currently has no ORM,
database, API framework, or frontend framework selected. Do not copy the
implementation into Deepstock until an adapter/dependency design is approved.

Until then:

1. Keep the existing Darwen backfill and forward scheduler as the only writer.
2. Keep the Deepstock local artifact sync healthy so each Darwen update is
   available under Deepstock's ignored `artifacts/` tree.
3. Complete the remaining frozen September minute labels without increasing
   the established two-call daily budget.
4. Validate the final prospective report and test for a data-source
   discontinuity between the legacy FTShare labels and Tushare labels.
5. Continue observation to at least 60 completed forward baseline cohorts.
6. Pre-register any next policy version and independent evaluation interval
   before changing features or thresholds.
7. If Level-2 data becomes available, build a separate fill-realism study
   before changing the current return labels.
