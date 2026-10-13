"""Fixed complete ARC/ADX prospective research, immutable plans and settlements."""
from dataclasses import asdict
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from deepstock.arc import run_arc_portfolio
from deepstock.backtest import StrategyConfig, run_backtest
from deepstock.bull import fixed_bull_candidates
from deepstock.grid import GridConfig, run_grid_backtest
from deepstock.regime import ARCConfig, ADXARCConfig, StrategyRoute, classify_market_regime, classify_adx_market_regime
from deepstock.risk import apply_risk_constraints

POLICY = {'version':'adx-shadow-v1-20261013', 'source_start':'2024-01-02',
          'adx':asdict(ADXARCConfig(confirmation_days=5,min_hold_days=10,reentry_cooldown_days=20)),
          'arc':asdict(ARCConfig()), 'bull':'strict_liquidity_55_20_5',
          'transaction_cost_bps':5.0,'rebalance_band':.10,'route_cooldown_days':10,
          'minimum_sessions':126,'minimum_switches':4,
          'initialization':'historical module/control warm start; prospective accounts start at equity 1',
          'timing':'close signal, next-session close-to-close analytical return; not executable shares'}
POLICY_HASH = hashlib.sha256(json.dumps(POLICY,sort_keys=True).encode()).hexdigest()
CONTROLLERS = ('adx','arc','spy')


class SettlementBlocked(ValueError):
    """A saved plan is unresolved; it cannot be discarded by a new segment."""


class ShadowBundle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_hash: str
    captured_at_utc: AwareDatetime
    data_date: str
    status: str
    segment: int = Field(ge=1)
    sessions: int = Field(ge=0)
    switches: int = Field(ge=0)
    review_ready: bool
    input_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    coverage_failures: int = Field(ge=0)
    summaries: dict[str, dict[str, float | int | None]]
    evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def open_state(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS plans
        (date TEXT PRIMARY KEY,segment INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlements
        (date TEXT PRIMARY KEY,segment INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incidents (date TEXT PRIMARY KEY,reason TEXT NOT NULL);
    ''')
    prior=db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()
    if prior and prior[0]!=POLICY_HASH:
        raise ValueError('Shadow rules changed: use a separate observation store')
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('policy',?)",(POLICY_HASH,))
    db.commit()
    return db


def build_targets(prices,eligibility,turnover,ohlc):
    from deepstock.turtle import run_turtle_backtest
    if len(prices)<504 or not prices.index.equals(eligibility.index) or not prices.index.equals(ohlc.index):
        raise ValueError('At least 504 exact common historical sessions required')
    risk_symbols=tuple(sorted(eligibility.columns))
    etf=prices[[*ARCConfig().risk_assets,'SHY']]
    if etf.isna().any().any():
        raise ValueError('Incomplete ETF history')
    if (eligibility & prices.reindex(columns=risk_symbols).isna()).any().any():
        raise ValueError('Effective-member quote gap: no historical padding')
    candidate=next(c for c in fixed_bull_candidates(risk_symbols) if c.name==POLICY['bull'])
    bull=run_turtle_backtest(prices,candidate.config,eligibility=eligibility,turnover=turnover).target_weights
    defensive=run_backtest(etf,StrategyConfig()).target_weights
    signals={'adx':classify_adx_market_regime(ohlc,ADXARCConfig(**POLICY['adx'])),
             'arc':classify_market_regime(etf[list(ARCConfig().risk_assets)],ARCConfig())}
    plans={}
    for name,signal in signals.items():
        routes=signal.strategy_route
        grid=run_grid_backtest(etf[['SPY','SHY']],routes,GridConfig()).target_weights
        result=run_arc_portfolio(prices,routes,{
            StrategyRoute.STOCK_TURTLE_RESEARCH.value:bull,
            StrategyRoute.DEFENSIVE_ETF.value:defensive,
            StrategyRoute.GRID_RESEARCH.value:grid},rebalance_band=.10,route_cooldown_days=10)
        # Missing held returns must never benefit from legacy engine fillna(0).
        held=result.executed_weights.gt(0)
        unavailable=prices.isna() | prices.shift(1).isna()
        unavailable.iloc[0]=False
        if (held & unavailable).any().any():
            raise ValueError('Warm-up module held quote gap; no implicit terminal liquidation')
        latest=signal.iloc[-1]
        route_frames={StrategyRoute.STOCK_TURTLE_RESEARCH.value:bull,
                      StrategyRoute.DEFENSIVE_ETF.value:defensive,
                      StrategyRoute.GRID_RESEARCH.value:grid}
        raw=route_frames[str(latest.strategy_route)].iloc[[-1]].reindex(columns=prices.columns,fill_value=0.)
        capped=apply_risk_constraints(raw,pd.DataFrame(0.,index=raw.index,columns=raw.columns)).iloc[0]
        execution_routes=routes.astype(str).shift(1).fillna(routes.astype(str).iloc[0])
        changes=np.flatnonzero(execution_routes.ne(execution_routes.shift()).to_numpy())
        age=len(execution_routes)-1-int(changes[-1]) if len(changes)>1 else POLICY['route_cooldown_days']
        plans[name]={'weights':{s:float(w) for s,w in result.target_weights.iloc[-1].items() if w>0},
                     'candidate_weights':{s:float(w) for s,w in capped.items() if w>0},
                     'regime':str(latest.regime),'route':str(latest.strategy_route),
                     'execution_route':str(execution_routes.iloc[-1]),'route_age':age}
    plans['spy']={'weights':{'SPY':1.0},'regime':'benchmark','route':'benchmark'}
    return plans


def advance(db, dates, prices, targets, input_hash, captured_at):
    """Only a previously persisted close plan can earn the next day's return."""
    if len(dates)<2 or not prices.index.equals(dates):
        raise ValueError('Exact settlement calendar required')
    targets=deepcopy(targets)
    for name in CONTROLLERS:
        weights=targets[name]['weights']
        for field in ('weights','candidate_weights'):
            values=targets[name].get(field,weights)
            if not values or any(not np.isfinite(w) or w<0 for w in values.values()) or abs(sum(values.values())-1)>1e-8:
                raise ValueError('Long-only fully allocated analytical targets required')
            for symbol in values:
                if symbol not in prices or not np.isfinite(prices.iloc[-1][symbol]) or prices.iloc[-1][symbol]<=0:
                    raise ValueError('Current target price unavailable')
    day=str(dates[-1].date())
    if db.execute('SELECT 1 FROM plans WHERE date=?',(day,)).fetchone():
        return 'already_recorded'
    prior=db.execute('SELECT * FROM plans ORDER BY date DESC LIMIT 1').fetchone()
    if prior and prior['date']>=day:
        raise ValueError('Shadow input date moved backwards')
    segment=prior['segment'] if prior else 1
    settled=None
    payload={'date':day,'input_hash':input_hash,'captured_at_utc':captured_at,'targets':targets,'policy_hash':POLICY_HASH}
    if prior:
        # Do not backdate a plan for any missed session.
        if prior['date']!=str(dates[-2].date()):
            # Before restarting a missed observation, verify the old saved
            # outcome. A terminal quote failure cannot disappear in a reset.
            saved=json.loads(prior['payload'])
            next_dates=dates[dates>pd.Timestamp(prior['date'])]
            if not len(next_dates) or pd.Timestamp(prior['date']) not in prices.index:
                raise SettlementBlocked('Prior plan price scope unavailable')
            for account in saved['targets'].values():
                for symbol in account['weights']:
                    values=prices.loc[[pd.Timestamp(prior['date']),next_dates[0]],symbol]
                    if not np.isfinite(values).all() or (values<=0).any():
                        raise SettlementBlocked('Unresolved held settlement: keep prior plan pending')
            segment+=1
        else:
            planned=json.loads(prior['payload'])
            prior_settled=db.execute('SELECT payload FROM settlements WHERE segment=? ORDER BY date DESC LIMIT 1',(segment,)).fetchone()
            prior_accounts=json.loads(prior_settled[0])['accounts'] if prior_settled else {}
            accounts={}
            for name in CONTROLLERS:
                target=planned['targets'][name]['weights']
                previous=prior_accounts.get(name,{'equity':1.,'peak':1.,'weights':{}})
                symbols=sorted(set(target)|set(previous['weights']))
                returns={}
                for symbol in target:
                    series=prices.loc[[pd.Timestamp(prior['date']),pd.Timestamp(day)],symbol]
                    if not np.isfinite(series).all() or (series<=0).any():
                        raise SettlementBlocked('Held settlement quote missing: keep pending')
                    returns[symbol]=float(series.iloc[1]/series.iloc[0]-1)
                traded=sum(abs(target.get(s,0)-previous['weights'].get(s,0)) for s in symbols)
                cost=traded*.0005
                gross=sum(w*returns[s] for s,w in target.items())
                equity=previous['equity']*(1+gross-cost)
                # These are the legacy daily target-weight analytical accounts.
                accounts[name]={'equity':equity,'peak':max(previous['peak'],equity),'weights':target,
                    'gross_return':gross,'net_return':gross-cost,'cost':cost,'turnover':traded/2,
                    'execution_route':planned['targets'][name]['route']}
                if name!='spy':
                    candidate=targets[name].get('candidate_weights',targets[name]['weights'])
                    if gross<=-.05 or equity/accounts[name]['peak']-1<=-.15:
                        candidate={'SHY':1.0}
                    old=planned['targets'][name]
                    execution_route=targets[name].get('execution_route',targets[name]['route'])
                    old_route=old.get('execution_route',old['route'])
                    age=0 if execution_route!=old_route else old.get('route_age',10)+1
                    distance=sum(abs(candidate.get(s,0)-target.get(s,0)) for s in set(candidate)|set(target))
                    targets[name]['weights']=target if distance<=.10 or age<10 else candidate
                    targets[name]['route_age']=age
            settled={'date':day,'accounts':accounts,'plan_date':prior['date'],'input_hash':input_hash}
    with db:
        if prior and not settled:
            db.execute('INSERT OR IGNORE INTO incidents VALUES (?,?)',(day,'missing_prior_plan'))
        if settled:
            db.execute('INSERT INTO settlements VALUES (?,?,?)',(day,segment,json.dumps(settled,sort_keys=True,allow_nan=False)))
        db.execute('INSERT INTO plans VALUES (?,?,?)',(day,segment,json.dumps(payload,sort_keys=True,allow_nan=False)))
    return 'recorded'


def bundle(db, data_date, input_hash, status='recorded', failures=0):
    plans=db.execute('SELECT * FROM plans ORDER BY date').fetchall()
    segment=plans[-1]['segment'] if plans else 1
    current=[json.loads(r['payload']) for r in plans if r['segment']==segment]
    settlements=[json.loads(r[0]) for r in db.execute('SELECT payload FROM settlements WHERE segment=? ORDER BY date',(segment,))]
    switches=sum(a['targets']['adx']['regime']!=b['targets']['adx']['regime'] for a,b in zip(current,current[1:]))
    summaries={}
    for name in CONTROLLERS:
        returns=np.array([r['accounts'][name]['net_return'] for r in settlements])
        equity=np.r_[1.,np.cumprod(1+returns)]
        summaries[name]={'total_return':float(equity[-1]-1), 'max_drawdown':float((equity/np.maximum.accumulate(equity)-1).min()),
            'sharpe':float(returns.mean()/returns.std(ddof=0)*np.sqrt(252)) if len(returns)>1 and returns.std(ddof=0)>0 else None,
            'transaction_cost':float(sum(r['accounts'][name]['cost'] for r in settlements)),
            'turnover':float(sum(r['accounts'][name]['turnover'] for r in settlements))}
    evidence=hashlib.sha256(json.dumps([dict(r) for r in plans]+settlements,sort_keys=True).encode()).hexdigest()
    return ShadowBundle(policy_hash=POLICY_HASH,captured_at_utc=datetime.now(timezone.utc),data_date=data_date,
        status=status,segment=segment,sessions=len(settlements),switches=switches,
        review_ready=len(settlements)>=126 and switches>=4,input_hash=input_hash,
        coverage_failures=failures,summaries=summaries,evidence_hash=evidence).model_dump(mode='json')


def validate_bundle(value):
    item=ShadowBundle.model_validate(value)
    if item.policy_hash!=POLICY_HASH or set(item.summaries)!=set(CONTROLLERS):
        raise ValueError('Unexpected shadow policy/controllers')
    if len(item.data_date)!=10 or str(date.fromisoformat(item.data_date))!=item.data_date:
        raise ValueError('Canonical date required')
    if item.review_ready!=(item.sessions>=126 and item.switches>=4):
        raise ValueError('Shadow count/review mismatch')
    if item.captured_at_utc>datetime.now(timezone.utc)+pd.Timedelta(minutes=5):
        raise ValueError('Future capture')
    if pd.Timestamp(item.data_date).date()>datetime.now(timezone.utc).date():
        raise ValueError('Future data')
    if item.status not in ('recorded','already_recorded','input_blocked','settlement_blocked','missed_decision_cutoff'):
        raise ValueError('Unexpected shadow status')
    keys={'total_return','max_drawdown','sharpe','transaction_cost','turnover'}
    for summary in item.summaries.values():
        if set(summary)!=keys or any(v is not None and (isinstance(v,bool) or not np.isfinite(v)) for v in summary.values()):
            raise ValueError('Only finite allowed aggregate metrics accepted')
        if any(summary[k] is None for k in keys-{'sharpe'}):
            raise ValueError('Required account metric unavailable')
        if summary['max_drawdown']>0 or summary['transaction_cost']<0 or summary['turnover']<0:
            raise ValueError('Invalid aggregate metric range')
    return item


def semantic_identity(value):
    """Retries may change only delivery timestamp/status, never recorded facts."""
    return {k:v for k,v in value.items() if k not in ('captured_at_utc','status')}
