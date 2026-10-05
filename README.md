# Deepstock

Multi-market quantitative research, governance, and fail-closed automated
trading control plane.

## Research Library

Strategies are independent peers, grouped into 美股 (`US`), A股 (`CN`) and
`Both`. The homepage and frozen archive support market tabs; new research is
not automatically added to ARC. Strategy implementations are organised under
`src/deepstock/strategies/{us,cn,both}/`; shared infrastructure and compatible
legacy imports keep existing schedules working. See
[strategy structure](.agent/workflows/strategy-structure.md).

The authenticated web application is deployed at
`https://dev-cn-01.yios.cn/deepstock/`. It presents all registered strategies,
their thesis, metrics, progress, runs, reports, notes, account state, orders,
alerts, and data jobs. The initial local administrator is `admin/admin`.

```bash
conda run -n deepstock python scripts/init_deepstock_app.py
cd frontend && npm install && npm run build
conda run -n deepstock python -m uvicorn deepstock.web.app:app \
  --host 127.0.0.1 --port 15001
```

The database and backups live under ignored `artifacts/app/`. Install periodic
state ingestion and backup with:

```bash
conda run -n deepstock python scripts/install_deepstock_app_cron.py
```

## Execution Agent

`scripts/ibkr_execution_agent.py` runs only beside the quantitative computer's
local TWS. It is read-only by default. Paper or Live submission requires an
explicit server plan plus all local and server risk gates; Live remains disabled
until a strategy is separately marked eligible and authorized.

On the quantitative computer, pin the sole account exposed by the intended TWS
profile without printing the account identifier:

```bash
conda run -n deepstock python scripts/ibkr_execution_agent.py \
  --bootstrap-account-guard --confirm PIN-SOLE-TWS-ACCOUNT
```

## Python Environment

Run Python only through the project Conda environment:

```bash
conda run -n deepstock python --version
```

## Project Operations

Read `.agent/workflows/project-index.md` before working on the project, then
read `.agent/workflows/PROJECT.md`. Record each day's work in
`milestone/YYYY-MM-DD.md`. Store private configuration in a local, untracked
`.env` file; start from `.env.example`.

## IBKR Read-Only Probe

Run the IBKR connectivity probe from the `deepstock` Conda environment after
starting TWS or IB Gateway in paper mode with read-only API access enabled:

```bash
conda run -p /Users/yangzhe/workspace/deepstock/.conda/envs/deepstock \
  python scripts/ibkr_read_only_check.py --json
```

The probe only requests server time, account summary, positions, and open
orders. It refuses to start unless `IBKR_MODE=paper` and `IBKR_READ_ONLY=true`
are set in the local `.env`.

## SGOV Paper API Smoke Test

`scripts/ibkr_paper_sgov_smoke_test.py` is an execution-path test, not a
strategy. It can submit exactly one `SGOV` Paper buy order for one share at a
deliberately non-marketable `$1.00` limit, then cancel it. It refuses live mode,
requires `IBKR_READ_ONLY=false`, and requires two explicit command-line
confirmations. It must only run on the local Paper TWS host:

```bash
conda run -n deepstock python scripts/ibkr_paper_sgov_smoke_test.py \
  --submit --confirm PAPER-SGOV-SMOKE-TEST --json
```

If a prior smoke-test order remains pending, the same script can cancel only
the exact `SGOV` one-share `$1.00` Paper order created by this test:

```bash
conda run -n deepstock python scripts/ibkr_paper_sgov_smoke_test.py \
  --cancel-pending --confirm PAPER-SGOV-SMOKE-CANCEL --json
```

## Defensive ETF Backtest

The initial research strategy uses adjusted daily closes for `SPY`, `QQQ`,
`IWM`, `TLT`, `IEF`, `GLD`, and `SHY`. Supply a local CSV with exactly these
symbols and the columns `date`, `symbol`, and `adjusted_close`. Data must be
complete, positive, split/dividend adjusted, and licensed for the intended use.

```bash
conda run -n deepstock python scripts/run_defensive_etf_backtest.py \
  --prices data/adjusted_daily_prices.csv
```

The backtest writes reproducible outputs to `artifacts/backtests/latest/` and
never connects to TWS or submits an order.

## A-Share ETF Afternoon Momentum

The independent `510300.SH` T+1 candidate is A-share-only, with three fixed
paths, four cost cases and matching-window benchmarks. A real one-minute
dataset and exchange calendar are required; missing data produces a blocked
report, not synthetic research returns. No broker orders are part of this study.
It is currently paused by the user and marked “需要更多数据”; the commands
below return the persisted pause without fetching data or running a backtest.

```bash
conda run -n deepstock python scripts/cn/download_etf_tail_minutes.py --probe-only \
  --from 2025-08-18 --to 2025-08-19
conda run -n deepstock python scripts/cn/run_etf_tail_momentum.py
```

See [fixed research specification](.agent/workflows/etf-tail-momentum.md) for
data access, fill timing, T+1 accounting and the optional short public pilot.

## ARC Research Reports

Deepstock ARC keeps regime control, route adapters, and portfolio risk checks
separate. Regime reports include controlled-state durations and route-conditional
benchmark results:

```bash
conda run -n deepstock python scripts/run_arc_regime_backtest.py \
  --prices data/adjusted_daily_prices.csv
```

The Range adapter also writes fixed 504/252/252 walk-forward slices and applies
the predeclared abnormal-move exit:

```bash
conda run -n deepstock python scripts/run_arc_grid_backtest.py \
  --prices data/adjusted_daily_prices.csv
```

The fixed ADX/DMI controller diagnostic requires genuine high, low, and close
bars. Download the locally licensed Massive coverage, then compare it with the
current controller on identical routes and dates:

```bash
conda run -n deepstock python scripts/download_massive_daily_ohlc.py \
  --from 2004-11-18 --to 2026-09-29 --symbols SPY \
  --output artifacts/data/massive_spy_ohlc.csv

conda run -n deepstock python scripts/run_arc_adx_backtest.py \
  --universe-dir artifacts/research/norgate/stock-universe-sp500-liquidity \
  --etf-prices artifacts/research/norgate/etf_prices.csv \
  --spy-ohlc artifacts/data/massive_spy_ohlc.csv \
  --spy-ohlc-manifest artifacts/data/massive_spy_ohlc.manifest.json
```

For the long-history gate, first export licensed total-return OHLC locally on
the Windows Norgate node, then transfer the ignored CSV and manifest to the
server research workspace and rerun the same fixed diagnostic without changing
the ADX parameters:

```bash
conda run -n deepstock python scripts/download_norgate_daily_ohlc.py \
  --symbol SPY \
  --output artifacts/research/norgate/spy_daily_ohlc.csv

conda run -n deepstock python scripts/run_arc_adx_backtest.py \
  --universe-dir artifacts/research/norgate/stock-universe-sp500-liquidity \
  --etf-prices artifacts/research/norgate/etf_prices.csv \
  --spy-ohlc artifacts/research/norgate/spy_daily_ohlc.csv \
  --output-dir artifacts/robustness/arc-adx-full-history-2026-09-30
```

These commands are research-only. They include modeled costs and never submit
orders.

The one-share fill test is also Paper-only. It uses a regular-hours `SGOV`
limit order capped at `$150`; it cancels automatically after two minutes if no
fill occurs:

```bash
conda run -n deepstock python scripts/ibkr_paper_sgov_smoke_test.py \
  --submit-fill-test --confirm PAPER-SGOV-FILL-TEST --json
```
