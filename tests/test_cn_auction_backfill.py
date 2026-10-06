from datetime import date

import pandas as pd

from scripts.backtest_auction_features import (
    _normalize_raw_trade_dates,
    _pending_minute_windows,
)
from scripts.install_csi300_research_cron import BEGIN as BEGIN_MARKER, backfill_block as cron_block, replace_block


def replace_managed_block(original, block):
    from scripts.install_csi300_research_cron import END
    return replace_block(original, BEGIN_MARKER, END, block)


def test_normalize_raw_trade_dates_allows_cached_integer_and_api_string_merge():
    cached = pd.DataFrame(
        [{"ts_code": "000001.SZ", "trade_date": 20260928, "price": 10.0}]
    )
    fetched = pd.DataFrame(
        [{"ts_code": "000001.SZ", "trade_date": "20260929", "price": 10.1}]
    )

    merged = pd.concat(
        [_normalize_raw_trade_dates(cached), _normalize_raw_trade_dates(fetched)],
        ignore_index=True,
    ).sort_values("trade_date", ascending=False)

    assert merged["trade_date"].tolist() == ["20260929", "20260928"]


def test_normalize_raw_trade_dates_rejects_invalid_values():
    frame = pd.DataFrame([{"trade_date": "not-a-date"}])

    try:
        _normalize_raw_trade_dates(frame)
    except ValueError as exc:
        assert "Invalid Tushare trade_date" in str(exc)
    else:
        raise AssertionError("invalid trade_date should be rejected")


def test_pending_minute_windows_requires_ok_manifest_and_cache_file(tmp_path):
    windows = [
        (date(2026, 9, 8), date(2026, 9, 8)),
        (date(2026, 9, 9), date(2026, 9, 9)),
    ]
    manifest = {
        "20260908_20260908": {"status": "ok"},
        "20260909_20260909": {"status": "error"},
    }
    (tmp_path / "20260908_20260908.csv.gz").touch()

    pending = _pending_minute_windows(windows, tmp_path, manifest)

    assert pending == [(date(2026, 9, 9), date(2026, 9, 9))]


def test_auction_backfill_cron_is_idempotent_and_preserves_other_blocks(tmp_path):
    original = "# BEGIN OTHER\nkeep me\n# END OTHER\n"
    block = cron_block(tmp_path, tmp_path / "python")
    installed = replace_managed_block(original, block)
    reinstalled = replace_managed_block(installed, block)

    assert installed == reinstalled
    assert installed.count(BEGIN_MARKER) == 1
    assert "keep me" in installed
    assert "scripts.run_csi300_minute_backfill" in installed
    assert "30 11" in installed and "35 12" in installed
