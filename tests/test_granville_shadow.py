"""Synthetic prospective timing/accounting evidence, never strategy performance."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from deepstock.strategies.us.granville_shadow import (
    ACCOUNTS, POLICY, SettlementBlocked, advance, bundle, decision, initial_accounts,
    latest_accounts, open_state, require_timing, validate_bundle,
)


@pytest.fixture
def inputs():
    dates = xcals.get_calendar('XNYS', start='2025-01-02', end='2026-10-12').sessions.tz_localize(None)
    bars = {}
    for symbol in ['A', 'B', 'C', 'D', 'E', 'F', 'SPY']:
        close = 100 + np.arange(len(dates)) * (.5 if symbol != 'SPY' else .2)
        frame = pd.DataFrame({'open': close, 'high': close + 1, 'low': close - 10, 'close': close,
                              'volume': 1e7, 'turnover': 1e9}, index=dates)
        for field in ['open', 'high', 'low', 'close']:
            frame['adjusted_' + field] = frame[field]
        bars[symbol] = frame
    return dates, bars, set(bars) - {'SPY'}


def ready(day):
    cal = xcals.get_calendar('XNYS', start=str(day.date()), end=str((day + pd.Timedelta(days=15)).date()))
    return (cal.session_close(day) + pd.Timedelta(hours=1)).isoformat()


def record(db, inputs, end, **kwargs):
    dates, bars, eligible = inputs
    dates = dates[:end]
    return advance(db, dates, bars, eligible, 'a' * 64, ready(dates[-1]), **kwargs)


def test_no_history_earns_return_and_restart_replay_is_idempotent(inputs, tmp_path):
    dates, _, _ = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    record(db, inputs, len(dates) - 10)
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0] == 0
    record(db, inputs, len(dates) - 9)
    stats = bundle(db, str(dates[-10].date()), 'a' * 64)
    assert stats['shadow_sessions'] == 1 and stats['planned_sessions'] == 2
    assert stats['summaries']['base']['buy_fills'] == 5
    assert stats['summaries']['base']['costs_usd'] > 0
    assert stats['summaries']['stress']['costs_usd'] > stats['summaries']['base']['costs_usd']
    before = db.execute('SELECT payload FROM settlements').fetchone()[0]
    db.close()
    db = open_state(tmp_path / 'state.sqlite3')
    assert record(db, inputs, len(dates) - 9) == 'already_recorded'
    assert db.execute('SELECT payload FROM settlements').fetchone()[0] == before
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0] == 1


@pytest.mark.parametrize('damage', ['held', 'anchor', 'terminal'])
def test_unknown_held_prices_block_atomically_and_cannot_reset(inputs, tmp_path, damage):
    dates, bars, eligible = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    record(db, inputs, len(dates) - 10)
    record(db, inputs, len(dates) - 9)
    before = latest_accounts(db)
    changed = deepcopy(bars)
    if damage == 'held':
        changed['A'].loc[dates[-9], 'adjusted_close'] = np.nan
    elif damage == 'anchor':
        changed['A'].loc[dates[-10], 'adjusted_close'] = np.nan
    else:
        changed.pop('A')
    with pytest.raises(SettlementBlocked):
        record(db, (dates, changed, eligible), len(dates) - 8)
    assert latest_accounts(db) == before
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0] == 1
    with pytest.raises(SettlementBlocked):
        record(db, (dates, changed, eligible), len(dates))


def test_missed_plan_days_keep_holdings_and_never_create_historical_signals(inputs, tmp_path):
    dates, _, _ = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    record(db, inputs, len(dates) - 10)
    record(db, inputs, len(dates) - 6)
    settlements = [json.loads(r[0]) for r in db.execute('SELECT payload FROM settlements ORDER BY date')]
    assert len(settlements) == 4
    assert settlements[0]['plan_date'] == str(dates[-11].date())
    assert all(s['plan_date'] is None for s in settlements[1:])
    assert all(not a['fills'] for s in settlements[1:] for a in s['daily'].values())
    value = bundle(db, str(dates[-7].date()), 'a' * 64)
    assert value['shadow_sessions'] == 1 and value['missed_plan_sessions'] == 3
    assert value['summaries']['base']['position_count'] == 5
    assert db.execute('SELECT count(*) FROM plans').fetchone()[0] == 2


def test_seven_session_exit_is_frozen_then_filled_at_next_open(inputs, tmp_path):
    dates, _, _ = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    start = len(dates) - 10
    for end in range(start, start + 9):
        record(db, inputs, end)
    settlement = json.loads(db.execute('SELECT payload FROM settlements ORDER BY date DESC LIMIT 1').fetchone()[0])
    sells = [f for f in settlement['daily']['base']['fills'] if f['action'] == 'SELL']
    assert len(sells) == 5 and all(f['reason'] == 'time_exit' for f in sells)
    buys = [f for f in settlement['daily']['base']['fills'] if f['action'] == 'BUY']
    assert [f['symbol'] for f in buys] == ['F']  # Only a prior-close alternative fills a truly freed slot.
    assert set(latest_accounts(db)['base']['positions']) == {'F'}


def test_partial_sells_do_not_free_slots_or_replace_failed_exits(inputs, tmp_path):
    dates, bars, eligible = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    start = len(dates) - 10
    for end in range(start, start + 7):
        record(db, inputs, end)
    changed = deepcopy(bars)
    for symbol in eligible:
        changed[symbol].loc[dates[start + 6], 'volume'] = 10
    record(db, (dates, changed, eligible), start + 7)
    record(db, (dates, changed, eligible), start + 8)
    saved = json.loads(db.execute('SELECT payload FROM settlements ORDER BY date DESC LIMIT 1').fetchone()[0])
    fills = saved['daily']['base']['fills']
    assert len(fills) == 5 and all(f['action'] == 'SELL' for f in fills)
    assert saved['daily']['base']['delayed_exit'] == 5
    assert len(latest_accounts(db)['base']['positions']) == 5


def test_saved_exit_capacity_is_not_changed_by_later_volume_revision(inputs, tmp_path):
    dates, bars, eligible = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    start = len(dates) - 10
    for end in range(start, start + 8):
        record(db, inputs, end)
    changed = deepcopy(bars)
    for symbol in eligible:
        changed[symbol].loc[dates[start + 6], 'volume'] = 0
    record(db, (dates, changed, eligible), start + 8)
    saved = json.loads(db.execute('SELECT payload FROM settlements ORDER BY date DESC LIMIT 1').fetchone()[0])
    assert len([f for f in saved['daily']['base']['fills'] if f['action'] == 'SELL']) == 5


def test_contiguous_shadow_matches_frozen_portfolio_rules_and_cash(inputs, tmp_path):
    from deepstock.strategies.us.granville_shadow import CONFIG, RULE
    from deepstock.strategies.both.granville_portfolio import make_panel, run_stock_portfolio
    dates, bars, eligible = inputs
    cfg = deepcopy(CONFIG)
    start = len(dates) - 10
    cfg.update(source_start=str(dates[0].date()), evaluation_start=str(dates[start].date()), evaluation_end=str(dates[-1].date()))
    rows = pd.concat([frame.assign(symbol=s, date=frame.index) for s, frame in bars.items() if s in eligible], ignore_index=True)
    members = pd.DataFrame([{'symbol': s, 'date': dates[0], 'end': dates[-1]} for s in eligible])
    panel = make_panel(rows, dates, members, 'US', RULE, cfg)
    db = open_state(tmp_path / 'state.sqlite3')
    for end in range(start, len(dates) + 1):
        record(db, inputs, end)
    saved = [json.loads(r[0]) for r in db.execute('SELECT payload FROM settlements ORDER BY date')]
    for name in ('base', 'stress'):
        daily, trades, _ = run_stock_portfolio(panel, cfg, RULE, 'US', stress=name == 'stress')
        assert [s['daily'][name]['nav'] for s in saved] == pytest.approx(daily.nav.to_list())
        assert [s['daily'][name]['cash'] for s in saved] == pytest.approx(daily.cash.to_list())
        assert sum(len(s['daily'][name]['fills']) for s in saved) == len(trades)


def test_rebasing_totalreturn_does_not_change_held_nav_or_stop(inputs, tmp_path):
    dates, bars, eligible = inputs
    results = []
    for i, scale in enumerate([1., 17.]):
        db = open_state(tmp_path / f'{i}.sqlite3')
        record(db, inputs, len(dates) - 10)
        record(db, inputs, len(dates) - 9)
        changed = deepcopy(bars)
        for frame in changed.values():
            for field in ['open', 'high', 'low', 'close']:
                frame['adjusted_' + field] *= scale
        record(db, (dates, changed, eligible), len(dates) - 8)
        results.append(latest_accounts(db))
    for name in ACCOUNTS:
        assert results[0][name]['nav'] == pytest.approx(results[1][name]['nav'])
        assert results[0][name]['cash'] == pytest.approx(results[1][name]['cash'])
        assert set(results[0][name]['positions']) == set(results[1][name]['positions'])


def test_future_prices_do_not_change_saved_decisions_or_settlements(inputs, tmp_path):
    dates, bars, eligible = inputs
    values = []
    for i, future in enumerate([1., 999.]):
        db = open_state(tmp_path / f'{i}.sqlite3')
        changed = deepcopy(bars)
        for frame in changed.values():
            frame.loc[dates[-8]:, ['adjusted_open', 'adjusted_high', 'adjusted_low', 'adjusted_close']] *= future
        record(db, (dates, changed, eligible), len(dates) - 10)
        record(db, (dates, changed, eligible), len(dates) - 9)
        values.append([r[0] for r in db.execute('SELECT payload FROM settlements')])
    assert values[0] == values[1]


def test_late_decision_never_creates_plan_and_late_settlement_is_allowed(inputs, tmp_path):
    dates, bars, eligible = inputs
    db = open_state(tmp_path / 'state.sqlite3')
    late = '2026-10-13T15:00:00+00:00'
    with pytest.raises(ValueError, match='missed next open'):
        advance(db, dates, bars, eligible, 'a' * 64, late)
    assert db.execute('SELECT count(*) FROM plans').fetchone()[0] == 0
    record(db, inputs, len(dates) - 1)
    advance(db, dates, bars, eligible, 'a' * 64, late, allow_plan=False)
    assert db.execute('SELECT count(*) FROM settlements').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM plans').fetchone()[0] == 1


def test_missing_warmup_remains_nan_unready_without_security_exclusion(inputs):
    dates, bars, eligible = inputs
    changed = deepcopy(bars)
    changed['A'] = changed['A'].drop(index=dates[-20])
    plan = decision(initial_accounts(), dates, changed, eligible)
    assert 'A' not in [r['symbol'] for r in plan['base']['buys']]
    assert plan['base']['buys']  # Other verified signals still qualify.
    changed['A'] = changed['A'].drop(index=dates[-1])
    with pytest.raises(ValueError, match='Current eligible'):
        decision(initial_accounts(), dates, changed, eligible)


def test_bundle_rejects_private_rows_infinities_orders_and_early_promotion(tmp_path):
    db = open_state(tmp_path / 'state.sqlite3')
    value = bundle(db, '2026-10-12', 'a' * 64, 'missed_decision_cutoff')
    validate_bundle(value)
    for change in [{'review_ready': True}, {'paper_authorized': True}, {'prices': []}, {'planned_sessions': 9}]:
        with pytest.raises(ValueError):
            validate_bundle({**value, **change})
    value['summaries']['base']['total_return'] = float('inf')
    with pytest.raises(ValueError):
        validate_bundle(value)


def test_late_empty_runner_skips_acquisition_and_publication_retry_skips_research(tmp_path, monkeypatch):
    from scripts import run_granville_shadow as cli
    monkeypatch.setattr(cli, 'capture_code_provenance', lambda: {'tracked_dirty': False, 'source_sha256': 'b' * 64})
    monkeypatch.setattr(cli, 'require_timing', lambda *_: (_ for _ in ()).throw(ValueError('late')))
    monkeypatch.setattr(cli, 'capture', lambda *_: pytest.fail('No late empty capture'))
    published = []
    monkeypatch.setattr(cli, 'publish', published.append)
    args = SimpleNamespace(publish_only=False, preflight=False)
    assert cli.run(args, tmp_path) == 0
    assert published[0]['status'] == 'missed_decision_cutoff'
    assert published[0]['coverage_failures'] == 0
    assert cli.run(SimpleNamespace(publish_only=True), tmp_path) == 0
    assert published[1] == published[0]


def test_preflight_does_not_create_plans_or_fills(inputs, tmp_path, monkeypatch):
    from scripts import run_granville_shadow as cli
    dates, bars, eligible = inputs
    monkeypatch.setattr(cli, 'capture_code_provenance', lambda: {'tracked_dirty': False, 'source_sha256': 'b' * 64})
    monkeypatch.setattr(cli, 'capture', lambda *_: (dates, bars, eligible, 'a' * 64, 0))
    monkeypatch.setattr(cli, 'publish', lambda _: None)
    assert cli.run(SimpleNamespace(publish_only=False, preflight=True), tmp_path) == 0
    value = json.loads((tmp_path / 'latest.json').read_text(encoding='utf-8'))
    assert value['status'] == 'preflight_ready' and value['planned_sessions'] == value['shadow_sessions'] == 0


def test_native_schedule_starts_next_morning_and_uses_fixed_observation(tmp_path):
    from datetime import timedelta
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from scripts.install_granville_shadow_task import NS, task_xml
    now = datetime(2026, 10, 13, 23, 58, tzinfo=timezone(timedelta(hours=8)))
    value = ET.fromstring(task_xml(tmp_path, Path('python.exe'), 'HOST\\admin', now))
    def text(path):
        return value.find(path, {'t': NS}).text
    assert text('t:Triggers/t:CalendarTrigger/t:StartBoundary') == '2026-10-14T08:10:00+08:00'
    assert text('t:Actions/t:Exec/t:Arguments') == '-m scripts.run_scheduled_task granville-shadow'
    assert text('t:Principals/t:Principal/t:LogonType') == 'InteractiveToken'
    assert text('t:Settings/t:MultipleInstancesPolicy') == 'IgnoreNew'
    with pytest.raises(ValueError, match='China timezone'):
        task_xml(tmp_path, Path('python.exe'), 'HOST\\admin', now.astimezone(timezone.utc))
