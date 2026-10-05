#!/usr/bin/env python3
"""Backward-compatible entry point; implementation is under scripts/cn/."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cn.download_etf_tail_minutes import (
    EASTMONEY_PARAMS, EASTMONEY_URL, download, download_recent, exchange_calendar,
    httpx, main, normalize, normalize_eastmoney, request,
)

if __name__ == "__main__":
    main()
