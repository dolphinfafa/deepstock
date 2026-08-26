# Global Futures Data Source Evaluation

## Decision State

`research_due_diligence_only`. No provider is licensed or selected. Do not
download, subscribe to, or use data until the supplier confirms the requested
entitlements and redistribution terms in writing.

## Recommendation Order

1. **CME Group DataMine / direct CME historical product** is the preferred
   first option to evaluate. The pre-registered `ES`, `NQ`, `ZN`, `ZB`, `CL`,
   `GC`, `6E`, and `6J` pool is within CME Group exchanges. A direct source is
   the strongest path to official settlement data and exchange-identical
   contract calendars. This remains a recommendation, not a verified purchase
   decision: confirm the exact dataset, delivery format, historical depth,
   settlement field, contract specifications, fee schedule, and research-use
   licence before accepting it.
2. **Databento CME historical data** is the preferred API-oriented alternative
   to evaluate when point-in-time instrument definitions and programmatic
   delivery are required. Its public material states CME venue coverage and
   point-in-time instrument definitions, but also advertises historical depth
   of up to 16 years. Confirm coverage of all eight roots, daily settlement
   availability, roll reconstruction inputs, and the actual first date before
   treating it as sufficient.
3. **Barchart or another established EOD futures vendor** is a fallback only
   if it supplies both individual-contract daily prices and written continuous
   series / roll methodology. A continuous price alone is insufficient for
   tradeable backtest returns.

No provider may be mixed with another in the first validation run unless the
source boundary, differences in settlement convention, and reconciliation are
predeclared. IBKR is reserved for later execution-time checks and is not a
historical research source.

## Supplier Questions (Must All Pass)

| Contract requirement | Evidence required before selection |
| --- | --- |
| Individual contracts | Sample CSV/API response for every root, with symbol, exchange, expiry, date, and official daily settlement field |
| Coverage | Actual first/last session for every root; at least 15 complete calendar years and no undocumented gaps |
| Roll reconstruction | First-notice and last-trade dates, plus a documented method to identify the tradable front / deferred contract each day |
| Continuous series | Published adjustment, roll trigger, and treatment of roll gaps; accepted only as a signal input, not sole return input |
| Contract metadata | Point value, tick size, currency, trading hours/calendar, and historical specification changes |
| Costs | Historical or current exchange and clearing fee schedule, and sufficient information to set a conservative commission assumption |
| Licence | Explicit internal quantitative research and local storage permission; no raw-data commit or public redistribution |
| Delivery | Repeatable export/API with retrieval timestamp and no future revisions silently overwriting an archived run |

## Acceptance Procedure

Before implementation, obtain a small sample covering two consecutive rolls of
`ES` and `CL`. Independently check that the settlement dates, expiry ordering,
roll dates, multiplier, tick value, and continuous-series adjustment match the
vendor documentation. Archive only the non-secret manifest and validation
report. The raw licensed sample remains Git-ignored.

Reject a source if any required contract lacks an identifiable official daily
settlement, expiry/roll inputs, or reproducible terms of use. Do not replace a
failed field with an ETF proxy or forward-filled data.

## Sources Consulted

- CME Group DataMine historical-data page: supplier information only; the
  request did not return a stable page during this review, so all product
  fields remain to be confirmed directly with CME.
- Databento historical-data page, accessed 2026-08-25: public material states
  CME coverage, point-in-time instrument definitions, and up to 16 years of
  historical data. It does not by itself verify the required daily settlement
  or full-root coverage.
- Barchart market-data API page: identified as a candidate for vendor due
  diligence; no specific product field has been accepted from public material.
