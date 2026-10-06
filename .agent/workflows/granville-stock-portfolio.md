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
If a held security ends and verified terminal proceeds are unavailable, block
the result rather than sell at a fictional last/zero price or omit the stock.

Historical sector classifications are not available: no sector cap is claimed,
and this remains a research/paper blocker. Future execution work also needs
raw-share corporate actions, taxes, settlements and verified terminal handling.
All freezes and order permissions elsewhere remain unchanged.

CLI: `python -m scripts.run_granville_portfolio --market US|CN --data-dir ...`.
Dataset collection: `python scripts/prepare_granville_stock_data.py --market ...`.
Server edit/test → GitHub → clean quantitative-computer pull is mandatory.
