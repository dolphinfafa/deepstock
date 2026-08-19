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
