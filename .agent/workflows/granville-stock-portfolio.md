# Independent Granville Stock Portfolio

User approved separate US/A-share individual-stock portfolio tests on October 6.
`granville_stock_portfolio` is a new Both research candidate, not an ARC route
and not a rewrite of `granville_ma_swing` ETF evidence.

Before performance, `config/granville_portfolio_v1.json` fixes all rules. Reuse
the ETF v1 three entry families (20/60/200 MA and ATR14), unchanged confirmation,
trend, stop and cooldown rules. Compare `time_7` (original close-day seven
time exit) and `trend_only` (no fixed time exit, same risk/signal exits). Ordinary
signal exits still need three held sessions. Primary display is fixed to
trend_pullback/time_7. All six pairs and base/10bp-slip stress remain reported.

Maximum five positions; each new position targets 16% of opening account NAV,
entry gross ceiling 80% and entry single-name ceiling 20%. These are entry caps,
not daily forced rebalancing; price drift is separately reported. No leverage,
shorts, daily ranking replacement or pyramiding. Sell intents execute first at
a later legal open; only actually freed slots/cash are then available to ranked
prior-close candidates. A blocked sell cannot finance another purchase. No
same-symbol same-open exit/reentry; three-session per-symbol cooldown.

Select by previous close's 126-session relative total-return strength against
the local ETF; deterministic ticker tie-break, no outcome/winner selection.
Prior 20-session average turnover >= USD10m / CNY100m; prior-session volume
participation <=1%. US S&P500 historical eligibility; CN latest completed
monthly CSI300 snapshot, never a future month or today's survivor list. The
monthly snapshot is a lagged historical proxy, not proof of exact daily index
membership or first-seen publication time. All historical constituents in the
requested sample are included. Stocks with inadequate history simply cannot
signal; missing executable quotes are not fabricated.

Common evaluation 2025-01-02–2026-09-29, source warm-up from 2024-01-02. Data
coverage determines per-market exchange sessions, not cherry-picked start/end.
2026-01-02 split is retrospective diagnostic only. Fixed 504/252/252 rolling
ledger slices are reported if long enough; do not invent windows for this short
sample. No claimed long-history robustness or genuinely unseen prospective OOS.

## Data, accounting and limitations

Norgate runs only on the quantitative computer's dedicated Deepstock Python.
Raw NONE and TOTALRETURN OHLC plus historical membership are retained/registered;
licensed bars never uploaded. Tushare daily/factors/actual limit prices,
suspension declarations and stock dividends are captured on the server,
without minute endpoints or changing auction quotas/schedules.

This stage tests **analytical economic total-return units**, not a historical
broker-share/cash-dividend payment/reinvestment reconstruction. Raw OHLC gives
entry-lot reference (US 1 share, CN 100 shares), volume capacity and CN actual
limit-open checks. After provider adjustments, economic units must not be
mislabelled broker-executable shares. Do not add explicit dividend cash again
to adjusted prices. CN deducts a fixed conservative 20% of held cash dividends
on ex-date, not actual holding-period-dependent deferred tax settlement; US
investor-specific withholding/capital-gain taxes are not modeled. Cash yield 0.
No raw-share, cash-settlement, opening-auction or real-account replay claim.

US: 2.5bp commission, minimum USD1, 2.5bp slip. CN: 3bp commission, minimum
CNY5, 5bp slip, 0.1bp transfer both sides, 5bp stamp duty on stock sales
(post-2023 rule, not the ETF exemption). Both retain 10bp-slip stress.
CN T+1 excludes same-day exits. Suspensions/limit opens delay fills; prior
volume caps allow partial exits and preserve intent. Missing internal prices
need explicit suspension evidence; stale marks are flagged, never executable.
US sparse quotes strictly before both first historical index eligibility and
evaluation are retained as audited NaN warm-up, not filled or discarded. Rolling
readiness cannot resume until a full continuous history exists. Evaluation or
already-eligible unexplained gaps still block; no security is removed.
Stock action entrance logs factor quantization and checks the supplied ex-date
reference. A single nominal entitlement with a small diluted-reference residual
(at most 0.5% of preceding raw close) is retained with an explicit audit warning,
not replaced by an inferred amount. This is a diagnostic allowance, not proof
of real-share execution accounting. Multiple/aggregate disclosures still need
one uniquely corroborated total; equal implementation revisions within the
same fiscal period collapse, not separate periods. Incomplete or non-unique
nominal payouts remain explicit unknowns and block any case held through the
event, not unrelated/unheld portfolios (including the all-cash warm-up).
Unspecified stock-only cash
remains null and blocks any case genuinely held through its ex-date; no fake
zero cash/tax is created. Norgate None terminal dates mean still-listed names.
Small source factor revisions confined to the all-cash warm-up, with unchanged
raw ex-reference, absolute step <=0.001 and relative step <=0.1%, remain in the
unchanged prices and are explicitly audited. Unexplained evaluation-period
steps still block. This does not manufacture an action or repair a price.
If a held security ends and verified terminal proceeds are unavailable, block
the result rather than sell at a fictional last/zero price or omit the stock.

Historical sector classifications are not available: no sector cap is claimed,
and this remains a research/paper blocker. Future execution work also needs
raw-share corporate actions, taxes, settlements and verified terminal handling.
All freezes and order permissions elsewhere remain unchanged.

CLI: `python -m scripts.run_granville_portfolio --market US|CN --data-dir ...`.
Supply `--benchmark` with the existing US SPY file / CN ETF source directory.
To combine summaries only, use `--us-summary ... --cn-summary ...`; registered
licensed rows never leave Windows. Case failures never change the principal.
Dataset collection: `python scripts/prepare_granville_stock_data.py --market ...`.
Server edit/test → GitHub → clean quantitative-computer pull is mandatory.

## October 6 results and remaining gates

Immutable publication: `artifacts/research/granville-stocks/20261006-fixed-v1/`.
US ran on the quantitative computer, CN on the server; both started from clean
`375c4d3` with source snapshots and runtime versions. The Windows runtime is
3.12.14 versus server 3.12.13, explicitly retained. Raw config byte hashes differ
under Windows CRLF; full parsed contracts must match and both raw hashes plus a
semantic configuration hash are retained. This is not permission to change rules.

535 US historical members / 338 CN monthly historical members; evaluation has
436 / 423 respective sessions. All 24 fixed cases retained: 22 completed and
2 blocked. US trend_pullback/trend_only, both cost cases, held WBA-202508 into
August 28, 2025; verified full terminal proceeds are missing. No fictional sale,
stock removal or substitute principal. The time_7 principal completes.

| Base cost | US cumulative / CAGR / MDD | CN cumulative / CAGR / MDD |
| --- | --- | --- |
| ma_cross / time_7 | -9.99% / -5.90% / -17.21% | +11.07% / +6.45% / -13.93% |
| ma_cross / trend_only | -4.36% / -2.54% / -18.58% | +9.40% / +5.50% / -17.80% |
| trend_pullback / time_7 (principal) | +22.26% / +12.32% / -17.89% | -10.48% / -6.38% / -28.36% |
| trend_pullback / trend_only | blocked — WBA proceeds | +13.02% / +7.56% / -26.41% |
| deviation_reversal / time_7 | -7.66% / -4.50% / -25.70% | +0.37% / +0.22% / -19.76% |
| deviation_reversal / trend_only | -8.04% / -4.73% / -28.73% | +4.84% / +2.86% / -20.29% |
| local ETF 100% buy/hold | +32.14% / +17.48% / -18.75% | +14.97% / +8.67% / -12.71% |

Principal average exposure 75.24% US / 69.81% CN; annualized round-trip turnover
31.74 / 30.00. US 10bp-slip stress cuts principal cumulative return from 22.26%
to 4.66%, not an isolated brokerage-fee deduction: costs can change lot/cash
paths under the same rules. No high-return alternative is promoted. Even the
80%-initial SPY benchmark returns 25.71% with -15.12% MDD, stronger than the
principal. CN shows no gain from promoting this stock-rotation principal.

Data audit retains SW's 82 sparse pre-index warm-up dates as NaN; evaluated US
sessions are complete. CN retains 103 small unchanged-reference factor moves
confined to inactive warm-up; no adjusted price is repaired. Implementation PDF
1220848913 independently verifies 601898's 2024-08-20 total cash dividend 0.555
(annual 0.442 + special 0.113), which Tushare listed incompletely. Public PDF,
registered source/derived evidence and hashes remain local ignored artifacts.
300803 cash for two reported stock-only events remains unspecified; none of the
completed cases was held through those dates. A genuinely held case must block.

There are zero full 504+252 rolling windows: sample is too short. No forward OOS,
historical sector cap or real-share settlement claim. Next: verified WBA cash/
contingent-right/settlement evidence; longer point-in-time OHLCV; action, industry,
tax/settlement accounting; new predeclared cost/holding diagnostics rather than
selecting a winner from these results. No Paper, Live or new observer schedule.

## Fixed next experiment: entry episodes v2

User removed the independent ETF MA-swing strategy and approved portfolio
optimization. ETF registrations/pages are retired, not shared helpers or past
data; v1 `parent_strategy` is immutable historical lineage, not a live dependency.

Before new performance, `config/granville_portfolio_episodes_v2.json` registers
one change only for trend_pullback/time_7: compare `signal_level` versus
`once_per_episode`, both original costs. A continuous true run of the unchanged
close signal is an episode. A false close re-arms the next true run; at most
one successful buy per stock per episode. Warm-up episodes are counted, and
unfilled/capacity/slot-blocked opportunities do not consume eligibility. This
proxy is not proof of a distinct new physical pullback. Original cooldown,
limits, stops, next-open fills, rankings, sizes and 7-day exits are unchanged.

Each market retains four cases (eight total); all original 24 cases/blocks are
also retained. Before comparing the new rule, independently replay base/stress
baseline and compare every original daily field and trade against SHA-verified
immutable ledgers. Do not promote a winner. All history was already seen;
this is neither forward OOS nor new full walk-forward evidence.

CLI: `python -m scripts.run_granville_optimization --market US|CN --data-dir ...
--baseline-dir ... --output-dir ...`. It reuses all registered input versions,
never downloads or rewrites dataset manifests. Two summaries can be published
with `--us-summary ... --cn-summary ...` into a fresh ignored directory under
`artifacts/research/granville-stock-optimizations/`; licensed US bars stay local.
Cross-node contracts compare complete parsed baseline and experiment configs.

Diagnostics: completed-position exit reasons/holding and realized PnL (includes
fill slippage/charges, does not allocate dividend tax); repeated successful
entries in the same episode; cash costs separately by fees/slip/dividend tax;
stress-minus-base net change = changed-path marked gross PnL minus extra direct
cost. Direct cash costs divided by initial capital are not compounded return
drag, and this identity is not a tradable zero-cost/reinvestment counterfactual.
Final open holdings and partial/deferred fills remain, never fake liquidation.

Later experiments (not stacked in v2): risk-normalized sizing, verified
historical sector/correlation constraints, low-frequency market filtering, and
longer point-in-time history/verified terminal accounting. Preserve US/CN
separation, freezes, observation writers and all order gates.

### v2 result — retain the negative finding

Run code `8ba2b27`, clean on both nodes; Python3.12.13 server / 3.12.14 Windows.
All eight new cases completed. Baseline daily and trade fields independently
match their original SHA-verified base/stress ledgers. Original24 cases and
the two WBA trend-only blocks remain, not reclassified as successful.
Publication: `artifacts/research/granville-stock-optimizations/20261006-entry-episodes-v2/`.

There were zero repeated successful same-episode buys in both markets/costs.
US suppresses one otherwise unused candidate-session; CN suppresses none.
Consequently all return, turnover, exposure and fill results are unchanged:
US base CAGR12.32%, MDD-17.89%, annual turnover31.74; CN base CAGR-6.38%,
MDD-28.36%, turnover30.00. Stress cumulative remains US4.66%, CN-12.48%.
The proposed episode restriction is redundant on this seen sample, not an
improvement and not proof that every broader form of rapid reentry is harmless.

Completed-position diagnostics, fees/slippage included and dividend tax not
allocated: CN time-exit222 positions net realized +CNY119,597, stop56 -83,379,
signal80 -45,412. US time223 +USD142,439, stop34 -61,590, signal92 -58,585.
This conditional grouping is NOT a counterfactual for holding those winners
longer or disabling stops. It motivates studying failed entries and risk
concentration as well as exits, not automatically extending all holds.

US stress-minus-base cumulative -17.6023pp cash-identity decomposes to extra
direct cost7.7868pp and changed-path marked gross PnL-9.8155pp. CN -2.0043pp
decomposes to extra direct cost4.9393pp and changed-path gross PnL+2.9350pp.
These are initial-capital cash ratios at actual fills, not gross strategy CAGR
or evidence that a zero-fee strategy could realize the added-back costs.

Both nodes passed244 tests and frontend production build passed. No winner
promotion, new observer task, Paper or live permission. Future one-dimension
experiments need their own fixed spec; do not silently tune v2 after this null
result. Remaining terminal/history/industry/settlement gates are unchanged.

## October 7 fixed US-only sizing v3

config/granville_us_sizing_v3.json preregisters only risk shrink: target16% ×
min(1, point-in-time eligible pool median20-session adjusted-return volatility /
stock volatility), ddof0, immediately preceding close. Median uses historical
membership, ready indicators, original liquidity gate and finite positive vol;
not only entry signals or chosen names. Invalid/zero volatility blocks and is
logged. Low-volatility names never exceed16%; residual stays zero-yield cash.
No daily rebalancing/adds; original5 slots/80% entry cap/ranks/signals/time_7/
stops/cooldown/costs unchanged. Same535 pool/registered versions/warm-up/evaluation.

Four US cases: fixed16% / shrink × base / stress. Both baseline ledger hashes
and every old daily/trade field must reproduce before comparisons. Licensed bars
and full ledgers stay Windows. Original24/v2 cases remain. CN historical evidence
is preserved under Both and explicitly not rerun. API extends
market_results.US.sizing_experiment; original headline never promoted by result.
No migration, genuinely unseen OOS, complete504+252 window or Paper/Live claim.

CLI: python -m scripts.run_granville_us_sizing --data-dir ... --baseline-dir ...
--output-dir ...; server publication uses --us-summary and --history-publication.
Run from clean committed source, pin source/config/input fingerprints before and
after. Separate easy-tdx CN audit is not a data source for this experiment.

October7 implementation/verification is ready but real US v3 results are pending:
the quantitative computer's20008 relay listener was absent and three SSH checks
returned connection refused. No Windows pull/test or licensed-data experiment
occurred, no alternative-node/synthetic substitution, no new outcome published.
Restore the quantitative computer/FRPC, then clean pull, local tests, four-case
runner and summary-only publication. Keep old US/CN/v2 evidence visible.
Catalog as_of remains October6 (latest completed evidence), while October7
planning appears in progress; a newer empty plan must not displace real metrics.

### v3 result after quantitative-node recovery

The20008 tunnel recovered. DESKTOP-ORNLESD/admin had a clean worktree; ff-only
pull reached fa1f84c and dedicated Python3.12.14 passed265 tests (75.77s).
Four real US cases completed from clean fa1f84c, unchanged535-name inputs and
436 evaluation sessions. Both base/stress baseline daily and trade fields match
their original SHA-verified ledgers. No invalid-volatility candidate was admitted;
maximum entry target remains16%. No CN rerun or result-based parameter change.

| US sizing / cost | Cumulative | CAGR | MDD | Sharpe | Annual turnover | Mean exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed16% / base | 22.26% | 12.32% | -17.89% | 0.58 | 31.74 | 75.24% |
| fixed16% / stress | 4.66% | 2.67% | -18.40% | 0.23 | 31.83 | 75.25% |
| shrink only / base | 6.77% | 3.86% | -13.56% | 0.33 | 22.67 | 53.58% |
| shrink only / stress | -3.06% | -1.78% | -14.17% | -0.05 | 22.76 | 53.69% |

Mean cash rises from24.76% to46.42% in base; direct cost/initial capital falls
from5.65% to3.95%, but this is an amount ratio, not compounded return drag.
Buy fills remain349 base /350 stress under both sizing policies. Lower exposure
and turnover do not imply fewer entry/exit events. The smaller drawdown comes
with substantially less return and lower Sharpe: this seen-sample diagnostic
does not establish a better strategy. Neither case beats SPY's17.48% CAGR;
the80%-initial SPY comparison is not dynamically risk/cash matched.

Windows-only ledger comparison also matches date/symbol/action/reason sequences
between sizing policies at each cost. Base shrinks250 of349 buys; stress251
of350. Base mean target11.39%, median11.92%, minimum2.44%. This sizes the same
observed trade events differently; it does not improve entry or exit timing.

Pre-split2025 base returns are -3.88% fixed / -3.41% shrink; retrospective2026
slice +27.21% / +10.54%. Both slices were already seen, and the latter has only
186 sessions; reference annualization is not genuinely unseen OOS evidence.
Shrink stress-minus-base cumulative -9.8319pp decomposes into additional direct
cost5.6277pp and changed-path gross PnL-4.2042pp using the actual cash identity,
not a tradable zero-cost counterfactual or identical stress/base fills.

Windows full ledgers/source snapshots remain under
artifacts/research/granville-stock-nodes/US-20261007-sizing-v3-restored/.
Only market_summary.json (479298 bytes, SHA256
ef778421041838c2ee55ec51b832583cdb97fdb89759b6209441ff9350a97d91) was copied.
Server immutable publication/report:
artifacts/research/granville-stock-sizing/20261007-risk-shrink-v3-restored/.
Baseline headlines, original24 cases/two WBA blocks, v2 and Both/CN history
remain. The real October7 publication becomes latest; the catalog's historical
October6 placeholder does not create a competing empty October7 run.

Keep research-only. Next requirements remain longer point-in-time history,
verified terminal proceeds, industry and real-share/tax/settlement accounting.
Any new entry/holding/filter change requires a separate fixed experiment, not
post-hoc selection from these four results. No new observer, Paper or Live orders.

Release verification after publication: server266 tests(84.27s), Windows266
tests at f1cf499(49.78s), frontend typecheck/build passed. Latest-display tests
now use isolated SQLite instead of local real artifacts, and cover both planned
versus completed and newly completed versus older evidence. Authenticated
production detail/report match publication; dataset provenance links the new
run, both licensed previews are empty; AHL/tail gates and livefalse/killtrue
remain. Repeat ingestion creates zero additional sizing runs. Results/workflows
were pushed to GitHub and ff-only synchronized; no expensive engine rerun.

## Subsequent October7 correction — US membership coverage invalidates comparison

The preceding v1/v2/v3 outputs and interpretations are historical records, not
current validation conclusions. Readiness inspection found US membership ends
2026-08-21 despite OHLC/calendar ending2026-09-29. Registered input
84993cbfd27161f8e2f498a19fb9a4aa6edb66c4ac81376ac4808fae419de4db,
sourceSHA1c5751bc61af6a200a81339cdbabbc7025c107748948159ed5b5344ba7a9ccce,
contains503 active symbol intervalsAugust21 but0 August24/September29.
Twenty-six evaluation sessions follow its cutoff. Original baseline has20
zero-position sessions startingSeptember1. Unknown future membership was
mistaken for known ineligibility; trade replay verification did not detect it.
US annualized12.32%/3.86% and other original/v2/v3 outputs therefore remain
auditable impaired diagnostics, NOT valid full-period performance or evidence
that sizing improved/failed. CN evidence is independent and unchanged.

Added collection/engine coverage entrance blocks a missing-index tail/internal
date, including the first evaluation open's prior close. Never extend cached
members, use current survivors, fill missing status0 or trim evaluation to hide
this. The necessary positive-presence check does not certify full universe
coverage. Old dataset/manifests/results remain byte-identical. Version-matched
quality notices appear on homepage/detail and a separate correction report;
no new empty ResearchRun displaces the old records. A corrected input needs a
new identity and a new fixed rerun, all original24/v2/v3 retained with warnings.

Long-history inventory: liquidity cache requests2005 onward, declares1301
codes,1040 retained mapping codes,261 no-price failures; all14 mapping chunks
endAugust21. These failures require first/last-quote and overlap verification,
not automatic exclusion or an unsupported claim that they predate the sample.
Cache has adjusted close/volume/turnover, not NONE/TOTALRETURN OHLC. Current
Norgate watchlist count1305 is another reason to refresh complete historical
membership rather than reuse the short-window535 pool over decades. SPY OHLC
exists1993-01-29—2026-09-29,8474 rows, but does not fix constituent inputs.

Official completion source:
https://www.sycamorepartners.com/news-article/sycamore-partners-completes-acquisition-of-walgreens-boots-alliance
confirms August28,2025 closing,11.45USD cash per raw share plus one
non-transferable contingent right capped at3USD from future VillageMD monetization.
The cap is not paid cash or known fair value. Actual cash settlement/right
valuation/payment dates remain unknown; WBA's two historical blocks remain.
Retained raw HTML11816 bytes/SHA
1e448a2af5236fc611f26c1796451492da8af10010b27583f4d3aaf936316e70,
registered public evidence version
b600198f52caba1124edd95d16833f7e9b7812199be3ade750fb6ffe70779ff5.
No terminal sale or cash amount is added to strategy ledgers.

Offline Windows audit CLI: python -m scripts.audit_granville_data_readiness
--data-dir ... --history-dir ... --sizing-dir ... --output <new-summary.json>.
It reads registered data, verifies original daily hashes and publishes only
counts/versions/impacts, no licensed prices or full ledgers. Before long-history
performance, refresh raw daily member coverage and complete historical symbol
list, classify legitimate IPO/terminal boundaries, obtain both OHLC adjustments,
then resolve held terminal cash/rights/settlement. No post-hoc rule changes or
new performance run is authorized by readiness collection itself.

Subsequent native metadata verification classifies all261 no-price failures as
terminal before2005-01-01, latest last quote2004-12-03; unresolved overlaps0.
They are legitimate out-of-range for the2005 onward cache, not evidence of261
missing in-sample prices. Current1305 versus old1301 watchlist contains8 new
codes/4 absent codes (including terminal aliases), not automatically4 new
constituents. Native AAPL membership sample has28 observationsAugust20—
September29; WBA sample in that post-removal range is empty. A sample availability
probe does not certify a complete universe. Formal CLI
--verify-provider-metadata captures raw local security/watchlist metadata with
restricted version/hash and returns aggregate classification only; no bars.

Formal Windows audit at clean0b6e8bb confirms allfour sizing cases have26 tail
sessions,20 zero-position days startingSeptember1, and1 tail buy fill (from the
last known prior close). Audit statusblocked is the expected data conclusion,
not a failed CLI.17 input references, source fingerprint retained; native
security metadata version
ac644a1ba22e7feef206cbe30bdfa6a5f09b312fe311077976f288a69fdda037,
rawSHA25ee524f3fcc32150b9b14fc5e63f19e11b37ef12d003404f05932df29a32e39.
Only13122-byte summary copied to server
artifacts/research/granville-stock-nodes/US-20261007-readiness-v1.json,
SHA2c67433d32d2da603e3d19153c4846dff323b6a6b18f620d396e048f056c2ca5,
both nodes verified. No price downloads or performance reruns occurred.

Both nodes passed276 tests (server83.55s, Windows50.69s), frontend build and
authenticated production warning/report smoke passed. Repeat notice ingestion0;
no new research run displaces performance history. Original v1/v2/v3 hashes
unchanged. Livefalse/killtrue remain. Completion of this audit/gate work does
NOT mean corrected membership/OHLC or full WBA proceeds were obtained. Next:
new immutable complete daily-member/universe capture, then required raw/adjusted
OHLC and a separately identified fixed data-correction rerun. Do not reuse
v3's535-name count as a proof of complete refreshed historical membership.

## Fresh native capture and fixed-period data correction

Continuation first acquires new evidence, not another signal/filter experiment.
`python -m scripts.download_norgate_membership --output-dir <new-folder>` pins
2005-01-01—2026-09-29 and required closes from2024-12-31. It captures the entire
current-and-past watchlist before/after, quote-life/asset metadata and every
in-range code's native unpadded daily0/1 response. All codes, including legitimate
pre2005 terminals, remain in the manifest. Missing dates stay unknown; positive
intervals split at gaps. Required-scope gaps block, while older gaps are reported
without claiming complete long-history membership. Native responses/clean daily
views and the manifest are registered/hash-verified; no licensed rows leave
Windows. AAPL/WBA/SW probes confirmed the native series follows quote dates;
SW's sparse early history means non-observation cannot be guessed as0.

`prepare_granville_stock_data --market US --us-membership-dir <new-capture>
--reuse-us-price-dir <old-export> --output-dir <empty-new-export>` consumes only
the declared capture. It selects all actual signal-close/evaluation members,
never forces535. Existing same-date registered NONE/TOTALRETURN prices can be
reused with their original versions; only newly required codes need bars. Old
membership is never reused or rewritten. This corrects the fixed2025-01-02—
2026-09-29 data scope, not a long-horizon performance study. New data still must
pass stock/terminal entrances. All fixed twelve original US cases remain;
new valid output, if obtained, is separately identified and CN remains historical.
Do not use old v3's535-name/ledger-equivalence requirement against corrected
data; preserve that old experiment unchanged. No altered rules, OOS selection,
Paper/Live orders or scheduler changes. Actual acquisition results are recorded
below only after native execution.

Native capture at clean3686f42 completed all1305 codes, verified261 pre2005
terminals, and returned503 members atSeptember29. No aggregate empty tail
remains, but the stronger all-code gate is still blocked: BIGGQ/SBNY/
YELLQ-202607 have required native dates missing; VYLR lacks verified first/last
quote boundaries. Missing historical observations total12695. All original
responses/daily views remain on Windows under
artifacts/research/norgate/membership-native-20261007-v2/; do not relabel this
as complete PIT evidence. No correction performance has run.

`download_norgate_stock_ohlc_inventory` can explicitly load blocked membership
**for evidence acquisition only**, using its entire frozen list, retaining all
codes and querying NONE/TOTALRETURN with no padding. It does not mark any
strategy input ready or weaken the normal load_capture gate. This permits
parallel completion of price evidence while membership/terminal unknowns
remain. Full licensed rows stay local; only hashes/counts/status leave Windows.

Full OHLC inventory completed at clean9413bfc: all1305 frozen codes processed,
1043 in-range securities/4133033 clean rows,262 provider-verified quote lives
outside the request (261 old terminals + VYLR future IPO),zero acquisition/row
cleaning failures. Historical observed-span missing price sessions12693 remain
unfilled; this is not complete-history certification. Native required coverage
still blocks3 codes/168 security-dates, and WBA full proceeds still unknown.
No portfolio engine or corrected-return publication ran.

Inventory manifest34b0e6f4a55dfb8eded0dffd3bcf3784b67066a35f0a1c4a9981e3d70d719f5f,
SHA8333f1e571a4238eea8f646a1b1fe1cc978512f71ab874c6a0b13d8ad6f7c1dc,
Windowsartifacts/research/norgate/stock-ohlc-native-20261008-v1/.
Only3678 aggregate bytes copied to server
artifacts/research/granville-data-readiness/20261008-native-capture-v1/;
membership summarySHA294bc7a7e477e20e44bb2af063d032b3373ae220946266dba8d05d3d64e65fb9,
OHLC summarySHA6ac6fc73c8c84748b621b61d3314e20472adb37d05e3e18f9e768fbb94312a8d,
both nodes verified. New standalone data-readiness report links only new manifest
versions and cannot displace the latest performance run. Catalog progress
distinguishes inventory completion from blocked strategy admission. Metadata
publication allows bounded no-padding/index/unknown policies, never raw payloads;
`publish_data_catalog --since <aware-ISO-UTC>` limits transfer to recent versions.
Before fixed-rule correction: resolve non-quote-day member semantics/independent
index-event evidence; do not loosen gates merely to produce returns. If external
support coordination or an imputation-policy change is necessary, obtain user
direction first. Complete data collection alone grants no trading permission.

Release40caaf5: server/Windows290 tests passed (86.63s/122.30s),6527 recent
price-free version manifests published. Authenticated production report/progress
smokepassed; new report is standalone,26 original research runs/latest sizing
evidence and original market_results remain unchanged. AHL/tail and livefalse/
killtrue gates remain. No engine rerun, valid new metrics, observer or orders.

## Non-quote-day evidence diagnosis

`python -m scripts.audit_norgate_membership_gaps --capture-dir <native-capture>
--output-dir <new-directory> [--probe-provider]` is evidence acquisition only.
It verifies the retained full capture, rechecks its failed records using the
current lifetime validator, and keeps all original manifest/errors unchanged.
Restricted exact missing-date records stay on Windows; summary counts/hashes
may leave that node. The optional probe requests only canonical S&P500 member
indicators with NONE/ALLMARKETDAYS, retaining both responses and comparing them
to existing observations. No OHLC, index assumption, imputation or backtest runs.
Even identical observed-date values and complete padded gap responses never
prove that a missing day's state is an independent historical observation.
The diagnostic always has backtest_admitted=false and cannot be a new usable
membership capture. Current public package documentation describes repetition
of previous closes for price padding, not a sufficient missing-member guarantee.
Resolve that semantic question with documented provider evidence before changing
an admission policy; sending support requests requires user direction.

Native diagnostic completed at clean c8eb883, package1.0.77. Canonical NONE
responses match retained required-period observations; ALLMARKETDAYS returns
all168 missing security-dates as0, with no overlapping-value differences.
Six raw responses and exact gap lists stay local. No missing-day truth is
certified and neither old nor new diagnostic manifests admit a strategy input.
Price-free summary8470 bytes copied toserver, SHA
15d95da8d718fc16c492398810157cdff7624d9e0bed157f3a94af5989f1129b,
bothnodesverified. Support draft: docs/research/norgate-membership-semantics-request.md,
not sent. WBA closing8-K locator0001193125-25-190603/d87240d8k.htm is confirmed
by SEC submissions metadata, but archive403 prevents content verification;
it is not evidence of full paid proceeds or contingent-right valuation.
New standalone semantics report never replaces impaired performance evidence.
