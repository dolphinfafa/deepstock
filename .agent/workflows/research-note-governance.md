# Research Note Governance

## Purpose

Deepstock treats user-provided notes as research hypotheses, not instructions that
silently change a strategy. Every note is preserved, assessed, shown to the user,
and waits for an explicit user decision before it can enter a research backlog.

## Required sequence

1. Preserve the original note without rewriting its claims.
2. Assess its effect on either the global research process or one named strategy.
3. Explain the assessment to the user before applying it.
4. Record the user's final decision separately from the agent recommendation.
5. If approved, create a new, preregistered research candidate. Never mutate a
   frozen strategy version, historical report, authorization, or live plan.

## Assessment fields

Each assessment records:

- `impact_scope`: affected strategies, data, risk, execution, or governance.
- `causal_logic`: why the idea could work and what observation could disprove it.
- `novelty`: new hypothesis, known literature, or duplication of existing work.
- `point_in_time_data`: availability and timestamp integrity of required data.
- `backtestability`: whether entries, exits, universe, costs, and benchmark can be
  expressed without discretionary hindsight.
- `leakage_risk`: future information, survivorship, revised data, or publication
  timing risks.
- `overfitting_risk`: number of choices relative to independent samples.
- `evidence`: supporting or conflicting evidence currently available.
- `next_test`: smallest fixed test that can answer the important question.

## Recommendations

- `reject`: no plausible causal basis, irreparable leakage, or outside scope.
- `observe`: interesting but data or executable rules are not ready.
- `test`: suitable for a preregistered research experiment.
- `adopt`: evidence is already adequate for inclusion in the research method.

`adopt` still does not authorize trading. Strategy promotion and execution
authorization remain governed by their own gates.

## User decisions

The valid final decisions are `rejected`, `deferred`, `approved_for_test`, and
`approved_for_method`. Until one is recorded, the note remains read-only and has
no effect on research code, frozen configurations, or execution.

## Commands

```bash
conda run -n deepstock python scripts/register_research_note.py \
  --text "..." --scope global --assessment assessment.json --recommendation test

conda run -n deepstock python scripts/decide_research_note.py NOTE_ID \
  --decision approved_for_test
```

New-strategy notes are independent by default. Once approved, register their
own strategy ID and link the note to that strategy; a controller integration
is a separate decision, never an implicit consequence of note approval.
