# A-Share ETF Afternoon Momentum / T+1 Research

## Decision and Scope

- Strategy ID: `cn_etf_tail_momentum`.
- Market category: `CN` (A股 only). US extension and ARC integration are not
  approved. Canonical code: `src/deepstock/strategies/cn/tail_momentum.py`.
- Version: `tail-momentum-t1-v1-20261006`.
- Approved for independent testing by the user on 2026-10-06.
- Source note: `71581a3c-2c76-484a-9088-e65d94b5ac5c`.
- Status: `research_only_no_orders`; no broker or controller integration.
- Research state: `paused_missing_data` / “需要更多数据”, by the user's
  explicit instruction on 2026-10-06. Stop all data acquisition and market
  backtesting. Acquiring data alone does not resume this study; require an
  explicit user resumption decision. Download/backtest CLI entry points and
  new-result ingestion honor this persisted pause. Existing code/specification
  and historical evidence remain intact; no valid market backtest was completed.
- This is inspired by the supplied Quantgirl/Yang (2021) note, not a verified
  table-by-table paper reproduction. Video claims remain external claims.

These rules are declared before obtaining strategy performance results. No
threshold search, result-based candidate selection or silent rule substitution
is allowed.

## Instruments and Sample Labels

Initial instrument: `510300.SH`, ordinary A-share ETF secondary-market T+1.
The exact video window, 2025-08-18 through 2026-08-14, is an already published
replication sample, never untouched OOS. Seek longer minute history from
2021-01-04 through the latest completed mainland session as of 2026-10-06.
Always report the actual range, number of sessions and missing bars.

Historical subperiods before/after the video's sample are fixed diagnostic
holdouts, not genuine prospective trading evidence. Future observation starts
only after this version's registration. No unseen future session is fabricated.
For sufficiently long data use fixed 504/252/252 train/test/step reporting
windows without parameter fitting. Shorter datasets receive a clearly labeled
pilot report and do not satisfy the rolling-validation gate.

## Minute Data Contract

- Raw, unadjusted one-minute OHLC, volume in shares, amount in CNY.
- Explicit Asia/Shanghai timezone and end-labeled timestamps. Normalize other
  conventions only when their provider definition is known.
- Exchange trading calendar, provider provenance and raw-file checksums.
- Ex-dividend cash/share and payment dates, and any split/unit-conversion
  events. Missing corporate-action evidence must be disclosed as price-only
  diagnostic evidence, not silently claimed total return.
- Retain a 09:30 auction row separately if supplied. The 09:31 row represents
  continuous trading during 09:30–09:31, not a guaranteed opening-auction fill.
- Duplicate timestamps, invalid OHLC, negative activity or inconsistent
  minute VWAP are rejected. Missing signal bars are not forward-filled.

## Fixed Candidates

Signals are close-to-close returns:

`r6 = close(14:00) / close(13:30) - 1`

`r7 = close(14:30) / close(14:00) - 1`

Require every completed minute in the corresponding signal window. Zero is
not positive. Signals use no other factor or market-regime controller.

1. Primary `r6_next_open`: r6 > 0; submit after one-minute latency at 14:01,
   model entry using the 14:01–14:02 minute VWAP; exit in the next trading
   session's first continuous minute (09:30–09:31 VWAP).
2. Control `r6_next_close`: same signal/entry; exit next-session final-minute
   VWAP (14:59–15:00), explicitly a closing-price proxy.
3. Control `r6_r7_next_open`: r6 > 0 and r7 > 0; enter during
   14:31–14:32 and exit next-session first-minute VWAP.

Using the completed signal's own closing price as the entry is not executable
evidence. Likewise next-session official open is not assumed achievable from
an arbitrary minute bar. The latency/VWAP version is a conservative adaptation;
it does not claim numerical equality with the video's results.

## Capital, Inventory and Execution

- Initial capital CNY 100,000; cash interest zero; no shorting or leverage.
- One instrument/position, shares rounded to lots of 100 at entry. Entries use
  available cash only, allowing for fees; never recycle unsettled holdings.
- Exit no earlier than the following exchange trading date. Net proceeds from
  a completed sale may be reinvested later that date.
- Existing positions block new entries. The next-close control cannot open a
  second full-capital position at 14:00 before its old position exits at 15:00.
- Modeled fills capped at 1% of the execution minute's share volume. Partial
  or missing exits remain inventory and retry the same prescribed exit window
  on later trading dates; record delays and blocked entries rather than using
  hindsight to remove trades.
- Daily NAV marked at the actual close with an initial-capital peak included
  in drawdowns. Unclosed inventory stays marked, not fictionally liquidated.
- Corporate actions accrue only to eligible shares; payment-date cash cannot
  finance earlier entries. Raw adjusted-price substitutions are not allowed.

## Fixed Costs and Baselines

Primary modeled costs, not a verified brokerage quote: commission 2.5bp per
side (minimum CNY 5 per order), plus 2.5bp adverse slippage per side. Nominal
round trip 10bp before minimum-fee effects. ETF stamp duty is not invented as
stock stamp duty. All fees, slippage and partial fills are reported.

Cost controls keep commission/minimum unchanged and set per-side slippage
0.5bp (nominal 6bp round trip) or 7.5bp (20bp round trip). A separate idealized
video-cost control uses 0.5bp commission per side, no minimum or slippage
(1bp round trip); clearly distinguish it from realistic cost evidence.

Each candidate has its own always-enter matched-window benchmark with the same
entry/exit proxies, costs, capital, capacity, lots and inventory constraints.
Also report full-session ETF buy-and-hold separately. Differences in cumulative
returns are percentage points, not relative wealth returns. Decompose trade
returns into entry-to-close and overnight components where data permit.

## Results, Data Access and Gates

Report every candidate and cost case: cumulative/annualized net return,
Sharpe (252 sessions/year, risk-free zero), maximum drawdown, matched benchmark,
trade counts, exposure, turnover, fees, slippage, capacity/delay diagnostics and
all fixed rolling windows. Do not promote the highest OOS result.

Try the subscribed Tushare `etf_mins` endpoint, independently of the existing
CSI300 `stk_mins` backfill; do not consume that stock-minute quota or buy a new
subscription without approval. If permissions/data are inadequate, preserve the
exact blocker, publish a data-blocked report and keep the tested engine ready
for an approved export. Never manufacture market returns from synthetic tests,
coarse bars, daily prices or the video's reported performance.

An optional public Eastmoney `trends2/get` adapter is explicitly limited to
at most five recent sessions. Preserve its raw response, map the eight declared
fields, convert lots to shares, independently verify the SSE calendar and
validate minute amount/volume against OHLC. A successful quote probe alone is
not a usable or validated dataset. Public display data has no assumed
validation-grade licence; transport/schema failures are blockers, not a reason
to fabricate prices. The provider's cumulative average-price field is not a
per-minute VWAP and must never be used as one.

## Reproducible Commands

Run in the `deepstock` environment, without any TWS connection:

The commands below are a resume-time reference, **not instructions to run
while paused**. In the current registry state they return `paused_missing_data`
without any network/data request or backtest.

```bash
conda run -n deepstock python scripts/cn/download_etf_tail_minutes.py --probe-only \
  --from 2025-08-18 --to 2025-08-19
conda run -n deepstock python scripts/cn/download_etf_tail_minutes.py
conda run -n deepstock python scripts/cn/run_etf_tail_momentum.py

# Optional recent-data pilot; never replaces long-history validation
conda run -n deepstock python scripts/cn/download_etf_tail_minutes.py \
  --provider eastmoney_recent --output-dir artifacts/research/cn-etf-tail-momentum/pilot-data
conda run -n deepstock python scripts/cn/run_etf_tail_momentum.py \
  --input-dir artifacts/research/cn-etf-tail-momentum/pilot-data
```

The root-level `run_cn_etf_tail_momentum.py` and
`download_cn_etf_tail_minutes.py` remain compatibility entry points. Missing or
invalid market input produces `blocked_data`, empty performance cases and a
readable report. Unit-test fixtures are never published as market results.
