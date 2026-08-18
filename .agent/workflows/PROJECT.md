# Deepstock Project Guide

> Before any project work, read `project-index.md` first, then this document.

## Purpose

Deepstock is a quantitative research and trading project focused on US equities.
The initial priority is reproducible research and paper trading; live trading is
out of scope until separately designed, reviewed, and approved.

## Mandatory Working Rules

1. Read `project-index.md` before any task, then read and maintain the
   documents in `.agent/workflows/`. Update them when a decision, architecture,
   operating procedure, or data contract changes.
2. Add a dated entry to `milestone/` for each day with meaningful project work.
   Record intent, changes, verification, and unresolved risks.
3. Put credentials, account identifiers, tokens, and other private values only
   in the local `.env` file. Never commit `.env`; use `.env.example` to document
   required variable names without values.
4. Run every Python command in the `deepstock` Conda environment:
   `conda run -n deepstock python ...`.

## Engineering Baseline

- Keep research reproducible: fix data ranges, record data sources, and retain
  assumptions required to recreate results.
- Prevent look-ahead bias, survivorship bias, and data leakage in every signal
  and backtest.
- Include transaction costs, slippage, liquidity constraints, and corporate
  actions in strategy evaluation when applicable.
- Treat market data licenses and API terms as constraints of the design.
- Default all execution integrations to paper trading. Do not place live orders
  without an explicit user request and a reviewed risk-control design.

## Project Layout

- `.agent/workflows/`: persistent project instructions and operating documents.
- `milestone/`: dated work records.
- `.env`: untracked local secrets and machine-specific configuration.
- `.env.example`: safe template for required environment variables.
