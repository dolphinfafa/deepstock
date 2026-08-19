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
- Positive, complete, split- and dividend-adjusted closing prices.
- All required symbols: `SPY`, `QQQ`, `IWM`, `TLT`, `IEF`, `GLD`, and `SHY`.
- Preserve a source manifest alongside every imported dataset: provider, symbol
  mapping, retrieval timestamp, date range, adjustment method, and license.

Do not commit licensed raw market data unless its provider's terms explicitly
allow redistribution. `artifacts/` and local research data are Git ignored.

The current source is Massive's daily aggregates endpoint with `adjusted=true`.
The downloader writes its provider, endpoint, date range, symbols, adjustment
flag, row count, and retrieval time to a local manifest without recording its
API key.
