# Auction daily-state interaction study

October25 owner approved the useful notebook hypotheses, selected existing data
and offline comparison plus independent prospective observation, then requested
implementation. This is a candidate under `csi300_opening_auction`, not a
replacement for `auction_context_v1_frozen_20260829` or an order permission.

## Fixed hypothesis

`config/auction_state_strength_v1.json` pins `auction_state_strength_v1`:

- A: the existing13 context features.
- B: A plus `previous_range_cs`, `volume_ratio_20d_cs`,
  `previous_close_position_cs`, `auction_gap_cs`, and
  `auction_gap_cs * auction_volume_log_ratio_cs`.
- C: B plus each of those three daily states multiplied by the gap-volume term.

Daily states are lagged; products use existing PIT cross-sectional inputs and
training-only Ridge scaling. Missing values are not zero-filled. A/B/C share
decision and training availability; A_full is a historical-only full-sample
baseline diagnostic. Daily proxies are not repeated limit-up breaks. No video
thresholds, feature search or assumed positive coefficient is allowed. Exact
cancellations and theme-leader trades remain deferred; fixed main-player-cycle
claims are rejected. The two weak-to-strong notes are one hypothesis. Original
files stay untracked in notebook; hashes, original text, assessment and explicit
approved_for_test decision are retained privately.

## Inputs and ledgers

Ignored `artifacts/auction_state_strength_v1/` has an independent SQLite.
`activation.json` freezes config hash, feature sets, source hashes, note ID and
actual timestamp before replay. Its enabled switch controls new signals only;
existing study holdings still settle when disabled. Neither the application
schema nor original forward schema changes. Use SQLite backup APIs for backups.

The default prediction may expose `PreparedAuctionInputs` to a callback without
changing numerical baseline behavior. The original signal is persisted first;
the study consumes that same frame, observation time, versions and health
context. The first frame is saved with SHA256;09:29 retries reuse it without
fetching again. Decisions require actual09:25–09:30 timestamps after
preregistration. Persistence crossing the cutoff is retained solely as late
diagnostic evidence, cannot fill, and is excluded from forward statistics.
Study failures never invalidate a completed original signal.

Existing fill combines pending symbols, prioritizes original orders, and shares
the existing50-symbol batch budget. Both ledgers consume the first stable09:31
snapshot. Symbols beyond that budget remain missing, not guessed fills.
Settlement obtains the date union once and shares responses between ledgers.
Keep industry2/Top5, dynamic confidence thresholds,100-share lots,5% minute
capacity, T+1/limit-down deferral and20bp costs. Report50bp stress. All study
records are diagnostic/actionable=false. Baseline health is context, not proof
B/C is executable. No new cron or mail task is installed.

## Replay and review

Run in the Deepstock environment:

```bash
python -m scripts.auction_state_study init --note-id <approved-note-id>
python -m scripts.auction_state_study backtest
python -m scripts.auction_state_study report
python -m scripts.auction_state_study disable
python -m scripts.auction_state_study enable
```

Replay uses registered retained inputs only, with provider calls disabled.
June3–August26 stays fixed. Each run retains start-time code hashes/dirty state,
upstream versions, coverage and missing dates. Legacy minute-cache timing
inherits its previously accepted source contract; absent original timestamp
columns must not be called newly observed or independently revalidated.
Unknown entry quotes and incomplete coverage are not cash returns. Future
labels never decide who is ranked; only already-realized labels train.
Historical evidence is development diagnostic, never the original policy OOS.

The first replay has56 common completed dates; June22–25 fail95% auction
coverage.20bp net mean cohort returns: A−0.1227%, B−0.2984%, C−0.3044%; C−A
is−0.1817 percentage points/day, C−B−0.0060.50bp stress also worsens. The
incomplete interval does not establish a benefit. Do not tune this version on
those results or replace the original strategy. The separately approved future
diagnostic remains an observation, not adoption of the features.

Forward starts with actual post-deployment decisions. Reports compare C−A/C−B
on common completed dates, include cash, distinguish unknown quotes/open
holdings/capacity misses, and show20/50bp cohort P&L. Cohort compounding is not
account NAV and is not annualized.60 common completed dates allow first manual
review only; independent health/universe-spread and sample adequacy still need
review. No automatic promotion or broker orders.

Deploy server tests → GitHub → clean quantitative ff-only pull → native tests.
Code/docs and aggregates may travel; private ledgers/provider rows/original
notebooks/credentials stay local.
