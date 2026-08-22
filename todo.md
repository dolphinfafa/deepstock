# TODO

- Verify the TWS trusted-IP allowlist remains limited to the local execution
  node; do not expose or forward its API socket.
- Review the completed fixed-split result: all 18 predeclared parameter sets
  preserved low out-of-sample drawdown (-5.12% to -4.42%) but returned 14.14%
  to 19.22%, versus `SPY` at 39.72%.
- Add a market-regime comparison and assess whether low-beta defensive behavior
  is acceptable despite material `SPY` underperformance; do not begin
  paper-order work before that review.
- Run fixed-split and walk-forward validation for the Turtle module. Initial
  55/20 and 20/10 full-sample tests underperformed `SPY` with higher turnover.
- Build a point-in-time, liquid stock universe with delisted names and run the
  stock Turtle script using a fixed split before comparing it with the ETF
  version. Do not use today's survivors as the historical universe.
- Run the Norgate S&P 500 current-and-past exporter pilot, then implement
  membership-aware Turtle eligibility before downloading all chunks.
- Define the laptop runtime schedule and alert channel before implementing an
  unattended paper-execution agent.
- Start the laptop paper observation period. Record daily plan IDs, TWS health,
  and reconciliation results for eight weeks before enabling paper orders.
- Resolve historical ticker mappings such as `FB` -> `META`, then build a
  point-in-time stock universe including delisted names. The current 15-stock
  result is exploratory and survivor-prone.
- Review the fixed-split stock-Turtle result: all six tested combinations
  underperformed `SPY`; do not enable paper orders or retune on this out-of-
  sample period.
- Add a survivorship-free, point-in-time universe and liquidity/sector controls;
  the corrected `FB` -> `META` result remains based on today's selected names.
- Treat the static sector-cap run as a separate risk-control baseline. It also
  underperformed `SPY`; do not change the cap based on this single interval.
- Validate the adaptive ETF winner with walk-forward windows and more market
  regimes. Its fixed-split result improved risk-adjusted returns but still
  underperformed `SPY`; do not paper-trade it yet.
- Define explicit acceptance criteria for a defensive strategy, including
  whether lower drawdown justifies lower long-run return versus `SPY`.
- On the Windows data node, run a local Norgate data query and record the
  returned schema/provenance; do not use it for live orders.
- Design and run a staged, resumable Norgate Platinum download of current and
  delisted US equities. Record data provenance, update timestamps, symbol
  coverage, and failed symbols; do not include unlicensed redistribution.
- Keep the frozen ETF configuration unchanged while starting the eight-week
  order-free observation and risk review; use Norgate primarily for the
  separate survivorship-free stock-Turtle research.
- Run the Windows Norgate exporter after each completed session and preserve
  eight weeks of unique ETF observation records. Exercise stale-data,
  duplicate-plan, and kill-switch paths before considering paper orders.
- Run the Windows local read-only TWS probe after confirming the trusted-IP
  allowlist remains `127.0.0.1`; never forward port 7497.
