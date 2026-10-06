import pytest

from deepstock.strategies.cn.auction.providers import CapturedTushare


@pytest.mark.parametrize("method,args,kwargs", [
    ("stk_mins", (), {"ts_code": "000001.SZ"}),
    ("query", ("stk_mins",), {"ts_code": "000001.SZ"}),
    ("query", (), {"api_name": "stk_mins", "ts_code": "000001.SZ"}),
])
def test_all_minute_call_forms_respect_shared_quota_before_provider(monkeypatch, method, args, kwargs):
    class NeverCalled:
        def __getattr__(self, name):
            raise AssertionError("Provider must not be reached when quota blocks")

    def blocked():
        raise ValueError("shared quota exhausted")

    monkeypatch.delenv("DEEPSTOCK_AUCTION_OFFLINE", raising=False)
    monkeypatch.setattr("deepstock.strategies.cn.auction.quota.consume_minute_request", blocked)
    with pytest.raises(ValueError, match="quota exhausted"):
        getattr(CapturedTushare(NeverCalled()), method)(*args, **kwargs)
