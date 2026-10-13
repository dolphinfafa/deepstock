# Fixed complete ADX prospective comparison

Compare ADX14 (confirmation5,hold10,reentry20), original ARC (3/5), and SPY.
Fixed stock Turtle55/20/5 USD25mADV, defensiveETF and grid modules;5bp per
traded-weight side,10%L1 band,10-session execution-route cooldown, existing
risk caps. Daily target-weight analytical research, not shares/fills/orders.
Granville and the A-share auction stay independent.

Windows daily Deepstock_ADX_Shadow runs at07:45China via run_scheduled_task.
Installer backs up prior same-name task XML; original07:30defensive task stays.
Capture the entire current/past S&P500 list, security lifetimes, unpadded
TOTALRETURN quotes/activity and reviewed ALLMARKETDAYS effective membership.
Seven ETFs cover every exchange session. Member gaps block; never replace
survivors or pad prices. Historical module/control warmup starts2024; only
persisted prospective plans earn next-day returns. Licensed data, positions
and SQLite remain Windows-local; only bounded aggregate reports cross nodes.

Plans must finish before the following XNYS open. Serialize runs; commit plan
and settlement together, charge initial entry and recover retry reports from
SQLite. Missing daily plans end a segment without fabricated returns; an
unresolved held quote cannot disappear through reset. Save prospective
route-age, rebalance and account-risk state. Late runs never backdate plans.
Missing inputs produce input_blocked; missing held prices produce
settlement_blocked. A late plan produces missed_decision_cutoff with no
invented input failures or prospective plan.

Authenticated POST /api/agent/research/adx-shadow enforces fixed policy,
count/review gates, finite metrics, immutable successful same-date evidence
and idempotent retry. Dates/segments cannot move backwards. Reports/progress
augment ARC without replacing old headline metrics.126settled sessions and4
ADXstate switches permit human review only, never automatic promotion.
