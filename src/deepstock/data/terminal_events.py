"""Explicit terminal cash claims and rights; unknown values never become zero."""
from datetime import date
import math
import re


def validate_event(event, symbol, last_quote, calendar):
    required={'symbol','last_quote_date','effective_date','source_hashes','cash_per_raw_share','cash_payment_date','right'}
    if set(event)!=required or event['symbol']!=symbol or event['last_quote_date']!=last_quote:
        raise ValueError('Terminal event identity/schema differs')
    sources=event['source_hashes']
    if not isinstance(sources,list) or not sources or any(not isinstance(s,str) or not re.fullmatch('[a-f0-9]{64}',s) for s in sources):
        raise ValueError('Terminal source hashes required')
    def number(v):
        if type(v) not in (int,float) or not math.isfinite(v) or v<0:
            raise ValueError('Verified nonnegative terminal value required')
    number(event['cash_per_raw_share'])
    effective=date.fromisoformat(event['effective_date']);last=date.fromisoformat(last_quote)
    payment=date.fromisoformat(event['cash_payment_date'])
    if not last<effective<=payment:
        raise ValueError('Terminal entitlement/payment chronology invalid')
    right=event['right']
    if not isinstance(right,dict) or right.get('kind') not in ('none','verified_schedule'):
        raise ValueError('Unknown contingent right: full valuation remains blocked')
    if right['kind']=='none':
        if set(right)!={'kind'}:
            raise ValueError('Unexpected absent-right fields')
    else:
        if set(right)!={'kind','payment_date','payment_per_raw_share','values_per_raw_share'}:
            raise ValueError('Complete contingent-right valuation/payment evidence required')
        end=date.fromisoformat(right['payment_date']);number(right['payment_per_raw_share'])
        if end<effective:
            raise ValueError('Right paid before entitlement')
        values=right['values_per_raw_share']
        expected={str(d.date()) for d in calendar if effective<=d.date()<end}
        if not isinstance(values,dict) or set(values)!=expected:
            raise ValueError('Every outstanding-right session needs a verified value')
        for v in values.values():number(v)
    return event


def load_events(data_dir, manifest):
    """Only a new manifest can opt in; pin registered evidence and source bytes."""
    import json
    from .store import DataStore, digest
    spec=manifest.get('terminal_events')
    if spec is None:
        return {}
    if set(spec)!={'file','version','sha256','sources'} or spec['file']!='terminal-events.json':
        raise ValueError('Pinned terminal event manifest required')
    store=DataStore();path=data_dir/spec['file']
    record=store.get(spec['version'])
    if digest(path)!=spec['sha256'] or record['raw_sha256']!=spec['sha256']:
        raise ValueError('Terminal event bytes changed')
    events=json.loads(store.verified_path(record,'raw').read_text(encoding='utf-8'))
    known=set()
    for version in spec['sources']:
        record=store.get(version);store.verified_path(record,'raw');known.add(record['raw_sha256'])
    if not isinstance(events,dict) or any(not set(e['source_hashes']).issubset(known) for e in events.values()):
        raise ValueError('Terminal source evidence unverified')
    return events
