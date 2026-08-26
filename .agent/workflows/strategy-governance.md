# Strategy Shadow Governance

## Purpose and Authority

The strategy-governance layer evaluates fixed strategy snapshots every research
day. It is deliberately separate from strategy backtests, paper-plan generation,
IBKR, and ARC routing. It has no broker import, cannot create orders, and never
sets `paper_authorized` or `live_authorized` to true.

The version-controlled registry is `config/strategy_registry.json`. Current
authority is intentionally narrow:

| Strategy | Registry status | Governance outcome ceiling |
| --- | --- | --- |
| Defensive ETF | `shadow_observation_no_orders` | `eligible_for_paper_review` |
| ARC, Stock Turtle, Range Grid, SPY Mean Reversion | `research_only_no_orders` | `continue_shadow_observation` |
| AHL-inspired Global Futures Trend | `research_only_no_orders` | `continue_shadow_observation` |

`eligible_for_paper_review` is an advisory result, not approval to start a
paper account, send an order, or modify a production allocation. A separate
human decision and execution-risk review would still be required.

## Frozen Policy v1

Policy identifier: `shadow-governance-v1-2026-08-26`.

The policy is effective from 2026-08-26. `as_of_date` is the local calendar
date on which the decision is made; `data_date` is the last completed market
session used by the report. The evaluator rejects a decision predating the
policy, so a later run cannot fabricate a contemporaneous historical decision.

Every daily snapshot must affirm fixed parameters, no OOS parameter selection,
included costs, fresh data, and a passed risk review. For the sole
shadow-observation strategy, it must also meet all of these gates:

- at least 6 fixed Walk-Forward test windows, with no more than 1 negative;
- at least 252 rolling OOS sessions and rolling OOS Sharpe of 0.50 or above;
- rolling OOS maximum drawdown no worse than -20%;
- annualized turnover no higher than 15.0; and
- at least 40 completed order-free shadow sessions.

These values are frozen before using this layer for selection. Modifying any
one requires a new policy identifier, a new research experiment, and a fresh
observation history; it must not rewrite past decisions.

## Daily Procedure

1. Update only the licensed source data that was available after the completed
   market session.
2. Rerun each strategy's fixed research script with its existing specification.
   Do not search parameters or rank candidates by the newly observed OOS data.
3. Produce a structured JSON snapshot from the resulting fixed reports. The
   snapshot records the as-of date, data date, OOS and Walk-Forward metrics,
   turnover, costs, freshness, and observation sessions.
4. Run `scripts/evaluate_strategy_registry.py --snapshots <file>`.
   It appends an idempotent decision to the ignored
   `artifacts/research/strategy-governance/decisions.jsonl` ledger.
5. Review any `eligible_for_paper_review` result manually. A new strategy
   remains in parallel, order-free observation until an explicit authorization
   decision exists.

The ledger permits at most one decision per strategy and as-of date. This
prevents a later same-day metric revision from silently replacing the
contemporaneous decision that would have been available in real time.

## Snapshot Example

```json
{
  "strategy_id": "adaptive_defensive_etf",
  "as_of_date": "2026-08-26",
  "data_date": "2026-08-25",
  "parameters_frozen": true,
  "oos_parameter_selection_prohibited": true,
  "costs_included": true,
  "data_fresh": true,
  "risk_review_passed": true,
  "walk_forward_windows": 6,
  "negative_walk_forward_windows": 1,
  "rolling_oos_sessions": 252,
  "rolling_oos_sharpe": 0.50,
  "rolling_oos_max_drawdown": -0.20,
  "annualized_turnover": 15.0,
  "shadow_sessions": 40
}
```

This example only documents the schema and threshold edges. It is not a real
strategy result and must never be entered into the decision ledger as evidence.
