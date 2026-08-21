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
