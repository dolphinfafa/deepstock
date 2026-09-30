from __future__ import annotations

import pandas as pd
import pytest

from scripts.download_norgate_daily_ohlc import build_ohlc_frame


def test_build_ohlc_frame_normalizes_norgate_response() -> None:
    source = pd.DataFrame(
        {
            "Date": ["2024-01-03", "2024-01-02"],
            "Open": [472.0, 470.0],
            "High": [474.0, 473.0],
            "Low": [471.0, 469.0],
            "Close": [473.0, 472.65],
        }
    )

    frame = build_ohlc_frame(source, "spy")

    assert frame.to_dict("records") == [
        {
            "date": "2024-01-02",
            "symbol": "SPY",
            "adjusted_open": 470.0,
            "adjusted_high": 473.0,
            "adjusted_low": 469.0,
            "adjusted_close": 472.65,
        },
        {
            "date": "2024-01-03",
            "symbol": "SPY",
            "adjusted_open": 472.0,
            "adjusted_high": 474.0,
            "adjusted_low": 471.0,
            "adjusted_close": 473.0,
        },
    ]


def test_build_ohlc_frame_rejects_invalid_bar() -> None:
    source = pd.DataFrame(
        {
            "Date": ["2024-01-02"],
            "Open": [470.0],
            "High": [469.0],
            "Low": [468.0],
            "Close": [472.65],
        }
    )

    with pytest.raises(ValueError, match="adjusted high"):
        build_ohlc_frame(source, "SPY")
