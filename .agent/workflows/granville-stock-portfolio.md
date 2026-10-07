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
