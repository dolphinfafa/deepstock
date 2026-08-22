# Defensive ETF Observation Protocol

## Scope

This is an eight-week, order-free observation protocol for the frozen adaptive
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

1. At least eight consecutive weekly observations with no stale-data run,
   duplicate plan, missing symbol, or unhandled script failure.
2. Every plan has a unique `plan_id`, a completed-session `data_date`, weights
   summing to one, and no risk-asset weight above 20%.
3. Observed targets are reconciled against the same Norgate input file and any
   simulated fills; exceptions are recorded rather than repaired silently.
4. The user accepts the documented trade-off: the strategy can materially lag
   SPY in strong equity markets and had a -9.81% rolling 2021-2022 window.
5. Kill-switch, duplicate-plan, missing-data, and stale-data paths have each
   been exercised and recorded. No broker order test is included in this phase.

## Daily Procedure

Run only after Norgate Data Updater has completed its end-of-day refresh on the
Windows data node:

```bash
conda run -n deepstock python scripts/download_norgate_defensive_etfs.py
conda run -n deepstock python scripts/generate_defensive_etf_plan.py \
  --prices artifacts/research/norgate/defensive_etf_prices.csv
conda run -n deepstock python scripts/record_paper_observation.py \
  --plan artifacts/paper/defensive-etf/latest.json
```

The first command runs only on the Windows Norgate node. The latter two run on
the host holding the downloaded file. Licensed Norgate data and all artifacts
remain local and must not be committed. Do not rerun the record command for an
unchanged plan: duplicate plan IDs are rejected by design.

## Windows Scheduling

The Windows node uses China Standard Time. After confirming NDU has completed
its daily update, the wrapper
`scripts\\run_defensive_etf_observation.cmd` can be registered as a daily
Task Scheduler job at 07:30 local time. It stops on the first failure and
appends diagnostics to `artifacts\\paper\\defensive-etf\\scheduler.log`.
The task is an observation job only; it does not start TWS or submit orders.
