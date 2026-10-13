"""Windows-local targeted unpadded quote/listing and WBA event evidence."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json

import exchange_calendars as xcals
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.granville_history_audit import security_coverage
from deepstock.data.store import DataStore, digest, read_clean_csv, write_json
from scripts.download_norgate_membership import load_capture
from scripts.prepare_granville_us_inventory import load_inventory, inventory_slice
from scripts.rerun_clean_research import capture_code_provenance


def classify_gap(day, raw_dates, adjusted_dates, listing):
    raw=day in raw_dates;adjusted=day in adjusted_dates
    if raw and adjusted:
        return 'new_quote_requires_separate_inventory'
    if raw!=adjusted:
        return 'raw_adjusted_mismatch'
    if listing.get(day)==0:
        return 'no_eligible_trade_not_major_exchange'
    if listing.get(day)==1:
        return 'no_eligible_trade_major_exchange'
    return 'listing_unknown'


def run(args):
    import norgatedata as n
    import importlib.metadata
    from deepstock.data.membership_contract import load_effective_membership_contract
    code=capture_code_provenance()
    if code['tracked_dirty']:
        raise ValueError('Committed clean source required')
    contract,_=load_effective_membership_contract()
    if importlib.metadata.version('norgatedata')!=contract['package_version']:
        raise ValueError('Reviewed Norgate package required')
    args.output_dir.mkdir(parents=True,exist_ok=False)
    _,captured,member_version=load_capture(args.membership_dir,evidence_only=True)
    store=DataStore();inventory,records=load_inventory(args.inventory_dir,args.inventory_version,captured,store)
    dates=xcals.get_calendar('XNYS',start=inventory['source_start'],end=inventory['source_end']).sessions.tz_localize(None)
    refs={r['symbol']:r for r in captured['records']}
    details=[];failures=[];counts=Counter();versions=[]
    for symbol,record in sorted(records.items()):
        if record['status']=='outside_verified_quote_lifetime':
            continue
        meta=refs[symbol]
        members=read_clean_csv(args.membership_dir/meta['daily_file'],version=meta['daily_version'])
        bars,_=inventory_slice(args.inventory_dir,record,{'source_start':inventory['source_start'],'evaluation_end':inventory['source_end']},store)
        _,masks,_=security_coverage(dates,meta,members,bars.date)
        gaps=dates[masks['member_missing']]
        if not len(gaps):
            continue
        try:
            if n.assetid(symbol)!=meta['assetid'] or str(n.first_quoted_date(symbol))!=meta['first_quote'] or str(n.last_quoted_date(symbol))!=str(meta['last_quote']):
                raise ValueError('Frozen quote identity/lifetime changed; separate capture required')
            frames={}
            start,end=str(gaps[0].date()),str(gaps[-1].date())
            for adjustment in ('NONE','TOTALRETURN'):
                frame=pd.DataFrame(n.price_timeseries(symbol,stock_price_adjustment_setting=getattr(n.StockPriceAdjustmentType,adjustment),
                    padding_setting=n.PaddingType.NONE,start_date=start,end_date=end))
                if 'Date' not in frame:
                    raise ValueError('Native quote schema missing')
                versions.append(capture_response('Norgate','granville-gap-'+adjustment+'-'+symbol,frame,'US',restricted=True))
                frames[adjustment]=pd.DatetimeIndex(pd.to_datetime(frame.Date))
            native=pd.DataFrame(n.major_exchange_listed_timeseries(symbol,padding_setting=n.PaddingType.ALLMARKETDAYS,start_date=start,end_date=end))
            versions.append(capture_response('Norgate','granville-listing-'+symbol,native,'US',restricted=True))
            if not {'Date','Major Exchange Listed'}.issubset(native) or native.Date.duplicated().any() or not native['Major Exchange Listed'].isin([0,1]).all():
                raise ValueError('Unverified listing schema/values')
            listing=dict(zip(pd.to_datetime(native.Date),native['Major Exchange Listed']))
            for day in gaps:
                reason=classify_gap(day,frames['NONE'],frames['TOTALRETURN'],listing)
                counts[reason]+=1
                details.append({'symbol':symbol,'date':str(day.date()),'classification':reason,'major_exchange':listing.get(day)})
            print(json.dumps({'diagnosed_securities':len({r['symbol'] for r in details}),'gaps':len(details)}),flush=True)
        except Exception as error:
            failures.append({'symbol':symbol,'reason':str(error)[:250]})
    try:
        wba=pd.DataFrame(n.capital_event_timeseries('WBA-202508',padding_setting=n.PaddingType.NONE,start_date='2025-07-01',end_date='2025-09-05'))
        version=capture_response('Norgate','granville-WBA-capital-events',wba,'US',restricted=True);versions.append(version)
        wba_evidence={'version':version,'rows':len(wba),'fields':list(wba.columns),'full_terminal_proceeds_verified':False}
    except Exception as error:
        wba_evidence={'error':type(error).__name__,'full_terminal_proceeds_verified':False}
    evidence={'code':code,'membership_version':member_version,'inventory_version':args.inventory_version,
        'versions':versions,'details':details,'failures':failures,'wba':wba_evidence,'contract':contract}
    write_json(args.output_dir/'evidence-restricted.json',evidence)
    public={'as_of_date':datetime.now(timezone.utc).date().isoformat(),'status':'execution_evidence_blocked',
        'source_sha256':code['source_sha256'],'inventory_version':args.inventory_version,'membership_version':member_version,
        'diagnosed_gap_count':len(details),'diagnosed_security_count':len({r['symbol'] for r in details}),
        'classifications':dict(counts),'failure_count':len(failures),'wba_capital_event_rows':wba_evidence.get('rows'),
        'wba_full_proceeds_verified':False,'restricted_evidence_sha256':digest(args.output_dir/'evidence-restricted.json'),
        'strategy_run':False,'prices_filled':False,
        'decision':'No last-sale-eligible trade is source evidence, not suspension/price/settlement authorization. Existing 18 windows stay subject to input and terminal gates.'}
    if capture_code_provenance()['source_sha256']!=code['source_sha256']:
        raise ValueError('Source changed during diagnostic')
    write_json(args.output_dir/'summary.json',public)
    print(json.dumps(public),flush=True)
    return public


def main():
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('membership-dir','inventory-dir','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--inventory-version',required=True)
    run(p.parse_args())


if __name__=='__main__':main()
