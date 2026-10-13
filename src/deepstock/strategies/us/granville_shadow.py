"""Prospective Granville analytical accounts; no broker/order capability."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Literal

import exchange_calendars as xcals
import numpy as np
import pandas as pd
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt

from deepstock.data.store import ROOT
from deepstock.strategies.both.granville import generate_signals


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


POLICY = json.loads((ROOT / 'config/granville_shadow_v1.json').read_text(encoding='utf-8'))
CONFIG = json.loads((ROOT / 'config/granville_portfolio_v1.json').read_text(encoding='utf-8'))
SIGNALS = json.loads((ROOT / CONFIG['signal_config']).read_text(encoding='utf-8'))
if fingerprint(CONFIG) != POLICY['base_config_sha256'] or fingerprint(SIGNALS) != POLICY['signal_config_sha256']:
    raise ValueError('Frozen Granville contracts changed; register a new shadow experiment')
RULE = SIGNALS['signal']
POLICY_HASH = fingerprint(POLICY)
ACCOUNTS = tuple(POLICY['accounts'])


class SettlementBlocked(ValueError):
    pass


class Summary(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    total_return: float
    max_drawdown: float = Field(le=0)
    sharpe: float | None
    costs_usd: float = Field(ge=0)
    turnover: float = Field(ge=0)
    position_count: StrictInt = Field(ge=0, le=5)
    buy_fills: StrictInt = Field(ge=0)
    sell_fills: StrictInt = Field(ge=0)


class ShadowBundle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    captured_at_utc: AwareDatetime
    data_date: date
    status: Literal['recorded', 'already_recorded', 'input_blocked', 'settlement_blocked', 'missed_decision_cutoff', 'preflight_ready']
    planned_sessions: StrictInt = Field(ge=0)
    shadow_sessions: StrictInt = Field(ge=0)
    missed_plan_sessions: StrictInt = Field(ge=0)
    coverage_failures: StrictInt = Field(ge=0)
    review_ready: StrictBool
    summaries: dict[str, Summary]
    input_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    broker_accounting_ready: Literal[False] = False
    paper_authorized: Literal[False] = False
    live_authorized: Literal[False] = False


def validate_bundle(value):
    item = ShadowBundle.model_validate(value)
    if item.policy_hash != POLICY_HASH or set(item.summaries) != set(ACCOUNTS):
        raise ValueError('Unexpected Granville shadow contract/accounts')
    if item.review_ready != (item.shadow_sessions >= POLICY['minimum_review_sessions']):
        raise ValueError('Review count mismatch')
    if item.shadow_sessions > item.planned_sessions or item.planned_sessions > item.shadow_sessions + 1:
        raise ValueError('Plan/settlement counts mismatch')
    now = datetime.now(timezone.utc)
    if item.data_date > now.date() or item.captured_at_utc > now + pd.Timedelta(minutes=5):
        raise ValueError('Future evidence')
    return item


def semantic_identity(value):
    return {k: v for k, v in value.items() if k not in ('captured_at_utc', 'status')}


def open_state(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (date TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlements (date TEXT PRIMARY KEY, payload TEXT NOT NULL);
    ''')
    prior = db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()
    if prior and prior[0] != POLICY_HASH:
        db.close()
        raise ValueError('Separate state store required for a new experiment')
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('policy', ?)", (POLICY_HASH,))
    db.commit()
    return db


def initial_accounts():
    return {name: {'cash': POLICY['initial_capital'], 'nav': POLICY['initial_capital'],
                   'positions': {}, 'last_exits': {}, 'initialized': False} for name in ACCOUNTS}


def latest_accounts(db):
    rows = [(r['date'], json.loads(r['payload'])['accounts']) for table in ('plans', 'settlements')
            for r in db.execute(f'SELECT * FROM {table} ORDER BY date DESC LIMIT 1')]
    # A same-date plan contains the newly frozen exits; prefer it over settlement.
    return deepcopy(max(rows, key=lambda r: r[0])[1]) if rows else initial_accounts()


def required_symbols(db):
    result = {'SPY'}
    for account in latest_accounts(db).values():
        result.update(account['positions'])
    row = db.execute('SELECT payload FROM plans ORDER BY date DESC LIMIT 1').fetchone()
    if row:
        for intent in json.loads(row[0])['intents'].values():
            result.update(c['symbol'] for c in intent['buys'])
    return result


def require_timing(day, ready):
    stamp = pd.Timestamp(ready)
    if stamp.tzinfo is None:
        raise ValueError('Aware capture time required')
    cal = xcals.get_calendar('XNYS', start=day, end=str((pd.Timestamp(day) + pd.Timedelta(days=15)).date()))
    if stamp < cal.session_close(pd.Timestamp(day)):
        raise ValueError('Decision precedes completed close')
    following = cal.sessions[cal.sessions > pd.Timestamp(day)][0]
    if stamp >= cal.session_open(following):
        raise ValueError('Decision missed next open; no backdated plan')


def decision(accounts, dates, bars, eligible):
    """Only the latest completed close produces a new decision, never old days."""
    day = str(dates[-1].date())
    signals, ranking = {}, []
    held = set().union(*(a['positions'] for a in accounts.values()))
    for symbol in sorted(set(eligible) | held):
        if symbol not in bars:
            raise ValueError('Required current/held quotes unavailable')
        frame = bars[symbol].reindex(dates)
        if not np.isfinite(frame.adjusted_close.iloc[-1]) or frame.adjusted_close.iloc[-1] <= 0:
            raise ValueError('Current eligible/held quote unavailable')
        signal = generate_signals(frame, RULE).iloc[-1]
        signals[symbol] = signal
        if symbol not in eligible or len(dates) <= CONFIG['relative_strength_sessions']:
            continue
        lookback = frame.adjusted_close.iloc[-CONFIG['relative_strength_sessions'] - 1]
        turnover = frame.turnover.iloc[-CONFIG['liquidity_sessions']:].mean() if frame.turnover.iloc[-CONFIG['liquidity_sessions']:].notna().all() else np.nan
        if (signal['ready'] and signal['trend_pullback'] and np.isfinite(lookback) and lookback > 0
                and turnover >= CONFIG['markets']['US']['minimum_average_turnover']):
            ranking.append({'symbol': symbol, 'strength': float(frame.adjusted_close.iloc[-1] / lookback - 1),
                            'prior_volume': float(frame.volume.iloc[-1])})
    ranking.sort(key=lambda row: (-row['strength'], row['symbol']))
    intents = {}
    for name, account in accounts.items():
        for symbol, position in account['positions'].items():
            if name not in ('base', 'stress') or position.get('pending'):
                continue
            held_sessions = dates.get_loc(pd.Timestamp(day)) - dates.get_loc(pd.Timestamp(position['entry_date'])) + 1
            stop = position['last_mark'] / position['entry_execution'] - 1 <= -RULE['stop_loss']
            reason = ('close_stop' if stop else 'time_exit' if held_sessions >= RULE['maximum_hold_sessions']
                      else 'signal_exit' if signals[symbol]['trend_exit'] and held_sessions >= RULE['minimum_hold_sessions'] else '')
            if reason:
                position['pending'] = {'reason': reason, 'signal_date': day}
        sells = {s: deepcopy(p['pending']) for s, p in account['positions'].items() if p.get('pending')}
        for symbol, sale in sells.items():
            volume = bars[symbol].reindex(dates).volume.iloc[-1]
            if not np.isfinite(volume) or volume < 0:
                raise ValueError('Held exit lacks verified prior-close volume')
            sale['prior_volume'] = float(volume)
        if name in ('base', 'stress'):
            buys = [deepcopy(r) for r in ranking if r['symbol'] not in account['positions']
                    and (r['symbol'] not in account['last_exits'] or dates.get_loc(pd.Timestamp(day)) + 1
                         - dates.get_loc(pd.Timestamp(account['last_exits'][r['symbol']])) > RULE['cooldown_sessions'])]
        else:
            buys = [] if account['initialized'] else [{'symbol': 'SPY', 'prior_volume': None}]
        intents[name] = {'sells': sells, 'buys': buys}
    return intents


def quote(bars, symbol, day, position=None):
    if symbol not in bars or pd.Timestamp(day) not in bars[symbol].index:
        raise SettlementBlocked('Required held/intent quote missing')
    frame = bars[symbol]
    row = frame.loc[pd.Timestamp(day)].copy()
    fields = ['open', 'high', 'low', 'close', 'adjusted_open', 'adjusted_high', 'adjusted_low', 'adjusted_close', 'volume', 'turnover']
    if not np.isfinite(row[fields].to_numpy(dtype=float)).all() or (row[fields[:8]] <= 0).any() or (row[fields[8:]] < 0).any():
        raise SettlementBlocked('Invalid settlement quote; no stale execution/valuation')
    for prefix in ('', 'adjusted_'):
        if (row[prefix + 'high'] < max(row[prefix + field] for field in ('open', 'close', 'low'))
                or row[prefix + 'low'] > min(row[prefix + field] for field in ('open', 'close', 'high'))):
            raise SettlementBlocked('Invalid settlement OHLC')
    if position:
        anchor = frame.reindex([pd.Timestamp(position['mark_date'])]).adjusted_close.iloc[0]
        if not np.isfinite(anchor) or anchor <= 0:
            raise SettlementBlocked('Held total-return normalization anchor missing')
        scale = position['last_mark'] / anchor
        row[['adjusted_open', 'adjusted_high', 'adjusted_low', 'adjusted_close']] *= scale
    return row


def settle_account(name, account, intent, bars, dates, day):
    cfg = CONFIG['markets']['US']
    slip = (cfg['stress_slippage_bps'] if name == 'stress' else cfg['slippage_bps']) / 10000
    positions = account['positions']
    marks = {s: quote(bars, s, day, p) for s, p in positions.items()}
    opening_nav = account['cash'] + sum(p['units'] * marks[s].adjusted_open for s, p in positions.items())
    prior_nav = account['nav']
    fees = slippage = traded = 0.
    fills, sold = [], set()
    blocked = delayed = 0

    def fill(symbol, action, units, row, reason):
        nonlocal fees, slippage, traded
        execution = row.adjusted_open * (1 + slip if action == 'BUY' else 1 - slip)
        notional = units * execution
        charge = max(cfg['minimum_commission'], notional * cfg['commission_bps'] / 10000)
        account['cash'] += notional - charge if action == 'SELL' else -notional - charge
        fees += charge
        slip_cash = units * row.adjusted_open * slip
        slippage += slip_cash
        traded += units * row.adjusted_open
        fills.append({'symbol': symbol, 'action': action, 'analytical_units': float(units),
                      'raw_shares_reference': float(units * row.adjusted_open / row.open),
                      'analytical_execution_price': float(execution), 'charges': float(charge),
                      'slippage': float(slip_cash), 'reason': reason, 'cash_after': float(account['cash'])})
        return float(execution)

    if intent:
        for symbol, sale in sorted(intent['sells'].items()):
            if symbol not in positions:
                continue
            row = marks[symbol]
            prior_volume = sale['prior_volume']
            if not np.isfinite(prior_volume) or prior_volume < 0:
                raise SettlementBlocked('Held exit lacks prior volume capacity')
            quantity = min(positions[symbol]['units'], prior_volume * CONFIG['prior_volume_participation'] * row.open / row.adjusted_open) if row.volume > 0 else 0
            if quantity <= 1e-10:
                delayed += 1
                continue
            fill(symbol, 'SELL', quantity, row, sale['reason'])
            positions[symbol]['units'] -= quantity
            sold.add(symbol)
            if positions[symbol]['units'] <= 1e-10:
                del positions[symbol]
                account['last_exits'][symbol] = day
            else:
                delayed += 1
        for candidate in intent['buys']:
            if len(positions) >= CONFIG['max_positions']:
                break
            symbol = candidate['symbol']
            if symbol in positions or symbol in sold:
                continue
            try:
                row = quote(bars, symbol, day)
            except SettlementBlocked:
                blocked += 1
                continue
            if row.volume <= 0:
                blocked += 1
                continue
            invested = sum(p['units'] * marks[s].adjusted_open for s, p in positions.items())
            target = CONFIG['initial_position_target'] if name in ('base', 'stress') else 1. if name == 'spy_100' else .8
            cap = CONFIG['gross_entry_ceiling'] if name in ('base', 'stress') else target
            budget = min(account['cash'], target * opening_nav, cap * opening_nav - invested)
            raw_execution = row.open * (1 + slip)
            affordable = max(0., (budget - cfg['minimum_commission']) / (raw_execution * (1 + cfg['commission_bps'] / 10000)))
            capacity = candidate['prior_volume'] * CONFIG['prior_volume_participation'] if candidate['prior_volume'] is not None else affordable
            shares = float(np.floor(min(affordable, capacity)))
            if shares <= 0:
                blocked += 1
                continue
            units = shares * row.open / row.adjusted_open
            execution = fill(symbol, 'BUY', units, row, 'trend_pullback' if name in ('base', 'stress') else 'initial_buy_hold')
            positions[symbol] = {'units': float(units), 'entry_execution': execution, 'entry_date': day,
                                 'last_mark': float(row.adjusted_close), 'mark_date': day}
            marks[symbol] = row
            account['initialized'] = True
    for symbol, position in positions.items():
        position.update(last_mark=float(marks[symbol].adjusted_close), mark_date=day)
    value = sum(p['units'] * p['last_mark'] for p in positions.values())
    nav = account['cash'] + value
    if account['cash'] < -1e-6 or not np.isfinite(nav) or nav <= 0 or len(positions) > 5:
        raise SettlementBlocked('Shadow account insolvency/position breach')
    account['nav'] = float(nav)
    return {'nav': float(nav), 'cash': float(account['cash']), 'net_return': float(nav / prior_nav - 1),
            'exposure': float(value / nav), 'costs_usd': float(fees + slippage),
            'turnover': float(traded / (2 * prior_nav)), 'position_count': len(positions),
            'blocked_entry': blocked, 'delayed_exit': delayed, 'fills': fills}


def advance(db, dates, bars, eligible, input_hash, ready, *, allow_plan=True):
    """Settle saved intentions, retain missed-plan holdings, then freeze one plan."""
    dates = pd.DatetimeIndex(dates)
    if len(dates) < 2 or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError('Ordered explicit exchange calendar required')
    day = str(dates[-1].date())
    if allow_plan:
        require_timing(day, ready)
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM plans WHERE date=?', (day,)).fetchone():
            return 'already_recorded'
        state = latest_accounts(db)
        latest = db.execute('SELECT max(date) FROM (SELECT date FROM plans UNION SELECT date FROM settlements)').fetchone()[0]
        if latest and latest > day:
            raise ValueError('Observation date moved backwards')
        if latest:
            if pd.Timestamp(latest) not in dates:
                raise SettlementBlocked('Previous account date missing from captured calendar')
            for current in dates[dates > pd.Timestamp(latest)]:
                current_day = str(current.date())
                prior_day = str(dates[dates.get_loc(current) - 1].date())
                prior = db.execute('SELECT payload FROM plans WHERE date=?', (prior_day,)).fetchone()
                saved = json.loads(prior[0]) if prior else None
                accounts = {name: settle_account(name, state[name], saved['intents'][name] if saved else None,
                            bars, dates, current_day) for name in ACCOUNTS}
                payload = {'date': current_day, 'plan_date': prior_day if saved else None,
                           'input_hash': input_hash, 'accounts': deepcopy(state), 'daily': accounts}
                db.execute('INSERT INTO settlements VALUES (?, ?)', (current_day, json.dumps(payload, sort_keys=True, allow_nan=False)))
        if allow_plan:
            # Only today's signals; missed dates were valued but never replanned.
            intents = decision(state, dates, bars, eligible)
            payload = {'date': day, 'captured_at_utc': ready, 'input_hash': input_hash,
                       'policy_hash': POLICY_HASH, 'accounts': state, 'intents': intents}
            db.execute('INSERT INTO plans VALUES (?, ?)', (day, json.dumps(payload, sort_keys=True, allow_nan=False)))
    return 'recorded' if allow_plan else 'missed_decision_cutoff'


def bundle(db, day, input_hash, status='recorded', failures=0):
    plans = [dict(r) for r in db.execute('SELECT * FROM plans ORDER BY date')]
    settlements = [dict(r) for r in db.execute('SELECT * FROM settlements ORDER BY date')]
    days = [json.loads(r['payload']) for r in settlements]
    count = sum(d['plan_date'] is not None for d in days)
    summaries = {}
    for name in ACCOUNTS:
        rows = [d['daily'][name] for d in days]
        returns = np.array([r['net_return'] for r in rows])
        equity = np.r_[1., np.cumprod(1 + returns)]
        summaries[name] = {'total_return': float(equity[-1] - 1),
            'max_drawdown': float((equity / np.maximum.accumulate(equity) - 1).min()),
            'sharpe': float(returns.mean() / returns.std(ddof=0) * np.sqrt(252)) if len(returns) > 1 and returns.std(ddof=0) > 0 else None,
            'costs_usd': sum(r['costs_usd'] for r in rows), 'turnover': sum(r['turnover'] for r in rows),
            'position_count': rows[-1]['position_count'] if rows else 0,
            'buy_fills': sum(f['action'] == 'BUY' for r in rows for f in r['fills']),
            'sell_fills': sum(f['action'] == 'SELL' for r in rows for f in r['fills'])}
    value = ShadowBundle(policy_hash=POLICY_HASH, captured_at_utc=datetime.now(timezone.utc), data_date=day,
        status=status, planned_sessions=len(plans), shadow_sessions=count, missed_plan_sessions=len(days) - count,
        coverage_failures=failures, review_ready=count >= POLICY['minimum_review_sessions'], summaries=summaries,
        input_hash=input_hash, evidence_hash=fingerprint({'plans': plans, 'settlements': settlements})).model_dump(mode='json')
    validate_bundle(value)
    return value
