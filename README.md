# Deepstock

Quantitative research and paper-trading tools for US equities.

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

These commands are research-only. They include modeled costs and never submit
orders.
