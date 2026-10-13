from copy import deepcopy

import numpy as np
import pytest

from deepstock.strategies.both.granville_portfolio import PortfolioDataError, run_stock_portfolio
from test_granville_portfolio import panel_fixture


def terminal_panel():
    panel,cfg,rule=panel_fixture()
    last,effective,payment=[str(panel.dates[i].date()) for i in (227,228,232)]
    panel.terminal['A']=last
    for field in ('open','close','adjusted_open','adjusted_close'):
        panel.values[field][228:,0]=np.nan
    panel.tradable[228:,0]=False;panel.eligible[228:,0]=False
    panel.terminal_events['A']={'symbol':'A','last_quote_date':last,'effective_date':effective,
        'source_hashes':['a'*64],'cash_per_raw_share':11.45,'cash_payment_date':payment,'right':{'kind':'none'}}
    return panel,cfg,rule


def test_terminal_distribution_is_receivable_until_paid_and_not_fake_sale():
    panel,cfg,rule=terminal_panel()
    daily,trades,summary=run_stock_portfolio(panel,cfg,rule,'US',exit_policy='trend_only')
    shares=trades.query("symbol=='A' and action=='BUY'").iloc[0].entry_raw_shares_reference
    assert daily.loc[panel.dates[228],'receivable']==pytest.approx(shares*11.45)
    assert daily.loc[panel.dates[232],'receivable']==0
    assert not trades.query("symbol=='A' and action=='SELL'").shape[0]
    ledger=summary['terminal_event_ledger']
    assert ledger[0]['action']=='ENTITLEMENT' and ledger[0]['raw_shares_reference']==pytest.approx(shares)
    assert ledger[1]['action']=='CASH_PAYMENT' and ledger[1]['amount']==pytest.approx(shares*11.45)
    assert daily.cash.min()>=0


def test_analytical_units_convert_with_last_verified_raw_ratio():
    panel,cfg,rule=terminal_panel()
    panel.values['adjusted_close'][227,0]*=2
    _,_,summary=run_stock_portfolio(panel,cfg,rule,'US',exit_policy='trend_only')
    item=summary['terminal_event_ledger'][0]
    assert item['raw_shares_reference']==pytest.approx(item['analytical_units']*2)


@pytest.mark.parametrize('change',['unknown_right','missing_payment','missing_source','missing_right_value'])
def test_unknown_terminal_terms_block(change):
    panel,cfg,rule=terminal_panel();event=panel.terminal_events['A']
    if change=='unknown_right':event['right']={'kind':'unknown','cap':3.}
    if change=='missing_payment':event['cash_payment_date']=None
    if change=='missing_source':event['source_hashes']=[]
    if change=='missing_right_value':event['right']={'kind':'verified_schedule','payment_date':str(panel.dates[232].date()),'payment_per_raw_share':1.,'values_per_raw_share':{}}
    with pytest.raises(PortfolioDataError):run_stock_portfolio(panel,cfg,rule,'US',exit_policy='trend_only')


def test_verified_right_values_separate_from_cash_and_payment():
    panel,cfg,rule=terminal_panel();event=panel.terminal_events['A']
    event['right']={'kind':'verified_schedule','payment_date':str(panel.dates[234].date()),
        'payment_per_raw_share':1.,'values_per_raw_share':{str(d.date()):.8 for d in panel.dates[228:234]}}
    daily,trades,summary=run_stock_portfolio(panel,cfg,rule,'US',exit_policy='trend_only')
    shares=summary['terminal_event_ledger'][0]['raw_shares_reference']
    assert daily.loc[panel.dates[228],'receivable']==pytest.approx(shares*12.25)
    assert daily.loc[panel.dates[232],'receivable']==pytest.approx(shares*.8)
    assert daily.loc[panel.dates[234],'receivable']==0
    assert summary['terminal_event_ledger'][-1]['action']=='RIGHT_PAYMENT'
