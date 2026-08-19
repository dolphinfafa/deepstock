"""Read-only Massive adjusted daily-price download support."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import pandas as pd


MASSIVE_BASE_URL = "https://api.massive.com"
JsonFetcher = Callable[[str], dict[str, Any]]


def load_env_value(path: str, key: str) -> str | None:
    """Load one non-empty value from a local dotenv-style file."""

    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key and value.strip().strip("\"'"):
            return value.strip().strip("\"'")
    return None


def _append_api_key(url: str, api_key: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("apiKey", api_key)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "deepstock-research/0.1"})
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoint
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Massive request failed with HTTP status {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Massive request failed due to a network error.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Massive returned an invalid JSON response.") from exc


def _initial_url(symbol: str, start: str, end: str, api_key: str) -> str:
    path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
    query = urlencode({"adjusted": "true", "sort": "asc", "limit": "50000", "apiKey": api_key})
    return f"{MASSIVE_BASE_URL}{path}?{query}"


def download_adjusted_daily_prices(
    symbols: Iterable[str], start: str, end: str, api_key: str, fetcher: JsonFetcher | None = None
) -> pd.DataFrame:
    """Download split- and dividend-adjusted daily closes without broker access."""

    if not api_key:
        raise ValueError("A Massive API key is required.")
    fetch = fetcher or _fetch_json
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("Symbols cannot be empty.")
        url = _initial_url(normalized, start, end, api_key)
        seen_urls: set[str] = set()
        while url:
            if url in seen_urls:
                raise RuntimeError("Massive pagination loop detected.")
            seen_urls.add(url)
            payload = fetch(url)
            if payload.get("status") not in {None, "OK"}:
                raise RuntimeError("Massive returned a non-success response.")
            for bar in payload.get("results", []):
                close = float(bar["c"])
                if close <= 0:
                    raise ValueError(f"Massive returned a non-positive adjusted close for {normalized}.")
                timestamp = pd.to_datetime(int(bar["t"]), unit="ms", utc=True)
                rows.append(
                    {
                        "date": timestamp.tz_convert("America/New_York").date().isoformat(),
                        "symbol": normalized,
                        "adjusted_close": close,
                    }
                )
            next_url = payload.get("next_url")
            url = _append_api_key(next_url, api_key) if next_url else ""

    frame = pd.DataFrame(rows, columns=["date", "symbol", "adjusted_close"])
    if frame.empty:
        raise ValueError("Massive returned no daily bars for the requested range.")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Massive returned duplicate daily bars.")
    return frame.sort_values(["date", "symbol"], ignore_index=True)
