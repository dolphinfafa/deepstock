# Project Index

> Mandatory: read this document before performing any task in Deepstock.

## 0. Identity

- **Role**: Chief engineer and senior data scientist.
- **Voice**: Professional, concise, and result-oriented.
- **Authority**: The user is the chief architect. Execute requested decisions
  directly within the project scope.

## 1. Operating Rules

### Strategy Independence

Deepstock is a multi-strategy research library, not an ARC-centered project.
ARC is one peer strategy. New papers, factors and strategy notes default to
independent candidates with their own data, configuration, evidence and gates.
Do not propose or implement ARC routing by default. Combining strategies needs
an explicit user decision and separate portfolio evidence. Respect explicit
research freezes, including AHL.

### Market Organisation

Every strategy declares `US`, `CN`, or `Both` consistently in the catalog and
registry. Implementations live in `src/deepstock/strategies/{us,cn,both}/`;
shared governance, data and execution remain outside these market packages.
Do not mark a strategy `Both` merely because its indicators are portable.
See `strategy-structure.md` before adding or reorganising a strategy. ETF
afternoon momentum is A-share-only under the current user decision.
The independent Granville stock portfolio is Both, with at most five stocks per
market and free-slot replacement; see `granville-stock-portfolio.md`. The user
removed the separate ETF MA-swing strategy on October 6; keep its immutable
historical evidence and shared helpers, not its registration or page. Never
pool USD/CNY returns. Entry-episode v2 is a single-change diagnostic, not winner
promotion or authority for orders.
US risk-shrink v3 changes sizing only, keeps Both/CN historical evidence and
fixed original headlines; see granville-stock-portfolio.md. easy-tdx is an
independent CN data-source audit, not a strategy input; read
easy-tdx-data-audit.md for its pinned SDK/build/request/license boundaries.
October7 follow-up invalidates full-period US Granville performance conclusions:
member intervals stopAugust21 while prices continueSeptember29. Preserve old
ledgers with version-matched quality notices, not as valid comparison evidence.
New collection/entrances reject missing membership coverage; never extend old
intervals or relabel unknown eligibility as false. See the portfolio workflow.
Fresh native acquisition covers the1305-code current/past list but individual
required-date gaps still block, even when aggregate positive coverage passes.
Full OHLC inventory is evidence acquisition, never a workaround to strategy
input gates. Quote-life boundaries require source metadata, not guessed zeros.
October8 user-forwarded Norgate reply confirms non-trading-date effective
membership semantics. A separate versioned ALLMARKETDAYS member-only capture
passed coverage and native-overlap reconciliation for all1305 codes and the168
required-date gaps; native/old diagnostic versions remain immutable and blocked.
This does not permit price padding, infer announcement dates or resolve
WBA, sector, broker-share/settlement or strategy-level execution gates.

### Think Before Act

Read `data-layers.md` before download/cleaner/loader work. Raw evidence is
immutable; cleaning owns normalisation/audit and research entrances validate
registered versions. Never invent unknown executable prices/activity or
silently drop missing sessions. New research results pin all input versions.
CSI300 collection/backtesting now belongs to Deepstock, not Darwen.

Before changing a file, state a three-point plan in the working update.

### Verification First

Do not report work as complete until an appropriate verification command or
test has passed.

### Session Contact

October12 user authorized persistent Deepstock session contact: after30
minutes without a reply to the latest completed session message, send one
email to the owner address configured locally, with From display name
`deepstock`. A Codex reply cancels the pending reminder. Matching email replies
return to the original session. Read `session-mail-contact.md` for the running
bridge and its strict shared-mailbox boundaries. This authorization persists
across turns; it is limited to this contact rule and does not permit browsing
other agents' messages or sending unrelated mail. Do not install a second
timer, resend a claimed reminder or duplicate the bridge's mailbox polling.
Verified October13 owner email adds immediate email delivery of completed
email-origin tasks; Codex-origin unanswered messages keep the30-minute rule.
The subsequent October13 incremental-discovery plan authorizes read-only
headers in every selectable shared-mailbox folder, including spam/deleted mail.
Use per-folder UIDVALIDITY/UID cursors and local classification; fetch bodies
only for confirmed, unprocessed owner replies linked to our stored outbound
IDs/verified chains. Unknown parents, subject-only matches and cross-session
conflicts remain pending. Other agents' bodies never enter this flow. Receiving
continues while Codex is offline, and archived/deleted sessions stay suspended.
This supersedes the former narrow server-side search-only restriction.
The later October13 owner instruction additionally permits adding only Seen
to confirmed Deepstock replies after actual tool execution/result or assistant
reply, never on scan/download/queue/turn start. Persist processing evidence and
flag retries; revalidate UID identity; respect later manual unread. Other
agents' flags remain untouched. See session-mail-contact.md.
The second verified October13 reply adds separate scheduled-task result
emails. Read `scheduled-task-mail.md`: the provisional `results` scope
suppresses only successful high-frequency collection/ingestion, pending owner
preference; task times/parameters/quotas stay fixed. Only aggregate receipts
cross nodes. The provider missed real reply headers and tagged subject searches;
local UID scanning now replaces that discovery mechanism. Preserve the two previously
adopted replies' deduplication records. Live new-turn validation must use a new
actual reply, never replay an already handled task.

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

The application stack below was approved for the unified research and execution
control plane. New major infrastructure still requires user confirmation.

| Category | Technology | Version | Notes |
| --- | --- | --- | --- |
| Language | Python | 3.12.13 | Locked |
| Broker API | `ibapi` | 9.81.1.post1 | Approved for read-only IBKR connectivity checks |
| Research | `numpy`, `pandas` | See `pyproject.toml` | Approved for deterministic backtesting |
| Research data | Massive REST API | API account subscription | Adjusted daily ETF history only |
| Research data | Norgate Data Platinum | Windows data node | Licensed long-history US equity/ETF research |
| API framework | FastAPI | 0.142+ | Authenticated research and execution control plane |
| Database | SQLite | 3 | Business state, metrics, reports, audit, and execution control |
| ORM/migrations | SQLAlchemy / Alembic | 2.1+ / 1.20+ | Schema changes only through migrations |
| Frontend | Vue 3 / TypeScript / Vite | 3.5+ / 5.9+ / 7.3+ | Base path `/deepstock/` |
| Visualization | ECharts | 6+ | Strategy metric profiles |
| Deployment | Uvicorn + user systemd + NGINX | - | Binds only to `127.0.0.1:15001` |
| Testing | pytest / Vue typecheck | 8+ | Backend, governance, agent, and frontend gates |

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

### Windows Node Synchronization

`DESKTOP-ORNLESD` (the quantitative computer) is the active Norgate, TWS,
scheduled-observation, deployment, and verification node. Its project checkout
is `D:\\workspace\\deepstock`, and its dedicated Python environment is
`D:\\workspace\\conda-envs\\deepstock`. It is not an editing workspace. Never
modify its checked-out project files directly.

The October13 user correction establishes the SSH route from `xnous-dev-01`:
FRPC connects to `frps.yios.cn:443` and registers remote port20008. Use the
FRPS SSH jump host so the target loopback address is reached from FRPS:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes \
  -o 'ProxyCommand=ssh -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes -i /home/zheyang/.ssh/lq_1101 -W %h:%p root@frps.yios.cn' \
  -p 20008 -i /home/zheyang/.ssh/lq_1101 admin@127.0.0.1 'hostname'
```

Both jump-host root and Windows admin logins were verified with the existing
local `/home/zheyang/.ssh/lq_1101` private key on October13. ProxyCommand above
explicitly supplies it for each connection, without changing SSH config. If
using `-J root@frps.yios.cn` instead, configure that host's IdentityFile in
local SSH config or load it in ssh-agent: the target `-i` option alone does not
configure the jump host's identity. Keep private keys local. A failure at
`root@frps.yios.cn` is a jump-host
authentication failure; it does not test the Windows admin account or prove
FRPC offline. Direct loopback on xnous-dev-01 and the unverified dashboard
hostname are not established FRP endpoints.

`DESKTOP-S31222F` (the study computer) is retired from scheduled Deepstock and
US-market work. Its Deepstock tasks are disabled; its FRP startup task remains
enabled for administration and historical-data recovery.
Use this sequence for every project change:

1. Make and verify the change in this server workspace.
2. Commit and push the verified change to GitHub.
3. On the quantitative computer, confirm a clean worktree and fast-forward it by
   pulling the pushed commit.
4. Run the required local validation there without modifying tracked project
   files.

Machine-local secrets and runtime state, such as the untracked `.env`, TWS
settings, and licensed data artifacts, remain local to the quantitative
computer and are not copied into Git. Licensed artifacts may be copied directly
between authorized Windows nodes over SSH, with file counts, byte counts, and
SHA-256 hashes verified after transfer.

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
| `IBKR_ACCOUNT` | Locally selected TWS account | Kept only in `.env` |
| `IBKR_EXPECTED_ACCOUNT_HASH` | Pins the allowed account | SHA-256 hash only |
| `MASSIVE_API_KEY` | Massive research-data credential | Kept only in `.env` |
| `DEEPSTOCK_DATABASE_URL` | Application database | `sqlite:///artifacts/app/deepstock.sqlite3` |
| `DEEPSTOCK_NODE_TOKEN` | Windows execution-node authentication | Random local secret |
| `DEEPSTOCK_LIVE_TRADING_ENABLED` | Initial live gate | `false` |
| `DEEPSTOCK_GLOBAL_KILL_SWITCH` | Initial global kill switch | `true` |
| `DEEPSTOCK_SMTP_HOST` / `DEEPSTOCK_SMTP_PORT` | SMTP server for severe alerts | Host plus provider port |
| `DEEPSTOCK_SMTP_USERNAME` / `DEEPSTOCK_SMTP_PASSWORD` | SMTP authentication | Kept only in `.env` |
| `DEEPSTOCK_SMTP_FROM` | Alert sender address | Provider-approved sender |
| `DEEPSTOCK_SMTP_SECURITY` | SMTP transport security | `starttls`, `ssl`, or `plain` |
| `DEEPSTOCK_ALERT_EMAIL_TO` | Alert recipients | Comma-separated addresses in `.env` |
| `DEEPSTOCK_IMAP_HOST` / `DEEPSTOCK_IMAP_PORT` / `DEEPSTOCK_IMAP_SECURITY` | Mailbox transport for session replies | SSL on993; see `session-mail-contact.md` |
| `DEEPSTOCK_IMAP_USERNAME` / `DEEPSTOCK_IMAP_PASSWORD` | Optional distinct receiving credentials | Falls back to SMTP credentials when absent |
| `DEEPSTOCK_SESSION_EMAIL_ENABLED` / `DEEPSTOCK_SESSION_EMAIL_TO` | Explicitly authorized30-minute session reminders | Disabled by default; recipient kept in local `.env` |
| `DEEPSTOCK_CODEX_SOCKET` | Owner-only local Codex control socket | Absolute Unix socket path; no public listener |

After changing mail configuration, validate the encrypted SMTP/IMAP paths and
reload the long-running dashboard process to read the new `.env`. Mail tests
require explicit user instructions to send; see `mail-connectivity.md` for
the historical connectivity check and `session-mail-contact.md` for the
subsequently authorized recurring rule and current mailbox restrictions.

### Common Commands

```bash
# Confirm the required Python runtime
conda run -n deepstock python --version

# Run the read-only IBKR connectivity probe
conda run -n deepstock \
  python scripts/ibkr_read_only_check.py --json

# Install project and approved development dependencies
conda run -n deepstock python -m pip install -e '.[dev]'

# Initialize/migrate the app database and import current research state
conda run -n deepstock python scripts/init_deepstock_app.py

# Build the Vue application
cd frontend && npm install && npm run build

# Run the fail-closed Windows execution node once (local TWS host only)
conda run -n deepstock python scripts/ibkr_execution_agent.py --once

# Run research tests
conda run -n deepstock python -m pytest

# Download adjusted ETF daily bars using the local Massive key
conda run -n deepstock python scripts/download_massive_adjusted_prices.py \
  --from 2010-01-01 --to 2026-08-19

# Windows Norgate node only: export defensive ETF total-return prices
conda run -n deepstock python scripts/download_norgate_defensive_etfs.py

# Restore the single-writer auction schedule with quota-limited backfill
conda run -n deepstock python -m scripts.install_csi300_research_cron --dry-run

# Windows: reconcile existing defensive evidence without restarting observation
conda run -n deepstock python scripts/reconcile_defensive_observation.py
conda run -n deepstock python scripts/publish_defensive_observation.py
```
