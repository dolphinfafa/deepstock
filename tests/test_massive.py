from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from deepstock.massive import (
    download_split_adjusted_daily_ohlc,
    download_split_adjusted_daily_prices,
    download_total_return_daily_prices,
)


def test_downloads_adjusted_daily_ohlc() -> None:
    bars = download_split_adjusted_daily_ohlc(
        ["SPY"],
        "2024-01-02",
        "2024-01-02",
        "secret",
        lambda _: {
            "status": "OK",
            "results": [
                {"t": 1704171600000, "o": 470.0, "h": 473.0, "l": 469.0, "c": 472.65}
            ],
        },
    )

    assert bars.to_dict("records") == [
        {
            "date": "2024-01-02",
            "symbol": "SPY",
            "adjusted_open": 470.0,
            "adjusted_high": 473.0,
            "adjusted_low": 469.0,
            "adjusted_close": 472.65,
        }
    ]


def test_downloads_adjusted_bars_with_pagination() -> None:
    requested: list[str] = []

    def fetch(url: str) -> dict:
        requested.append(url)
        if len(requested) == 1:
            return {
                "status": "OK",
                "results": [{"t": 1704171600000, "c": 472.65}],
                "next_url": "https://api.massive.com/next?page=2",
            }
        return {"status": "OK", "results": [{"t": 1704258000000, "c": 470.12}]}

    prices = download_split_adjusted_daily_prices(
        ["SPY"], "2024-01-02", "2024-01-03", "secret", fetch
    )

    first_query = parse_qs(urlparse(requested[0]).query)
    assert first_query["adjusted"] == ["true"]
    assert first_query["apiKey"] == ["secret"]
    assert parse_qs(urlparse(requested[1]).query)["apiKey"] == ["secret"]
    assert prices.to_dict("records") == [
        {"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 472.65},
        {"date": "2024-01-03", "symbol": "SPY", "adjusted_close": 470.12},
    ]


def test_rejects_empty_response() -> None:
    with pytest.raises(ValueError, match="no daily bars"):
        download_split_adjusted_daily_prices(
            ["SPY"], "2024-01-02", "2024-01-03", "secret", lambda _: {"status": "OK"}
        )


def test_accepts_delayed_historical_response() -> None:
    prices = download_split_adjusted_daily_prices(
        ["SPY"],
        "2024-01-02",
        "2024-01-03",
        "secret",
        lambda _: {"status": "DELAYED", "results": [{"t": 1704171600000, "c": 472.65}]},
    )
    assert len(prices) == 1


def test_builds_total_return_prices_with_dividend_factor() -> None:
    def fetch(url: str) -> dict:
        if "/v2/aggs/" in url:
            return {
                "status": "OK",
                "results": [
                    {"t": 1704171600000, "c": 100.0},
                    {"t": 1704258000000, "c": 99.0},
                ],
            }
        return {
            "status": "OK",
            "results": [
                {"ex_dividend_date": "2024-01-03", "split_adjusted_cash_amount": 1.0}
            ],
        }

    prices = download_total_return_daily_prices(["SPY"], "2024-01-02", "2024-01-03", "secret", fetch)
    assert prices["adjusted_close"].tolist() == pytest.approx([100.0, 100.0])
