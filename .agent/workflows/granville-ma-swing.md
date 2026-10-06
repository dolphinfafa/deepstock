# Granville-Inspired ETF Moving-Average Swing

Independent strategy `granville_ma_swing`, explicitly **Both**, research-only.
User approved a new strategy and separate US/A-share backtests on October 6.
First fixed instruments are SPY and 510300.SH, broad-market ETFs, not a
survivorship-prone selection of winning stocks. Not an ARC route. CANSLIM is
not part of this request; AHL and afternoon momentum remain frozen/paused.

## Preregistration

`config/granville_v1.json` fixes all rules before performance evaluation.
This is an independently defined quantitative proxy, not a reproduction of
the author's undisclosed three-MA/key-candle system. No parameters or winner
may be chosen from new historical results. All history predates registration;
the 2021-08-23 split is a retrospective diagnostic holdout, **not** genuinely
unseen prospective OOS or evidence of paper eligibility.

Reference MAs use 20/60/200 closes, ATR14 and a five-session slope. Indicators
use only the completed day's history; all entries/exits execute on a later
session's open, never at the earlier touch/recovery price. Trend environment:
close above MA200 and MA60 above MA200. Trend entries additionally require
MA20 five-session slope at least 0.1 ATR. Two consecutive closes above their
own MA20 establish recovery confirmation.

Fixed signal families, all retained:

1. `ma_cross`: two-close-confirmed recovery from below MA20 in the fixed trend
   environment; transition into confirmation triggers, not every above-MA day.
2. `trend_pullback` (principal): trend environment, MA20 upward slope, a low
   touching MA20 + 0.25 ATR in the prior/current three completed candles,
   followed by two-close confirmation, and close at most 1 ATR above MA20.
3. `deviation_reversal`: trend environment but no fast-slope requirement;
   close at least 2 ATR below MA20. Exit after recovery to MA20.

One long position, maximum initial exposure 80%, no shorts/leverage. Minimum
hold three sessions applies only to normal signal exits, never the 8% close
risk stop or seven-session time exit. Time exit is submitted after the seventh
held session and fills at the next legal open, so gaps/blocked exits can make
realized holding longer. Normal trend exits need two closes below MA20 minus
0.5 ATR or trend-environment failure. Three-session reentry cooldown. No
same-open exit/reentry. Cash interest is zero in both markets, explicitly.

## Market mechanics and limitations

- US: existing Norgate TOTALRETURN OHLC. Analytical fractional units model
  economic total returns/reinvestment, not historical raw-dollar broker fills.
  2.5bp commission per side (USD1 minimum) plus 2.5bp slippage. No share/volume
  capacity claim without raw OHLCV. This proxy is not an IBKR execution test.
- CN: Tushare raw OHLCV, daily adjustment factors for indicators only, actual
  cash dividends accrued on ex-date and paid on pay-date. CNY100k capital,
  100-share lots, 3bp commission (CNY5 minimum), 5bp slippage, ETF stamp duty
  zero. T+1 exits only; conservatively defer buys/sells at 10% limit opens.
  Capacity capped at 1% of prior session volume, not future full-day volume.
  No Level-2/open-auction fill claim. Unexplained factor changes/splits block
  the entrance rather than inventing corporate actions.
- Both markets also retain fixed 10bp-per-side slippage stress. These costs
  are explicit research assumptions, not a historical broker fee schedule.
- No forced terminal liquidation: final NAV marks positions/receivables;
  pending/unclosed positions and unpaid dividends must be reported.

## Data and evaluation

Only immutable raw/clean versions; reject missing exchange sessions. SSE uses
captured Tushare trade calendar, US XNYS. No minute or restricted `stk_mins`
calls; the existing auction quota remains unchanged. Daily ETF data does not
resume the paused afternoon-momentum strategy.

Evaluate January 2, 2014–September 29, 2026 (exchange-specific sessions),
retaining earlier warm-up. Record full history and retrospective split, plus
fixed 504-history/252-test/252-step windows with no parameter training/search.
Windows are slices of one continuous ledger, never reset to avoid losses.
Both 100% and exposure-matched 80% buy/hold ledgers share execution/cost and
calendar; benchmarks are market-specific, not SPY for China. Report returns,
CAGR, Sharpe, drawdown, turnover, costs, holding, deferred fills, exposure and
cash days for every variant/cost. Never merge USD/CNY returns into one CAGR.

CLI: `python scripts/run_granville_backtest.py`; CN preparation:
`python scripts/download_granville_cn_daily.py`. Use the Deepstock Conda env.
Results and reports are ignored artifacts; the authenticated strategy page
shows two separate market summaries and all fixed comparisons.

## October 6 fixed result

Run: `artifacts/research/granville/20261006T045028-6001c0/`.
Registered CN evidence: `artifacts/data/granville-cn-20261006-v4/`, 3,487 rows
since 2012-05-28 and 14 cash-dividend events, calendar/action audit passed.
No minute request. Equivalent dividend disclosures retain raw auxiliary
revisions; amount/date conflicts still block in clean-v1.6.
US uses the existing 8,474-row licensed total-return OHLC export.

| Full-history base cost | SPY CAGR / MDD | 510300 CAGR / MDD |
| --- | --- | --- |
| ma_cross | +0.34% / -7.07% | -2.46% / -27.63% |
| trend_pullback (fixed principal) | -0.25% / -15.24% | +0.17% / -28.14% |
| deviation_reversal | +0.74% / -14.54% | -1.36% / -28.55% |
| 100% buy/hold | +13.74% / -33.72% | +6.69% / -44.61% |
| 80% initial buy/hold | +12.18% / -30.08% | +5.69% / -40.20% |

Principal average exposure is 12.07% US / 8.35% CN; cash-day fractions are
84.93% / 89.55%. Both have ten complete rolling test windows, with 5 / 7
negative. Base principal drawdown improves on buy/hold but returns are weak;
10bp slippage further reduces CAGR to -1.01% / -0.18%. The strongest full-
history alternative is not promoted. CN holdout principal CAGR +0.44% is a
retrospective diagnostic, not evidence from unseen history.

US analytical TOTALRETURN units include reinvestment; CN buy/hold receives
cash dividends without automatic reinvestment, explicitly. Cross-market
absolute NAVs must not be compared or pooled. Source snapshots/hashes,
configuration, raw/clean input versions and cost-after daily ledgers are saved
at start; this first run used a dirty workspace with untracked new sources
and does not claim to reproduce a clean Git commit.

Next experiments require new fixed specifications: examine short holding
versus slow trend-filter mismatch, opportunity cost while flat, and robustness
on additional predeclared ETFs. Do not alter this version using these returns.
No shadow scheduler, Paper plan or order permission was created.
