# IBKR Paper-Trading Onboarding

## Status

The user has opened and funded an IBKR account with approximately USD 10,000,
enabled Paper Trading, and logged in to TWS in paper mode. No API connection,
market-data subscription, paper order, or live order is configured by this
project yet.

The user has configured the laptop TWS Socket API for local read-only access.
The host, port, account identifier, and credentials are private local values
and are intentionally not recorded here.

TWS runs on the user's laptop. This server has no graphical environment and
must not connect directly to the laptop's TWS API socket.

## Required Account Checks

Complete these checks in IBKR Client Portal before any software integration:

1. Confirm the deposit is settled and the account is approved for US stocks and
   ETFs. Do not assume a funding notification means funds are available to trade.
2. Confirm account type, base currency, trading permissions, and applicable
   market-data subscriptions. For this low-frequency strategy, do not request
   options, margin, short-sale, or complex-product permissions.
3. Enable strong two-factor authentication and review trusted devices, login
   notifications, withdrawal instructions, and contact details.
4. Record no paper-account credentials in this repository or in project
   documentation.
5. Use paper trading first. A funded live account is not approval to send live
   orders from this system.

## API Choice

Use the official IBKR TWS API for the initial integration. It connects over a
local TCP socket to a running Trader Workstation (TWS) or IB Gateway instance.

- Start with TWS in paper mode to make account state and API behavior visible.
- Use IB Gateway only after the paper workflow is reliable; it has a smaller
  operational footprint, but still requires graphical authentication.
- Do not expose the TWS/IB Gateway API port to the public internet. Bind it to
  localhost or restrict it to explicitly trusted private-network addresses.
- Keep API order submission disabled until the account/position/order read path
  has passed its tests.
- Use a distinct API client ID for this system and record it as configuration,
  not as a credential.

IBKR documents that TWS and IB Gateway are designed for daily restart, and that
authentication is not supported in a headless session. The runtime design must
therefore include health monitoring, daily reconnect handling, and a documented
manual re-authentication procedure after the weekly reset.

Official reference: <https://interactivebrokers.github.io/tws-api/initial_setup.html>

## Execution-Node Topology

Use the laptop as the paper-execution node while TWS is running there. Use this
server only for research, backtesting, signal generation, and the audit store.

```text
Server: data, signals, approved target weights, audit records
                         ^
                         | authenticated outbound HTTPS polling
                         |
Laptop: execution agent -> localhost TWS API -> IBKR paper account
```

- The execution agent must run on the laptop in a local `deepstock` Conda
  environment and connect only to `127.0.0.1`.
- Do not open, forward, or publicly expose the TWS API port. In particular, do
  not create a reverse tunnel that allows the server to send arbitrary traffic
  to the API socket.
- The laptop agent should poll the server for an approved target plan over
  authenticated HTTPS. It must independently check paper mode, kill switch,
  data freshness, and risk limits before any future paper order.
- Until that agent exists, TWS API tests must be run locally on the laptop.

## Paper-Mode Connection Checklist

1. TWS is installed and signed in to the paper account.
2. The laptop TWS Socket API is enabled for local read-only access, with trusted
   IPs configured narrowly.
3. Record the chosen host, port, and client ID only in the laptop-local `.env`
   after the application configuration is finalized. TWS and IB Gateway use
   different default paper ports, so the configured value must be verified in
   the app.
4. Set up the `deepstock` Conda environment and project checkout on the laptop.
5. Run a read-only smoke test that retrieves server time, account summary,
   positions, and open orders. It must submit no order.
6. Reconcile those values against TWS and Client Portal. Resolve every
   difference before enabling paper-order capability.
7. Test rejected-order, disconnect, duplicate-run, and restart behavior in
   paper mode. Verify that alerts reach the approved channel.

## Capital and Execution Policy

The current USD 10,000 is live capital. It remains outside the automation path
until all paper-trading gates are met. When live readiness is separately
approved, begin with a predefined small allocation rather than the full balance.
The exact amount, drawdown limits, and go-live date require explicit approval.

The system's existing policy remains: long-only, no leverage, no short selling,
no options, and no live orders without an approved risk-control design.
