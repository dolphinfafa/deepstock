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
