#!/usr/bin/env python3
"""Backward-compatible entry point; canonical CLI is scripts/cn/run_etf_tail_momentum.py."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cn.run_etf_tail_momentum import format_report, main, run_research, verify_input

if __name__ == "__main__":
    main()
