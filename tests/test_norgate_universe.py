from __future__ import annotations

import numpy as np

from scripts.download_norgate_stock_universe import membership_intervals


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
