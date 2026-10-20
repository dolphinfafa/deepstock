import pandas as pd
import pytest

from deepstock.strategies.cn.auction.providers import CapturedTushare


@pytest.mark.parametrize("method,args,kwargs", [
    ("stk_mins", (), {"ts_code": "000001.SZ"}),
    ("query", ("stk_mins",), {"ts_code": "000001.SZ"}),
    ("query", (), {"api_name": "stk_mins", "ts_code": "000001.SZ"}),
])
def test_all_minute_call_forms_respect_shared_quota_before_provider(monkeypatch, tmp_path, method, args, kwargs):
    class NeverCalled:
        def __getattr__(self, name):
            raise AssertionError("Provider must not be reached when quota blocks")

    def blocked():
        raise ValueError("shared quota exhausted")

    monkeypatch.delenv("DEEPSTOCK_AUCTION_OFFLINE", raising=False)
    monkeypatch.setattr("deepstock.strategies.cn.auction.quota.consume_minute_request", blocked)
    with pytest.raises(ValueError, match="quota exhausted"):
        getattr(CapturedTushare(NeverCalled(), state_path=tmp_path / 'state.sqlite3'), method)(*args, **kwargs)


@pytest.mark.parametrize('method,args,kwargs', [
    ('anns_d', (), {}), ('query', ('anns_d',), {}), ('query', (), {'api_name': 'anns_d'}),
])
def test_explicit_permission_denial_disables_only_that_endpoint_across_restarts(tmp_path, method, args, kwargs):
    class Client:
        calls = []

        def __getattr__(self, name):
            def fetch(*args, **kwargs):
                endpoint = (args[0] if args else kwargs['api_name']) if name == 'query' else name
                self.calls.append(endpoint)
                if endpoint == 'anns_d':
                    raise Exception('抱歉，您没有访问该接口的权限，权限的具体详情访问网站')
                return pd.DataFrame()
            return fetch

    client = Client()
    path = tmp_path / 'permissions.sqlite3'
    with pytest.raises(PermissionError, match='anns_d disabled'):
        getattr(CapturedTushare(client, state_path=path), method)(*args, **kwargs)
    restarted = CapturedTushare(client, state_path=path)
    for name, a, kw in [('anns_d', (), {}), ('query', ('anns_d',), {}), ('query', (), {'api_name': 'anns_d'})]:
        with pytest.raises(PermissionError, match='disabled'):
            getattr(restarted, name)(*a, **kw)
    assert client.calls == ['anns_d']
    assert restarted.trade_cal().empty
    assert client.calls == ['anns_d', 'trade_cal']


@pytest.mark.parametrize('message', [
    'Read timed out', 'Connection reset by peer', '每分钟最多访问该接口200次',
    '积分不足，请增加积分', '每天最多访问该接口2次', '您的token不对，请确认',
])
def test_transient_errors_quotas_credits_and_invalid_tokens_do_not_disable_endpoint(tmp_path, message):
    class Client:
        calls = 0

        def anns_d(self):
            self.calls += 1
            raise RuntimeError(message)

    client = Client()
    path = tmp_path / 'permissions.sqlite3'
    for _ in range(2):
        with pytest.raises(RuntimeError, match=message):
            CapturedTushare(client, state_path=path).anns_d()
    assert client.calls == 2 and not path.exists()


def test_disabled_minute_endpoint_does_not_consume_shared_quota(tmp_path, monkeypatch):
    class Client:
        def stk_mins(self):
            raise Exception('权限不足')

    calls = []
    monkeypatch.setattr('deepstock.strategies.cn.auction.quota.consume_minute_request', lambda: calls.append(1))
    adapter = CapturedTushare(Client(), state_path=tmp_path / 'permissions.sqlite3')
    with pytest.raises(PermissionError):
        adapter.stk_mins()
    with pytest.raises(PermissionError):
        adapter.query('stk_mins')
    assert calls == [1]
