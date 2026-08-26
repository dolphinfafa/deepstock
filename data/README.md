# Research Data Contract

The defensive ETF backtest consumes a local CSV with these exact columns:

```text
date,symbol,adjusted_close
2024-01-02,SPY,472.65
2024-01-02,SHY,81.50
```

Requirements:

- One row per trading date and symbol.
- ISO-8601 dates, sorted ascending after loading.
- Positive, complete total-return adjusted closing prices.
- All required symbols: `SPY`, `QQQ`, `IWM`, `TLT`, `IEF`, `GLD`, and `SHY`.
- Preserve a source manifest alongside every imported dataset: provider, symbol
  mapping, retrieval timestamp, date range, adjustment method, and license.

Do not commit licensed raw market data unless its provider's terms explicitly
allow redistribution. `artifacts/` and local research data are Git ignored.

Massive daily aggregates with `adjusted=true` adjust only for splits. The
downloader combines those bars with Massive dividend events into a sequential
total-return index, adding the split-adjusted cash dividend on each ex-dividend
date. This avoids future corporate-action leakage into historical signals. The
local manifest records both endpoints, the adjustment method, date range,
symbols, row count, and retrieval time without recording the API key.

Massive may label a valid response as `DELAYED` under the current subscription.
This is acceptable for fixed historical research data, but never for real-time
execution checks or order pricing; those remain an IBKR responsibility.

Always compare the requested and actual date ranges in the manifest. Subscription
history limits can return less data than the requested range; do not describe a
period as tested unless it appears in `actual_from` through `actual_to`.

## Stock Turtle Input

The stock version of the Turtle research script accepts the same long CSV
schema, but `symbol` may contain a predeclared, point-in-time stock universe
instead of the seven ETF symbols. The dataset must be adjusted for splits and
cash dividends, include delisted names when they were historically eligible,
and include a non-secret manifest documenting the universe construction,
liquidity filter, provider, actual coverage, and license. Close-only data cannot
validate bid-ask spreads, gaps, or earnings-event execution; those limitations
must remain in the backtest report.

The Norgate stock-universe exporter stores price CSV chunks plus a JSON mapping
of each symbol to inclusive historical index-membership intervals. These
intervals must be applied at each signal date; treating the union of current
and past constituents as eligible on every date recreates survivorship and
look-ahead bias.

## Global Futures Trend Input (Planned)

The AHL-inspired global futures trend study has no approved historical input
yet. It must not run from ETF proxies, IBKR snapshots, or a generic adjusted
close file. A provider and licensed, roll-aware continuous-contract history
must be available before implementing a performance backtest.

Each imported futures dataset must have a non-secret manifest containing:

- provider, licence, retrieval timestamp, actual coverage, exchange calendar,
  and an explicit source-to-research-symbol mapping;
- contract root, exchange, currency, point value / multiplier, tick size,
  initial and maintenance margin metadata, and exchange fees when available;
- daily official settlement prices for individual contracts, including expiry
  and first-notice / last-trade dates;
- continuous-series methodology: eligible contracts, roll trigger and dates,
  roll window, back-adjustment method, and whether returns come from actual
  contract rolls or an adjusted synthetic series;
- currency conversion source and timestamp policy for non-USD contracts;
- missing-session, limit-move, stale-price, and contract-delisting policies.

The research engine must size and book returns on actual contract metadata,
then independently reconcile results with the documented continuous series.
It must model both sides of every scheduled roll, commissions, bid-ask/slippage
and exchange fees. Back-adjusted prices may create signals but are not by
themselves a tradeable return series.
