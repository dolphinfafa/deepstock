# Versioned Data Layers

All strategies/markets are peers. `deepstock.data` owns raw provider evidence,
deterministic cleaning and strict research inputs, outside market packages.

## Storage and contracts

`artifacts/datasets/<version>/` holds immutable raw bytes, clean CSV/GZip or
JSON, manifest and quarantined rows. Versions include source/content, node,
declared contract and cleaning revision. Application SQLite stores version
metadata and research-input relations through Alembic, not large market arrays.
Legacy exports are `legacy_import`; TOTALRETURN is provider-adjusted, not
unadjusted raw. New exports capture provider responses and upstream versions.
`clean-v1.6` also recognises fund adjustment factors and dividend event schemas.
Dividend duplicates require matching entitlement amount, announcement/record/
ex/pay dates; auxiliary disclosure revisions remain in raw evidence and audit.
Unexplained factor magnitudes/splits still block the CN research entrance.
`clean-v1.13` adds stock limits, verified-empty optional suspension responses,
nullable stock dividend plans and fiscal-period disclosure revisions. Same-day
economic conflicts still block. Missing proposal dates require a supplied
implementation date, never an inferred date. Explicit study-period dividend
views retain whole raw responses/upstream IDs and outside-period row counts.
Stock entrances independently reconcile disclosed entitlement totals with raw
close/factor steps, preventing aggregate/repeated-period double counting; an
ambiguous total blocks rather than guessing cash amounts. Different dated
nominal payouts stay alternatives until this audit; blindly taking the latest
can erase an additional special dividend. All existing stock disclosures are
re-audited from retained provider bytes without re-downloading bars.

Cleaning normalises IDs/dates, removes only identical duplicates, audits
numeric/OHLC/activity constraints and missing US sessions against XNYS. Unknown
prices/volumes are never interpolated; conflicting/executable-quote gaps are
quarantined. Auction partial views are explicit and logged, subject to the
fixed coverage/entry gates. Nullable auxiliary features remain null for the
model's training-only imputation. CN minute calendar, precise timestamps, T+1
and corporate-action checks remain mandatory at the strategy entrance.

`read_clean_csv`/`read_clean_json` resolve registered immutable versions and
reject unregistered or changed inputs. `complete_panel` allows only logged
common-inception trimming, never internal missing sessions. CLI reports pin
`data_versions`/`input_exclusions`. Old unbound reports are not relabelled.

## Operation and recovery

```bash
conda run -n deepstock python scripts/clean_existing_data.py
conda run -n deepstock python scripts/rerun_clean_research.py --strategy spy_mean_reversion
# Recover publication only; never repeat a completed expensive engine run.
conda run -n deepstock python scripts/rerun_clean_research.py --strategy stock_turtle --publish-existing artifacts/reclean/<completed-stock-turtle-run>
conda run -n deepstock python scripts/ingest_deepstock_state.py
```

Inventory excludes synthetic fixtures and result/weight files. Unsupported
schemas and mixed observation DBs retain an explicit blocked/evidence-only
status; a DB archive is not a cleaned price panel. Preserve all old data and
reports. Rule changes require a new version. Fixed reruns use unique ignored
directories and never optimise on OOS. Pauses/freezes and order gates remain.
Future reruns capture the starting tracked-code fingerprint and dirty state;
nested controller IDs include the parent run, preventing rerun collisions.
The first migration runs predate start-time capture: their reports cannot be
claimed to reproduce a clean Git commit. Re-publication preserves source-file
hashes and old reports, and never changes the engine result. OOS statistics
must share one daily slice; never combine full-history drawdown with OOS CAGR.
Cost is charged on traded weights (5bp per side here); turnover is half the
sum of bought/sold weights, so cost divided by turnover is not single-side bps.
Cached imports verify retained snapshots again; source changes during capture
are rejected instead of publishing a mismatched version.
Back up application SQLite before migrations; restore backup and matching code
together if needed, never delete market evidence for rollback.

Windows cleans Norgate locally in its dedicated Deepstock environment before
planning/backtesting. `publish_data_catalog.py` publishes metadata/quality only.
Licensed bars remain local. Server edit/test → GitHub → clean Windows pull is
mandatory. `.env`, all artifacts and entire `handoff/` remain Git ignored.

## UI/API and annualization

Authenticated `/data` shows versions, raw/clean paginated previews, hashes,
contract, coverage, quality and related research. APIs: `GET /api/data`,
`GET /api/data/{version}`, `GET /api/data/{version}/preview?layer=raw|clean`.
Previews are limited to 100 rows of registered hash-verified files, never
arbitrary paths. Remote/licensed nodes expose no row preview. Bearer-auth
`POST /api/agent/research/data-versions` accepts bounded metadata, not raw rows.

Every strategy has an annualized-return slot. New evidence uses continuous
cost-after daily returns, including cash sessions, and 252 sessions/year,
retaining scope/dates/count/cost. Less than 252 sessions is reference-only.
Auction cohort compounding is not account NAV and is not annualised. Missing
equity means unavailable; research freezes are not resumed to fabricate metrics.

CSI300 now owns its independent code/data/environment/scheduler under
`strategies/cn/auction`; Darwen is a retained one-time migration source only.
See `csi300-opening-auction.md`. No broker work is part of this migration.

October7 source-audit recovery: generic inventory now honors explicit market
in bounded ancestor manifests, including nested stock-export prices directories.
Older misclassified US/Massive versions/blocks are retained, not edited. Audit
references pin already registered hash-matching CN/Tushare versions directly;
an incorrect generic alias is not evidence that A-share prices failed XNYS.
Stock exports retain original thousand-CNY amount as an auxiliary field beside
canonical CNY turnover; cross-source audits select documented turnover, never
fit a ratio to outcomes. Offline audit recovery validates capture hashes and
makes zero new requests. No old stock manifest or raw price is rewritten.

US stock membership now has a separate necessary coverage entrance:
deepstock.data.membership requires nonempty historical index intervals for
every evaluation/prior-signal close. Empty tail/internal coverage blocks before
signals, never silently forces cash or extends an old interval. Passing this
presence check is NOT proof of full daily membership/publication completeness;
new acquisition must retain raw0/1 dates and exact captured universe/coverage.
Historical readiness failures attach append-only notices by input version,
separate from immutable result/report bytes; corrected versions do not inherit
an unrelated old notice. Official terminal press releases stay evidence-only,
not executable quotes, complete payout valuations or guessed settlement dates.

Native-member acquisition retains an entire frozen current/past historical
watchlist, security lifetimes and raw unpadded daily indicators. Positive spans
split at unknown exchange dates; no missing value becomes0. A necessary aggregate
presence check alone cannot admit a dataset with individual required-date gaps.
Quote lifetimes wholly before/after the request are excluded only with retained
provider metadata (not an empty response). A full OHLC **inventory** may explicitly
read blocked membership for evidence acquisition without relaxing strategy input
gates. Its raw NONE and adjusted TOTALRETURN responses, clean exports and
manifests are versioned locally. Inventory status/cleaning readiness must not be
mislabelled as full PIT/terminal/accounting readiness or a completed backtest.
