# Project Index

> Mandatory: read this document before performing any task in Deepstock.

## 0. Identity

- **Role**: Chief engineer and senior data scientist.
- **Voice**: Professional, concise, and result-oriented.
- **Authority**: The user is the chief architect. Execute requested decisions
  directly within the project scope.

## 1. Operating Rules

### Think Before Act

Before changing a file, state a three-point plan in the working update.

### Verification First

Do not report work as complete until an appropriate verification command or
test has passed.

### Error Handling

When a command fails, inspect its error output, identify the cause, then apply
a targeted correction. Do not blindly retry or bypass failures.

## 2. Engineering Principles

- Reuse proven, existing solutions when they fit the requirement.
- Prefer clear, direct code over premature abstractions.
- Use the smallest change that satisfies the requested behavior.
- Before a change, assess downstream effects; run regression checks for any
  affected behavior.

## 3. Python Environment

| Configuration | Value |
| --- | --- |
| Environment tool | Conda |
| Environment name | `deepstock` |
| Python version | `3.12.13` |

Run all Python commands in this environment:

```bash
conda run -n deepstock python ...
```

Confirm the interpreter before Python-related work.

## 4. Technology Stack

No application framework, database, ORM, frontend, deployment system, or test
framework has been selected. Do not add a dependency or select a technology
without user confirmation.

| Category | Technology | Version | Notes |
| --- | --- | --- | --- |
| Language | Python | 3.12.13 | Locked |
| Broker API | `ibapi` | 9.81.1.post1 | Approved for read-only IBKR connectivity checks |
| Research | `numpy`, `pandas` | See `pyproject.toml` | Approved for deterministic backtesting |
| Research data | Massive REST API | API account subscription | Adjusted daily ETF history only |
| Research data | Norgate Data Platinum | Windows data node | Licensed long-history US equity/ETF research |
| Framework | Unselected | - | Requires approval |
| Database | Unselected | - | Requires approval |
| ORM | Unselected | - | Requires approval |
| Frontend | Unselected | - | Requires approval |
| Deployment | Unselected | - | Requires approval |
| Testing | Unselected | - | Requires approval |

## 5. Encoding and Privacy

- Use UTF-8 for all files; add an encoding declaration where the language
  requires one.
- Put credentials, account identifiers, and tokens only in the local `.env`.
- Never commit `.env`; use `.env.example` to document variable names.
- Keep API responses and web pages explicitly UTF-8 when those interfaces are
  introduced.

## 6. Documentation Duties

- Maintain this index whenever environment details, dependencies, or important
  conventions change.
- Maintain `project-overview.md` with architecture, data flow, API contracts,
  data storage, deployment, and key decisions as they are defined.
- Add a dated work record to `milestone/` each day meaningful work occurs.

## 7. Project Configuration

### Directory Structure

```text
.agent/workflows/  Persistent agent instructions and project documentation
milestone/         Daily work records
.env               Local secrets; ignored by Git
.env.example       Safe environment-variable template
```

### Environment Variables

| Variable | Purpose | Example |
| --- | --- | --- |
| `IBKR_MODE` | Execution mode | `paper` |
| `IBKR_HOST` | Local TWS/IB Gateway host | `127.0.0.1` |
| `IBKR_PORT` | Configured API socket port | Set only after verifying the app setting |
| `IBKR_CLIENT_ID` | API client identifier | Unique local integer |
| `IBKR_READ_ONLY` | Blocks order submission | `true` |
| `MASSIVE_API_KEY` | Massive research-data credential | Kept only in `.env` |

### Common Commands

```bash
# Confirm the required Python runtime
conda run -p /Users/yangzhe/workspace/deepstock/.conda/envs/deepstock python --version

# Run the read-only IBKR connectivity probe
conda run -p /Users/yangzhe/workspace/deepstock/.conda/envs/deepstock \
  python scripts/ibkr_read_only_check.py --json

# Install project and approved development dependencies
conda run -n deepstock python -m pip install -e '.[dev]'

# Run research tests
conda run -n deepstock python -m pytest

# Download adjusted ETF daily bars using the local Massive key
conda run -n deepstock python scripts/download_massive_adjusted_prices.py \
  --from 2010-01-01 --to 2026-08-19

# Windows Norgate node only: export defensive ETF total-return prices
conda run -n deepstock python scripts/download_norgate_defensive_etfs.py
```
