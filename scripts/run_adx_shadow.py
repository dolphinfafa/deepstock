"""Windows-local daily full-input capture, fixed targets and prospective ledger."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from deepstock.adx_shadow import POLICY, SettlementBlocked, advance, build_targets, bundle, open_state
from deepstock.data.capture import capture_response
from deepstock.data.store import DataStore, read_clean_csv, write_json
from deepstock.web.config import settings
from scripts.rerun_clean_research import capture_code_provenance
from deepstock.strategies.cn.auction.forward import _command_lock


def completed_session(now):
    cal=xcals.get_calendar('XNYS',start=POLICY['source_start'],end=str(now.date()))
    sessions=cal.sessions
    return next(s for s in sessions[::-1] if cal.session_close(s)<pd.Timestamp(now))


def require_before_next_open(day, now):
    cal=xcals.get_calendar('XNYS',start=day,end=str((pd.Timestamp(day)+pd.Timedelta(days=15)).date()))
    following=cal.sessions[cal.sessions>pd.Timestamp(day)]
    if pd.Timestamp(now)>=cal.session_open(following[0]):
        raise ValueError('Plan finished after next session open; no backdated prospective plan')


def capture(folder, day, code):
    import norgatedata as n
    from deepstock.data.membership_contract import load_effective_membership_contract
    from deepstock.data.membership import native_membership_view
    import importlib.metadata
    contract,_=load_effective_membership_contract()
    if importlib.metadata.version('norgatedata')!=contract['package_version']:
        raise ValueError('Reviewed Norgate version required')
    start=POLICY['source_start']
    cal=xcals.get_calendar('XNYS',start=start,end=day).sessions.tz_localize(None)
    symbols=sorted(n.watchlist_symbols('S&P 500 Current & Past'))
    if not symbols or len(symbols)!=len(set(symbols)):
        raise ValueError('Complete unique historical list required')
    etfs=['SPY','QQQ','IWM','TLT','IEF','GLD','SHY']
    allbars=[]; members=pd.DataFrame(False,index=cal,columns=symbols)
    failures=[]; versions=[]; records=[]
    for i,symbol in enumerate([*symbols,*etfs]):
        try:
            first,last=n.first_quoted_date(symbol),n.last_quoted_date(symbol)
            if first is None:
                raise ValueError('Quote lifetime unknown')
            meta={'symbol':symbol,'assetid':n.assetid(symbol),'first':str(first),'last':str(last) if last else None}
            versions.append(capture_response('Norgate','adx-quote-life',meta,'US',restricted=True))
            alive=cal[(cal>=pd.Timestamp(first)) & (cal<=pd.Timestamp(last) if last else True)]
            if not len(alive):
                records.append({**meta,'outside_lifetime':True})
                continue
            frame=pd.DataFrame(n.price_timeseries(symbol,stock_price_adjustment_setting=n.StockPriceAdjustmentType.TOTALRETURN,
                padding_setting=n.PaddingType.NONE,start_date=start,end_date=day))
            versions.append(capture_response('Norgate','adx-'+symbol,frame,'US',restricted=True))
            if frame.empty or not {'Date','Open','High','Low','Close','Turnover'}.issubset(frame):
                raise ValueError('Required OHLC/turnover missing')
            observed=pd.DatetimeIndex(pd.to_datetime(frame.Date))
            if observed.has_duplicates or not observed.is_monotonic_increasing or len(observed.difference(alive)):
                raise ValueError('Invalid quote calendar')
            bars=pd.DataFrame({'date':observed,'symbol':symbol,'adjusted_close':frame.Close.to_numpy(),
                'adjusted_high':frame.High.to_numpy(),'adjusted_low':frame.Low.to_numpy(),
                'turnover':frame.Turnover.to_numpy()})
            if symbol in symbols:
                native=pd.DataFrame(n.index_constituent_timeseries(symbol,'S&P 500',padding_setting=n.PaddingType.ALLMARKETDAYS,
                    start_date=start,end_date=day))
                versions.append(capture_response('Norgate','adx-member-'+symbol,native,'US',restricted=True))
                view,_,quality=native_membership_view(native,cal,symbol,first,last,start)
                if quality['missing_required_sessions']:
                    raise ValueError('Unknown effective membership')
                series=view.set_index('date').weight
                members.loc[alive,symbol]=series.reindex(alive).to_numpy(dtype=bool)
                if len(alive[members.loc[alive,symbol]].difference(observed)):
                    raise ValueError('Effective-member quote gap')
            elif len(cal.difference(observed)):
                raise ValueError('ETF dates incomplete')
            allbars.append(bars)
            records.append(meta)
        except Exception as error:
            failures.append({'symbol':symbol,'reason':str(error)[:180]})
        if (i+1)%50==0:
            print(json.dumps({'capture_processed':i+1,'capture_failures':len(failures)}),flush=True)
    if sorted(n.watchlist_symbols('S&P 500 Current & Past'))!=symbols:
        failures.append({'symbol':None,'reason':'Universe changed during capture'})
    manifest={'day':day,'source_start':start,'symbols':symbols,'records':records,'versions':versions,'failures':failures,'code':code}
    write_json(folder/'capture.json',manifest)
    input_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    if failures:
        return None,input_hash,len(failures)
    long=pd.concat(allbars,ignore_index=True)
    path=folder/'prices.csv.gz';path.parent.mkdir(parents=True,exist_ok=True)
    long.to_csv(path,index=False,compression='gzip')
    version=DataStore().import_file(path,{'market':'US','provider':'Norgate','restricted':True,'padding':'NONE',
        'origin':'derived_provider_export','upstream_versions':versions,'timezone':'America/New_York'})['id']
    clean=read_clean_csv(path,version=version);clean.date=pd.to_datetime(clean.date)
    manifest['combined_version']=version
    write_json(folder/'capture.json',manifest)
    input_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    prices=clean.pivot(index='date',columns='symbol',values='adjusted_close').reindex(cal)
    prices=prices.reindex(columns=[*symbols,*etfs])
    turnover=clean.pivot(index='date',columns='symbol',values='turnover').reindex(index=cal,columns=symbols)
    spy=clean.loc[clean.symbol.eq('SPY')].set_index('date')
    ohlc=spy[['adjusted_high','adjusted_low','adjusted_close']].rename(columns={'adjusted_high':'high','adjusted_low':'low','adjusted_close':'close'}).reindex(cal)
    return (prices,members,turnover,ohlc),input_hash,0


def publish(value):
    import httpx
    response=httpx.post(settings.public_base_url+'/api/agent/research/adx-shadow',
        headers={'Authorization':'Bearer '+settings.node_token},json=value,timeout=30)
    response.raise_for_status()


def run(args, root):
    if args.publish_only:
        publish(json.loads((root/'latest.json').read_text()))
        return 0
    now=datetime.now(timezone.utc);day=str(completed_session(now).date())
    db=open_state(root/'state.sqlite3')
    try:
        row=db.execute('SELECT payload FROM plans WHERE date=?',(day,)).fetchone()
        if row:
            value=bundle(db,day,json.loads(row[0])['input_hash'],'already_recorded')
            write_json(root/'latest.json',value);publish(value)
            print(json.dumps({'status':'already_recorded','data_date':day}));return 0
        code=capture_code_provenance()
        if code['tracked_dirty']:
            raise ValueError('Clean committed source required')
        folder=root/'captures'/now.strftime('%Y%m%dT%H%M%S%fZ')
        status='input_blocked';failures=0
        input_hash=hashlib.sha256(json.dumps({'day':day,'code':code},sort_keys=True).encode()).hexdigest()
        try:
            require_before_next_open(day,now)
            inputs,input_hash,failures=capture(folder,day,code)
            if inputs:
                prices,members,turnover,ohlc=inputs
                targets=build_targets(prices,members,turnover,ohlc)
                if capture_code_provenance()['source_sha256']!=code['source_sha256']:
                    raise ValueError('Source changed during capture')
                ready=datetime.now(timezone.utc)
                require_before_next_open(day,ready)
                status=advance(db,prices.index,prices,targets,input_hash,ready.isoformat())
        except (ValueError,KeyError,RuntimeError) as error:
            status='settlement_blocked' if isinstance(error,SettlementBlocked) else 'input_blocked'
            failures=max(1,failures)
            write_json(folder/'blocked.json',{'reason':str(error)[:250],'code':code})
        value=bundle(db,day,input_hash,status,failures)
        write_json(root/'latest.json',value)
        publish(value)
        print(json.dumps({'status':status,'data_date':day,'shadow_sessions':value['sessions'],'coverage_failures':failures}))
        return 0
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish-only',action='store_true')
    args=parser.parse_args()
    root=settings.project_root/'artifacts/research/adx-shadow-v1'
    with _command_lock(root,'adx-shadow') as acquired:
        if not acquired:
            raise RuntimeError('ADX capture already running')
        return run(args,root)


if __name__=='__main__':
    raise SystemExit(main())
