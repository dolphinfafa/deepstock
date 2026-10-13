from types import SimpleNamespace
import pandas as pd
import pytest
from deepstock.data.store import DataStore, DataQualityError
from deepstock.strategies.cn.auction.providers import CapturedTushare


def test_provider_accepts_only_explicit_auction_partial(monkeypatch, tmp_path):
    monkeypatch.setattr('deepstock.strategies.cn.auction.providers.ROOT', tmp_path)
    monkeypatch.setattr('deepstock.strategies.cn.auction.providers.DataStore', lambda: DataStore(tmp_path))
    frame = pd.DataFrame({'ts_code':['600000.SH','600001.SH'], 'trade_date':['20261013']*2,
                          'price':[10.,None], 'vol':[100.,0.], 'amount':[1000.,0.]})
    client = CapturedTushare(SimpleNamespace(stk_auction=lambda **_:frame, daily=lambda **_:frame))
    result = client.stk_auction(trade_date='20261013')
    assert result.ts_code.tolist() == ['600000.SH']
    assert result.trade_date.tolist() == ['20261013']
    with pytest.raises(RuntimeError, match='blocked'):
        client.daily()


def test_auction_conflicts_and_empty_valid_response_still_block(monkeypatch, tmp_path):
    monkeypatch.setattr('deepstock.strategies.cn.auction.providers.ROOT', tmp_path)
    monkeypatch.setattr('deepstock.strategies.cn.auction.providers.DataStore', lambda: DataStore(tmp_path))
    frame = pd.DataFrame({'ts_code':['600000.SH']*2,'trade_date':['20261013']*2,'price':[10.,11.]})
    with pytest.raises(DataQualityError):
        CapturedTushare(SimpleNamespace(stk_auction=lambda:frame)).stk_auction()
    frame.price = None
    with pytest.raises(RuntimeError, match='no valid rows'):
        CapturedTushare(SimpleNamespace(stk_auction=lambda:frame)).stk_auction()
