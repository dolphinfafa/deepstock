from __future__ import annotations

import numpy as np

from pathlib import Path

import pandas as pd

from scripts.download_norgate_stock_universe import chunk_has_liquidity_fields, membership_intervals


def test_membership_intervals_compress_daily_membership() -> None:
    series = np.array(
        [
            ("2020-01-01", 0),
            ("2020-01-02", 1),
            ("2020-01-03", 1),
            ("2020-01-06", 0),
            ("2020-01-07", 1),
        ],
        dtype=[("Date", "datetime64[D]"), ("Index Constituent", "i4")],
    ).view(np.recarray)

    assert membership_intervals(series) == [
        {"start": "2020-01-02", "end": "2020-01-03"},
        {"start": "2020-01-07", "end": "2020-01-07"},
    ]


def test_chunk_schema_requires_liquidity_fields(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    pd.DataFrame({"date": [], "symbol": [], "adjusted_close": []}).to_csv(path, index=False)
    assert not chunk_has_liquidity_fields(path)
    pd.DataFrame(
        {"date": [], "symbol": [], "adjusted_close": [], "volume": [], "turnover": []}
    ).to_csv(path, index=False)
    assert chunk_has_liquidity_fields(path)
