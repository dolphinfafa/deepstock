# Defensive ETF Observation Protocol

## Scope

This is a six-week, order-free observation protocol for the frozen adaptive
defensive ETF configuration. It does not authorize IBKR order submission,
paper or live. The plan generator has no broker dependency.

## Frozen Configuration

- Universe: `SPY`, `QQQ`, `IWM`, `TLT`, `IEF`, `GLD`, and `SHY`.
- Signals: 252-session momentum, 200-session trend and market filter.
- Selection: top two eligible risk assets, inverse-volatility sizing.
- Exposure: 80% when SPY is above its filter and 40% otherwise.
- Limits: 20% maximum per risk asset, balance in `SHY`, 5 bps modeled cost.

Do not change this configuration during the observation period. A proposed
change starts a new research experiment and restarts the observation clock.

## Acceptance Criteria

The strategy is evaluated as a defensive allocation, not as a benchmark-alpha
claim. Before any future paper-order implementation, all conditions below must
be reviewed explicitly:

1. At least 30 completed trading-session observations over at least 42 calendar
   days, with no stale-data run, duplicate-plan write, missing symbol, or
   unhandled script failure.
2. Every plan has a unique `plan_id`, a completed-session `data_date`, weights
   summing to one, and no risk-asset weight above 20%.
3. Observed targets are reconciled against the same Norgate input file and any
   simulated fills; exceptions are recorded rather than repaired silently.
4. The user accepts the documented trade-off: the strategy can materially lag
   SPY in strong equity markets and had a -9.81% rolling 2021-2022 window.
5. Kill-switch, duplicate-plan, missing-data, and stale-data paths have each
   been exercised and recorded. No broker order test is included in this phase.

The current governance policy is `shadow-governance-v2-2026-08-31`. Earlier
observation records remain auditable but do not count toward this policy; the
new observation clock begins with the first completed data date on or after
2026-08-31.

## Daily Procedure

Run only after Norgate Data Updater has completed its end-of-day refresh on the
Windows data node:

```bash
conda run -n deepstock python scripts/download_norgate_defensive_etfs.py
conda run -n deepstock python scripts/generate_defensive_etf_plan.py \
  --prices artifacts/research/norgate/defensive_etf_prices.csv
conda run -n deepstock python scripts/record_paper_observation.py \
  --plan artifacts/paper/defensive-etf/latest.json
conda run -n deepstock python scripts/run_defensive_etf_backtest.py \
  --profile adaptive \
  --prices artifacts/research/norgate/defensive_etf_prices.csv \
  --output-dir artifacts/research/strategy-governance/adaptive-defensive-latest
conda run -n deepstock python scripts/run_adaptive_defensive_walkforward.py \
  --prices artifacts/research/norgate/defensive_etf_prices.csv \
  --output-dir artifacts/research/strategy-governance/adaptive-defensive-walkforward
conda run -n deepstock python scripts/build_defensive_governance_snapshot.py \
  --prices artifacts/research/norgate/defensive_etf_prices.csv \
  --daily artifacts/research/strategy-governance/adaptive-defensive-latest/daily_results.csv \
  --walkforward artifacts/research/strategy-governance/adaptive-defensive-walkforward/walkforward_results.csv \
  --manifest artifacts/research/strategy-governance/adaptive-defensive-walkforward/manifest.json \
  --plan artifacts/paper/defensive-etf/latest.json \
  --observations artifacts/paper/defensive-etf/observations.jsonl \
  --output artifacts/research/strategy-governance/adaptive-defensive-snapshot.json
conda run -n deepstock python scripts/evaluate_strategy_registry.py \
  --snapshots artifacts/research/strategy-governance/adaptive-defensive-snapshot.json \
  --skip-duplicate
conda run -n deepstock python scripts/publish_defensive_observation.py
```

The first command runs only on the Windows Norgate node. The following commands
run on the host holding the downloaded file. The final four commands use the
same frozen Defensive ETF configuration to refresh the research report,
Walk-Forward evidence, governance snapshot, and append-only decision ledger.
The risk-review field defaults to false, so this process cannot promote the
strategy. Licensed Norgate data and all artifacts remain local and must not be
committed. Do not rerun the record command for an unchanged plan: duplicate
plan IDs are rejected by design.

## Windows Scheduling

The quantitative computer `DESKTOP-ORNLESD` uses China Standard Time. The wrapper
`scripts\\run_defensive_etf_observation.cmd` is registered as the daily Task
Scheduler job `Deepstock_Defensive_ETF_Observation` at 07:30 local time. It is
configured to wake the computer from sleep and to run as soon as possible after
a missed start. It stops on the first failure and appends diagnostics to
`artifacts\\paper\\defensive-etf\\scheduler.log`. The task is an observation
job with an additional research-governance evaluation; it does not start TWS or
submit orders. Windows power policy or a disconnected AC adapter may still
prevent wake-up.

The wrapper resolves the project directory from its own location and runs
`D:\\workspace\\conda-envs\\deepstock\\python.exe` on the quantitative
computer. The former task on `DESKTOP-S31222F` is disabled. The historical
one-time SGOV Paper fill-test task on that computer is also disabled and is not
recreated because it has no future trigger.

## Configuration Reconciliation and Website Publishing (2026-10-05)

The previous daily wrapper accidentally invoked the default baseline backtest
(no Top-2 selection or market filter), while the plan and Walk-Forward used the
frozen adaptive parameters. Previous rolling metrics are not valid adaptive
evidence. The strategy rules themselves have not changed.

`deepstock.defensive.frozen_defensive_config()` is now the shared configuration.
The adaptive backtest, Walk-Forward, plan and snapshot must agree on its SHA-256
hash and evidence date; the snapshot also verifies the daily CSV checksum.
Mismatches fail the task instead of producing governance evidence.

Use `scripts/reconcile_defensive_observation.py` once on the Windows node to
archive previous evidence under the ignored reconciliation directory, recompute
from the already downloaded Norgate file, and write a correction audit. It does
not download prices, append an observation, reset the clock, change a rule, or
overwrite the original decision ledger. Then run the publisher.

The daily wrapper now POSTs a price-free aggregate bundle to
`/api/agent/research/defensive-observation` using `DEEPSTOCK_NODE_TOKEN` and
`DEEPSTOCK_PUBLIC_BASE_URL`. The server checks configuration, timestamps,
observation counts and report dates, rejects replay/backwards evidence, and
prefers that node bundle over stale server-local artifacts. Publishing failure
fails the scheduled task. Raw Norgate prices and account data remain local.
Current assessments are stored in the research report; historical decisions
remain auditable, including the configuration correction.

October13 owner email additionally requests separate scheduled-task result
mail. The existing Task Scheduler action/time remains; its `.cmd` now calls
the receipt wrapper and `run_defensive_etf_observation_core.cmd` contains the
unchanged original pipeline. After success/failure, only an allowlisted
aggregate receipt is published using the existing node token to
`/api/agent/scheduled-results`. SMTP/IMAP credentials remain server-only.
Notification failure leaves the original exit code and research task intact;
pending receipt publication is idempotent. See `scheduled-task-mail.md`.

## Background recovery and publication isolation (2026-10-22)

The three quantitative observations can be restored together with
`python -m scripts.install_observation_tasks`. It exports all selected existing
task XMLs before replacement and runs one temporary on-demand S4U diagnostic
under the verified current user's SID. That context must read local settings,
plans/ledgers, current Norgate ETF data and historical watchlist, and reach the
server health endpoint. A failure leaves the original schedules unchanged;
never substitute SYSTEM, store a password or enable autologin. The temporary
task is ended/deleted even on failure. Registration failures restore attempted
tasks from their local XML backups. --probe-only performs no permanent task
replacement; --task can select one of the existing authorized observers.

Passing installations use S4U/LeastPrivilege, WakeToRun, StartWhenAvailable,
IgnoreNew, no battery condition and a two-hour execution limit. Original actions
and07:30/07:45/08:10China times are retained, starting at their next trigger.
This removes the interactive-login requirement; next scheduled execution still
needs separate real-world verification. Local diagnostics/backups stay under
artifacts/research/observation-scheduler. No permanent diagnostic timer is added.

The defensive core now completes local plan/observation/governance evidence
before either network publication. It attempts metadata and aggregate
observation publishing independently, retains a failing final exit if either
fails, and logs publication pending. Recover with publish_data_catalog.py and/or
publish_defensive_observation.py alone. Never rerun the engine solely to retry
upload, reset the observation clock or invent missed observations.

October22 native validation: metadata recovery succeeded for28823 manifests,
including two2701-reference summaries; repeat publication sent0. Actual S4U
passed user/local-file checks but Norgate did not finish. The SDK can sys.exit(0)
on provider failures, so the probe explicitly records SystemExit as failure.
The vendor-documented NDU.Trigger UPDATE DONOTSHOW attempt returned0 yet NDU
4.2.0.38 crashed with InvalidOperationException in Form.ShowDialog (Windows
Application events1026/1000/1001). No updater remained. Until NDU starts in an
actual Windows user session and ordinary data refresh completes, retain the
existing task XML and report this dependency block; do not claim installation,
current observations or next-trigger acceptance. Re-probe before replacing tasks.

LaterOctober22 at15:10China, the owner had logged into Windows and NDU was
running in desktop session1. SDK reads succeeded throughOctober21. A new actual
S4U probe passed identity/files/Norgate/network at15:11:56; all three existing
tasks were migrated with original actions/times intact. The Defensive original
task then completed in session0 and publishedOctober21 evidence with26effective
observations. This verifies background access to an already running desktop
NDU, not unattended NDU startup or behavior after logout/reboot. Keep NDU
running; its headless startup crash remains unresolved. Next daily triggers
need independent verification rather than being inferred from manual starts.
