from copy import deepcopy
from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd
import pytest

from deepstock.adx_shadow import SettlementBlocked, advance, build_targets, bundle, open_state, validate_bundle
from scripts.run_adx_shadow import require_before_next_open


def fixture():
    dates=pd.bdate_range('2026-10-05',periods=5)
    prices=pd.DataFrame({'SPY':[100.,110.,121.,120.,122.],'SHY':[100.]*5},index=dates)
    targets={name:{'weights':{'SPY':1.},'regime':'bull','route':'stocks'} for name in ('adx','arc','spy')}
    return dates,prices,targets


def record(db,prices,targets):
    return advance(db,prices.index,prices,targets,'a'*64,datetime.now(timezone.utc).isoformat())


def test_only_saved_plan_earns_return_cost_and_restart(tmp_path):
    dates,prices,targets=fixture();db=open_state(tmp_path/'state.sqlite3')
    record(db,prices.iloc[:2],targets)
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0]==0
    record(db,prices.iloc[:3],targets)
    saved=json.loads(db.execute('SELECT payload FROM settlements').fetchone()[0])
    assert saved['accounts']['adx']['net_return']==pytest.approx(.1-.0005)
    db.close();db=open_state(tmp_path/'state.sqlite3')
    assert record(db,prices.iloc[:3],targets)=='already_recorded'
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0]==1
    value=bundle(db,str(dates[2].date()),'a'*64)
    assert value['sessions']==1 and not value['review_ready']
    validate_bundle(value)


def test_missing_held_price_keeps_prior_plan_atomic(tmp_path):
    _,prices,targets=fixture();db=open_state(tmp_path/'state.sqlite3')
    record(db,prices.iloc[:2],targets)
    damaged=prices.iloc[:3].copy();damaged.iloc[-1,0]=np.nan
    safe={name:{'weights':{'SHY':1.},'regime':'bear','route':'safe'} for name in targets}
    with pytest.raises(SettlementBlocked):record(db,damaged,safe)
    assert db.execute('SELECT count(*) FROM plans').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0]==0
    with pytest.raises(SettlementBlocked):record(db,prices.assign(SPY=[100,110,np.nan,120,122]),safe)
    assert db.execute('SELECT count(*) FROM plans').fetchone()[0]==1


def test_missed_plan_starts_new_segment_without_backdated_returns(tmp_path):
    _,prices,targets=fixture();db=open_state(tmp_path/'state.sqlite3')
    record(db,prices.iloc[:2],targets);record(db,prices,targets)
    assert db.execute('SELECT max(segment) FROM plans').fetchone()[0]==2
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0]==0
    assert db.execute('SELECT reason FROM incidents').fetchone()[0]=='missing_prior_plan'


def test_cooldown_uses_saved_prospective_state_and_does_not_mutate(tmp_path):
    _,prices,targets=fixture();db=open_state(tmp_path/'state.sqlite3')
    record(db,prices.iloc[:2],targets)
    changed=deepcopy(targets)
    for name in ('adx','arc'):
        changed[name].update(weights={'SHY':1.},candidate_weights={'SHY':1.},execution_route='safe',route='safe')
    original=deepcopy(changed);record(db,prices.iloc[:3],changed)
    plan=json.loads(db.execute('SELECT payload FROM plans ORDER BY date DESC').fetchone()[0])
    assert plan['targets']['adx']['route_age']==0
    assert plan['targets']['adx']['weights']=={'SPY':1.}
    assert changed==original


def test_future_returns_cannot_change_saved_prefix(tmp_path):
    _,prices,targets=fixture()
    values=[]
    for index,last_price in enumerate((1.,999.)):
        db=open_state(tmp_path/f'{index}.sqlite3');changed=prices.copy();changed.iloc[-1,0]=last_price
        record(db,changed.iloc[:2],targets);record(db,changed.iloc[:3],targets)
        values.append(db.execute('SELECT payload FROM settlements').fetchone()[0])
    assert values[0]==values[1]


def test_plan_must_finish_before_next_exchange_open():
    require_before_next_open('2026-10-12',datetime(2026,10,13,13,29,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match='backdated'):
        require_before_next_open('2026-10-12',datetime(2026,10,13,13,30,tzinfo=timezone.utc))


def test_bundle_cannot_export_prices_or_claim_review_early(tmp_path):
    db=open_state(tmp_path/'state.sqlite3');value=bundle(db,'2026-10-12','a'*64)
    value['review_ready']=True
    with pytest.raises(ValueError):validate_bundle(value)
    value['review_ready']=False;value['summaries']['adx']['prices']=123
    with pytest.raises(ValueError):validate_bundle(value)


def test_complete_routes_warmup_prefix_and_member_gap_gate():
    dates=pd.bdate_range('2024-01-02',periods=506)
    stocks=list('ABCDE')
    symbols=['SPY','QQQ','IWM','TLT','IEF','GLD','SHY',*stocks]
    prices=pd.DataFrame({s:100+np.arange(506)*(.01 if s=='SHY' else .1) for s in symbols},index=dates)
    members=pd.DataFrame(True,index=dates,columns=stocks)
    turnover=pd.DataFrame(1e8,index=dates,columns=stocks)
    ohlc=pd.DataFrame({'high':prices.SPY+1,'low':prices.SPY-1,'close':prices.SPY},index=dates)
    first=build_targets(prices.iloc[:504],members.iloc[:504],turnover.iloc[:504],ohlc.iloc[:504])
    assert set(first)=={'adx','arc','spy'}
    for target in first.values():assert sum(target['weights'].values())==pytest.approx(1)
    changed=prices.copy();changed.iloc[504:,:]=999
    assert first==build_targets(changed.iloc[:504],members.iloc[:504],turnover.iloc[:504],ohlc.iloc[:504])
    prices.iloc[300,-1]=np.nan
    with pytest.raises(ValueError,match='member quote gap'):
        build_targets(prices,members,turnover,ohlc)
