from __future__ import annotations

import pandas as pd
import pytest

from scripts.merge_ticker_alias import merge_alias


def test_merge_ticker_alias_uses_alias_before_switch() -> None:
    base = pd.DataFrame(
        {
            "date": ["2022-06-09", "2022-06-10"],
            "symbol": ["META", "META"],
            "adjusted_close": [190.0, 195.0],
        }
    )
    alias = pd.DataFrame(
        {
            "date": ["2022-06-07", "2022-06-08"],
            "symbol": ["FB", "FB"],
            "adjusted_close": [185.0, 188.0],
        }
    )
    merged = merge_alias(base, alias, "META", "FB", "2022-06-09")
    assert merged["symbol"].unique().tolist() == ["META"]
    assert merged["date"].tolist() == ["2022-06-07", "2022-06-08", "2022-06-09", "2022-06-10"]


def test_merge_ticker_alias_rejects_duplicate_dates() -> None:
    base = pd.DataFrame({"date": ["2022-06-09"], "symbol": ["META"], "adjusted_close": [190.0]})
    alias = pd.DataFrame({"date": ["2022-06-08"], "symbol": ["FB"], "adjusted_close": [188.0]})
    with pytest.raises(ValueError, match="duplicate"):
        merge_alias(pd.concat([base, base]), alias, "META", "FB", "2022-06-09")
