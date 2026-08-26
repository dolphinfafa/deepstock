#!/usr/bin/env python3
"""Combine the fixed ETF, Grid, and point-in-time Bull targets into ARC."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
from deepstock.arc import fixed_walk_forward_windows, run_arc_portfolio, summarize_walk_forward, assess_walk_forward
from deepstock.backtest import StrategyConfig, run_backtest
from deepstock.grid import GridConfig, run_grid_backtest
from deepstock.regime import ARCConfig, classify_market_regime, ARC_EXECUTION_STATUS
from deepstock.bull import fixed_bull_candidates
from deepstock.turtle import run_turtle_backtest
from scripts.run_point_in_time_turtle import load_point_in_time_inputs

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--universe-dir',required=True); p.add_argument('--etf-prices',required=True); p.add_argument('--bull-targets'); p.add_argument('--bull-candidate'); p.add_argument('--output-dir',required=True); p.add_argument('--confirmation-days',type=int,default=3); p.add_argument('--min-hold-days',type=int,default=5); p.add_argument('--reentry-cooldown-days',type=int,default=0); p.add_argument('--risk-off-confirmation-days',type=int); p.add_argument('--risk-off-bypasses-min-hold',action='store_true'); p.add_argument('--risk-off-bypasses-reentry-cooldown',action='store_true'); p.add_argument('--rebalance-band',type=float,default=0.05); p.add_argument('--route-cooldown-days',type=int,default=5); a=p.parse_args()
    if bool(a.bull_targets) == bool(a.bull_candidate): raise ValueError('Specify exactly one of --bull-targets or --bull-candidate.')
    arc_config=ARCConfig(confirmation_days=a.confirmation_days,min_hold_days=a.min_hold_days,reentry_cooldown_days=a.reentry_cooldown_days,risk_off_confirmation_days=a.risk_off_confirmation_days,risk_off_bypasses_min_hold=a.risk_off_bypasses_min_hold,risk_off_bypasses_reentry_cooldown=a.risk_off_bypasses_reentry_cooldown)
    prices, eligibility, turnover, routes = load_point_in_time_inputs(Path(a.universe_dir), Path(a.etf_prices))
    etf_symbols=(*arc_config.risk_assets,'SHY')
    etf=prices.loc[:, list(etf_symbols)].dropna()
    signals=classify_market_regime(etf.loc[:, list(arc_config.risk_assets)], arc_config)
    routes=signals.strategy_route.reindex(prices.index).fillna('defensive_etf')
    defensive=run_backtest(etf, StrategyConfig())
    grid=run_grid_backtest(etf.loc[:, ['SPY','SHY']], routes.reindex(etf.index).fillna('defensive_etf'), GridConfig())
    if a.bull_targets:
        bull=pd.read_csv(a.bull_targets, index_col='date', parse_dates=True).reindex(prices.index).fillna(0.0)
    else:
        candidates={candidate.name: candidate for candidate in fixed_bull_candidates(tuple(eligibility.columns))}
        if a.bull_candidate not in candidates: raise ValueError(f'Unknown fixed Bull candidate: {a.bull_candidate}')
        bull=run_turtle_backtest(prices,candidates[a.bull_candidate].config,eligibility=eligibility,turnover=turnover).target_weights
    route_targets={'defensive_etf': defensive.target_weights.reindex(prices.index).fillna(0.0), 'grid_research': grid.target_weights.reindex(prices.index).fillna(0.0), 'stock_turtle_research': bull}
    result=run_arc_portfolio(prices, routes, route_targets, rebalance_band=a.rebalance_band, route_cooldown_days=a.route_cooldown_days)
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    result.daily.to_csv(out/'daily_results.csv',index_label='date'); result.target_weights.to_csv(out/'target_weights.csv',index_label='date')
    windows=fixed_walk_forward_windows(prices.index); wf=summarize_walk_forward(result,windows); wf.to_csv(out/'walkforward_results.csv',index=False)
    (out/'walkforward_acceptance.json').write_text(json.dumps(assess_walk_forward(wf),indent=2,sort_keys=True),encoding='utf-8')
    result.summary['confirmation_days']=a.confirmation_days; result.summary['min_hold_days']=a.min_hold_days; result.summary['reentry_cooldown_days']=a.reentry_cooldown_days; result.summary['risk_off_confirmation_days']=a.risk_off_confirmation_days; result.summary['risk_off_bypasses_min_hold']=a.risk_off_bypasses_min_hold; result.summary['risk_off_bypasses_reentry_cooldown']=a.risk_off_bypasses_reentry_cooldown; result.summary['bull_candidate']=a.bull_candidate; result.summary['rebalance_band']=a.rebalance_band; result.summary['route_cooldown_days']=a.route_cooldown_days
    (out/'summary.json').write_text(json.dumps(result.summary,indent=2,sort_keys=True),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps({'system_name':'Deepstock ARC','execution_status':ARC_EXECUTION_STATUS,'paper_authorized':False,'oos_parameter_selection':'prohibited','confirmation_days':a.confirmation_days,'min_hold_days':a.min_hold_days,'reentry_cooldown_days':a.reentry_cooldown_days,'risk_off_confirmation_days':a.risk_off_confirmation_days,'risk_off_bypasses_min_hold':a.risk_off_bypasses_min_hold,'risk_off_bypasses_reentry_cooldown':a.risk_off_bypasses_reentry_cooldown,'bull_candidate':a.bull_candidate,'rebalance_band':a.rebalance_band,'route_cooldown_days':a.route_cooldown_days},indent=2,sort_keys=True),encoding='utf-8')
    print(json.dumps(result.summary,indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
