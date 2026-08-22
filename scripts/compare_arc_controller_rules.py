#!/usr/bin/env python3
"""Compare predeclared ARC controller hysteresis rules without selection."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
from deepstock.regime import ARCConfig, classify_market_regime, regime_statistics

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--prices',required=True); p.add_argument('--output-dir',required=True); a=p.parse_args()
    raw=pd.read_csv(a.prices); raw['date']=pd.to_datetime(raw['date']); base=ARCConfig(); prices=raw.pivot(index='date',columns='symbol',values='adjusted_close').sort_index().loc[:,list(base.risk_assets)].dropna()
    rules={'current_3_confirm_5_hold':base, 'fixed_5_confirm_10_hold':ARCConfig(confirmation_days=5,min_hold_days=10)}
    rows=[]; out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    for name,config in rules.items():
        signals=classify_market_regime(prices,config); stats=regime_statistics(signals); transitions=signals['regime'].astype(str).ne(signals['regime'].astype(str).shift())
        bull_range=((signals['regime'].astype(str).shift().isin(['bull','range'])) & signals['regime'].astype(str).isin(['bull','range']) & transitions).sum()
        rows.append({'rule':name,'confirmation_days':config.confirmation_days,'min_hold_days':config.min_hold_days,'state_switches':stats['state_switches'],'average_hold_days':stats['average_hold_days'],'bull_range_switches':int(bull_range)})
        signals.to_csv(out/f'{name}_signals.csv',index_label='date')
    table=pd.DataFrame(rows); table.to_csv(out/'controller_comparison.csv',index=False)
    (out/'manifest.json').write_text(json.dumps({'policy':'Predeclared diagnostic comparison; no OOS selection or production rule change.'},indent=2),encoding='utf-8'); print(table.to_csv(index=False)); return 0
if __name__=='__main__': raise SystemExit(main())
