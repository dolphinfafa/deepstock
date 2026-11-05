# QMT VIP read-only evaluation — November 5, 2026

The owner purchased VIP and supplied a token to evaluate replacing Tushare
market data. Authorization covers bounded read-only acquisition, not production
cutover, new scheduled jobs, strategy changes or broker orders. The token lives
only in Windows' ignored `.env` as `QMT_VIP_TOKEN`, with a prior local backup.
Never copy its value into source, reports, command lines or chat.

## Environment and retained evidence

The quantitative node `DESKTOP-ORNLESD` uses project `D:\workspace\deepstock`
and dedicated Deepstock Python3.12.14. No running QMT GUI was found; the main
environment had no installed `xtquant`. No main-environment dependencies were
installed or replaced. The official PyPI wheel250807.1.2 was extracted into
ignored `artifacts/qmt-vip/sdk-250807.1.2`, loaded only in probe subprocesses.
Wheel size34,370,142bytes; SHA256:
`91f19ff9a92971c5abe64fbd077e5212e0418f0820aa3427aef3444230f72921`.

Three-stock capture: `artifacts/qmt-vip/20261105T041546Z-smoke`.
Main300-stock capture: `artifacts/qmt-vip/20261105T041859Z-batch`.
Native-local directories retain provider-returned DataFrame snapshots, progress
metadata, probe code, summaries and SHA256 manifests. They are diagnostic
evidence, not registered strategy inputs or network-wire recordings. QMT market
rows stay on Windows; only aggregates return to the server.

Data center initializes SH/SZ with `start_local_service=False`, then explicitly
listens on127.0.0.1:58611. Never use `listen()` defaults, which bind0.0.0.0.
No `xttrader` import or broker connection was used.

## Measured results

The300-stock universe uses the existing20260831PIT snapshot, the latest before
requested date20260923. Window09:30–09:32China; unadjusted, `fill_data=False`,
no price padding. A fresh private data-home separates first download from cache.

| Operation | Result |
| --- | --- |
| Token login / initialization | Success; main capture9.52seconds; VIP SH/SZ stations |
| First300-stock minute download | 16.57seconds;300/300progress completed |
| First read, quality checks and JSON export | 1.86seconds; not a pure read timing |
|10cached batch reads | Median0.1233seconds; P950.1319seconds;894rows each time |
| Exact09:31valid activity/OHLC | 298/300stocks,99.33%; all298VWAPs within bar range after volume×100 |
| Three-stock historical tick download | 1.78seconds;571rows09:15–09:32 |
| Pre09:25historical indicative quotes | 447rows, five-level bid arrays |
| VIP `limitupperformance` | Three positive `openVol` rows;0.20second download |
| Eight-second whole-quote subscription | Accepted; two callbacks covering three stocks |

Two missing minute codes,601059.SH/601238.SH, also have invalid activity in the
retained Tushare capture. No QMT rows were fabricated. Subscription source
times11:30:00–11:30:02China preceded this lunchtime test; subscription access
is verified, trading-hour latency/recovery is not. `get_full_tick` before
subscribing returned empty; the snapshot path after subscription was not tested.
`get_period_list()` omitted tick/limitupperformance although actual calls worked;
do not use that list alone to infer entitlement.

## Compatibility and boundary differences

Offline comparison used the hash-verified pinned Tushare dataset
`bbad366c62535571ca817a791cf0d8d677f3b591a6191452d5647815068784e8`.
No Tushare call or restricted quota was consumed. It contains300rows each at
09:30and09:31; both providers yield exactly the same298valid codes.

Matching timestamps do not imply equal bars: only83/298volumes and80/298amounts
match within float tolerance. Median absolute relative volume difference is
0.998%,95th percentile24.55%. VWAP agrees within1bp for188/298 and5bp for273/298;
maximum relative difference30.54bp. These are cross-source differences, not
errors established against exchange ground truth.

Saved000001.SZ ticks reconstruct QMT09:31volume/amount from cumulative
observations strictly before09:31 minus strictly before09:30. Tushare matches
the inclusive endpoints instead. This supports `[09:30,09:31)` versus
`(09:30,09:31]` for that sample. The other two tick samples have no turnover at
the boundaries and cannot distinguish conventions; small volume rounding also
remains. Do not claim this explains every discrepancy, shift all timestamps,
or add auction volume to force equality. QMT09:30+09:31does not reproduce
Tushare09:31for any of the298stocks.

Each sampled `openVol` equals its post09:25tick cumulative volume. However,
`limitupperformance.time` is a midnight daily label, with fields such as closing
auction volume computed later. It does not prove availability before09:30.
Pre-open tick `lastPrice` is zero in these samples while bid1 is positive;
indicative-price semantics need separate mapping. Historical quote snapshots
are not proof of Level2逐笔委托/成交, unmatched quantity semantics or complete
auction order flow.

The existing Tushare normalizer separately compares timezone-aware cleaned
timestamps against a naive09:31target. Prior saved-capture diagnosis found
valid rows can be discarded. Buying QMT does not fix that local bug; this
evaluation leaves it as a separate repair and preserves old labels, completion
counts, frozen backtests and prospective signals.

## SDK lifecycle and transport limitations

250807.1.2 is the latest PyPI release checked onNovember5. Its Python
`xtdatacenter.shutdown()` calls a missing native `datacenter.shutdown` attribute.
The smoke worker exited with Windows3221226505 (`0xC0000409`); the main batch
also returned that code after saving its capture, including when requesting
immediate process exit. Data acquisition succeeded; worker lifecycle did not
pass. Do not call this a clean production worker. No probe process remained.

The first smoke had an additional temporary probe serialization error:
`download_history_data2` returns per-stock metadata containing datetimes,
despite its bool docstring. The corrected probe separates aggregate metadata
from progress serialization. This was a probe issue, not a token denial or
proof of download failure. Native logs were checked; none contained the token.

SSH once failed with `Connection closed by UNKNOWN port 65535` before the
first batch created a remote directory. Jump-host/Windows connectivity and
absence of that remote run were checked before starting the successful batch.
This transport interruption is separate from QMT reliability. If SSH loses a
result, recover saved state/check workers before repeating any native login.

## Recommendation and remaining conditions

The owner subsequently said to continue. The read-only adapter is implemented
as `deepstock.data.qmt`, `qmt_runtime`, `qmt_worker` and manual
`scripts/capture_qmt_minutes.py`. It has a single OS-owned token lock, a hard
deadline and a Windows kill-on-close Job Object. A worker cannot import the
native SDK before assignment to that Job Object and an explicit parent start
gate. It disconnects its RPC client, atomically writes a request-bound receipt,
then waits for parent termination. The parent verifies every evidence hash,
uses TerminateProcess and verifies that the local listener is released. Worker
exit1 is the expected supervised termination, not a repaired vendor shutdown.
Missing/corrupt receipts, premature native exit, deadline or Job failure block;
no automatic login/download retry is made. Native verification follows the
server test → GitHub → clean Windows pull sequence; see the dated work log.

The wheel and all126original SDK files are rechecked before capture; unexpected
Python/native executable files are rejected. Only the child imports xtquant.
Captured unadjusted minute CSV is immutable, then registered through DataStore
with `normalizer=qmt-native-minute-v1`, explicit units/boundary, private previews
and `strategy_input_approved=false`.09:30observations stay separately labelled
`opening_auction`, with no invented continuous-bar interval. Tick/subscribed
snapshot JSON is registered as evidence, not an executable input. Raw provider
returns remain unchanged; unit normalization belongs to the cleaner.

The first three real tick samples have entirely zero `pvolume`, including rows
with positive `volume`; raw shares are unavailable in that SDK response. The
diagnostic uses the documented lots×100conversion and explicitly marks the
share count as rounded. Exact volume agreement and agreement within a declared
100-share cumulative-difference rounding bound are reported separately; the
bound was specified before expanded acquisition. A nonzero but inconsistent
raw-share field blocks instead of silently changing units. Snapshot VWAP is
diagnostic only; no intraminute OHLC is reconstructed from coarse snapshots.

`scripts/audit_qmt_boundaries.py` performs offline reconciliation against the
hash-verified pinned Tushare reference handoff, with fixed1bp/5bp VWAP thresholds
and amount tolerance max(1CNY,1e-7relative). Diagnostic samples deliberately
include known discrepancies and cannot estimate representative source accuracy.
No CLI is wired into the auction scheduler or source selector.

The raw300-stock supervised capture completed with894minute rows and642tick
rows for the28frozen diagnostic symbols. Controlled stop returned the expected
worker code1 and released the listener. Registration correctly quarantined16
whole-capture observations for nominal VWAP/OHLC disagreement:15opening-auction
rows and one09:32row. All16are compatible with the previously declared100-share
rounding interval, but remain quarantined under the original price test.
No threshold was relaxed. All298exact09:31bars passed the same test.

Cleaning also supports the explicit `minute_window=exact_0931_v1` contract,
requiring the requested session. It retains unknown/cross-session timestamps
as blocking errors, selects the exact minute, records out-of-scope exclusions
and preserves original row numbers. Full-capture and exact-minute views get
different immutable versions over the same retained raw bytes. The full view's
blocked status and older diagnostic reports are preserved; the target view
does not authorize strategy use or redefine a native bar as a Tushare bar.
Capture and normalization code commits are recorded separately for recovery.

Expanded offline comparison passed28/28amount checks and28/28VWAP differences
within1bp using right-closed snapshot endpoints;17share counts match exactly,
all28are within the declared lot-rounding bound. Native left-closed endpoints
pass only2amount checks and3VWAP checks within1bp. This is strong evidence of
boundary mismatch for the deliberately selected discrepancies, not full OHLC
equivalence or representative accuracy. Receipt hashes/reference provenance
were verified and no new Tushare request was made.

Actual-native fault tests verified a15-second hard deadline after minute data
was captured, and termination of the supervisor while the SDK was active.
Both released the local listener; parent-death also released the token lock.
The interrupted runs remain private evidence, not completed datasets. See the
work log for release/restart and live-quote validation; one-day tests still do
not establish3–5trading-day stability.

Prioritize historical/subscribed live minutes for a QMT adapter. The measured
throughput avoids the existing Tushare `stk_mins`2/day bottleneck. No same-machine
equal-sized Tushare network timing was measured, so no speed-multiple claim is
supported. Cache timings are not network timings. One date and ten cache reads
do not establish long-term stability.

Before production use: resolve native-worker lifecycle with a reproducible
supervised exit; specify/version minute boundaries and share/hand conversion;
expand tick reconciliation; observe3–5trading days of freshness, completeness
and disconnect recovery. Official tokens permit a single login: one native
data service should serve local clients. Do not install timers or change
production sources under this evaluation authorization.

Retain Tushare PIT membership/calendar, daily/corporate actions, suspensions and
limits until individual replacement contracts pass. Keep CNINFO/SSE/SZSE as
announcement sources. Auction≥95%, cross-section≥240, exact09:31≥90%, cutoff,
cost and research-only order gates remain. Probe99.33%coverage is not strategy
admission. Official VIP limits describe300instruments, up to five quote levels,
three years of minutes and one year of tick history; only the dates above were
tested, not full-history/account-wide entitlements.

References:

- https://dict.thinktrader.net/dictionary/
- https://dict.thinktrader.net/dictionary/stock.html
- https://dict.thinktrader.net/dictionary/question_answer.html
- https://dict.thinktrader.net/nativeApi/xtdata.html
- https://pypi.org/pypi/xtquant/250807.1.2/json
