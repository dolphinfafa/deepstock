# Isolated IBKR ETF paper pilot

October26 owner selected the custom unattended execution route. The latest
pilot parameters are USD200 and70:30. The owner's original request concerns
live money; the present implementation and deployment cover paper testing
only and must never be described as fulfilling that live purchase.
VOO/QQQM are the proposed example instruments, not owner-selected tickers.
This is one plumbing test, not a strategy, recurring contribution or authority
to deploy the earlier USD10,000 plan. It does not resume frozen research.

## Contract

- Fixed `paper-etf-200-70-30-v1`, VOO notional at most140 and QQQM at most60.
  Four-decimal fractional share quantities round down at a protected limit;
  fills can cost slightly less. USD200 excludes commissions. Require at least
  USD205 of both virtual USD ledger cash and available funds. Unknown settled
  cash is explicitly recorded; this is not proof of live settled-cash readiness.
- The worker runs only on Windows, connects only to127.0.0.1:7497, requires
  exactly one DU-number paper account and a salted local account pin. It has
  no live mode, host or port option, and cannot cancel/replace orders.
- `.env.paper-pilot` is separate from the main `.env`, initially disabled.
  Account identifiers never enter source control or public output.
- Exchange calendar gates orders from five minutes after regular open until
  ten minutes before close, including holidays, early closes and DST.
  Require real-time bid and ask received within15seconds, spread at most0.5%,
  USD SMART STK contracts pinned by conId/primary exchange. A limit is at most
  the ask plus0.1%, rounded up to cents. Both what-if checks pass before the
  first submission. No whole-share fallback or budget increase is allowed.
- An OS process lock protects the single machine-local journal. Plan and
  account/date identity are immutable. Each intent is flushed/fsynced and
  atomically persisted before submission. Missing acknowledgements never
  authorize a retry, even if broker searches return no order. Restart performs
  open/completed order and execution reconciliation first. Duplicate/stale
  callbacks cannot reverse fills. Rejections stop further submission; partial
  fills are observed without replacing or topping up orders.
- Success requires both complete quantities and matching changes in actual
  paper positions. Unknown commissions remain unknown. Results/fees and state
  stay in `artifacts/ibkr-paper-pilot/`; no new mail polling is installed.

## SDK isolation

The project retains `ibapi==9.81.1.post1` for existing clients. Its negotiated
protocol157 cannot submit fractional stock orders: actual paper what-if
requests returned10243 for fractional quantities and10244 for stock cashQty.
This does not establish missing account trading permissions.

The pilot may use an official1051.1 Python SDK only within its own process.
The official download page moved from interactivebrokers.github.io to
<https://download2.interactivebrokers.com/installers/tws-api/index.html>.
The October26 official Linux installer was downloaded through normal HTTPS
on Windows after server TLS failures; the public installer was copied to the
server and installed only under ignored artifacts, without changing TWS or
the existing Python installation.

- Installer URL: `https://download2.interactivebrokers.com/installers/tws-api/latest/tws-api-latest-linux-x64.sh`
- Installer SHA256: `152af944be28bcfaf2733aec907e7724f6dda092cb3148848e84d2a0bb866c0a`
- Official `setup.py` requires protobuf5.29.5. Its pure-Python wheel is placed
  alongside the unmodified client; the main Conda environment is unchanged.
- The local bundle contains `source/pythonclient/`, that dependency, IBKR
  LICENSE/API_VersionNum and a provenance manifest. Bundle SHA256 and full
  source-tree SHA256 are pinned in `prepare_ibkr_paper_sdk.py`. An unexpected
  archive or changed/extra source fails closed. The bundle is a local public
  software artifact, not a committed dependency or a copied private database.
- Official source confirms `MIN_SERVER_VER_FRACTIONAL_SIZE_SUPPORT=163`.
  SDK1051 adds errorTime to error callbacks and renames commission fields;
  the isolated adapter handles both forms. Never import the general broker
  agent before selecting the process-local SDK.

## Operations

Follow server tests → GitHub → clean Windows ff-only pull → native tests.
Transfer the checksum-verified local public bundle separately. Run with the
dedicated `D:\workspace\conda-envs\deepstock\python.exe`, project cwd:

```text
python -m scripts.prepare_ibkr_paper_sdk --bundle artifacts/ibkr-paper-pilot/download/paper-sdk-1051.1.zip
python -m scripts.run_ibkr_paper_pilot --sdk-root artifacts/ibkr-paper-pilot/sdk --pin-account
python -m scripts.run_ibkr_paper_pilot --sdk-root artifacts/ibkr-paper-pilot/sdk --check
```

`--check` sends only non-executing what-if previews. Example limits1000/500
are diagnostic inputs, not executable plans or current quotes. It does not
enable submission. A preflight pass still requires current quotes, market
hours, identity/cash/reconciliation and local enablement at execution time.

An isolated paper run, after native checks, uses one explicit session date:

```text
python -m scripts.run_ibkr_paper_pilot --sdk-root artifacts/ibkr-paper-pilot/sdk --run-paper --trade-date 2026-10-26 --watch-seconds 900
```

Do not delete/recreate the journal or re-pin an account to bypass a block.
After ambiguous submission, retain it for reconciliation. A one-shot pilot
cannot become monthly just by invoking it on another date. A successful test
does not enable live trading or prove unattended TWS login/re-authentication.
