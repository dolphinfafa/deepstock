# -*- coding: utf-8 -*-
"""Forward announcement capture and paper trading for the auction strategy."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

import numpy as np
import pandas as pd

from deepstock.strategies.cn.auction.repository import SessionLocal
from deepstock.strategies.cn.auction.providers import get_pro
from deepstock.strategies.cn.auction.cli import build_parser, run_auction_prediction
from deepstock.strategies.cn.auction.data import (
    fetch_tushare_auction_snapshot,
    load_current_hs300,
)
from deepstock.strategies.cn.auction.tushare_minute import (
    SHANGHAI,
    poll_tushare_rt_min_entries,
)


DEFAULT_FORWARD_ROOT = Path("artifacts/short_term_forward")
PAPER_CAPITAL = 1_000_000.0
DAILY_BUDGET_FRACTION = 0.5
ROUND_TRIP_COST_BPS = 20
PRELIMINARY_EVALUATION_COHORTS = 20
ROBUST_EVALUATION_COHORTS = 60

STRATEGY_ROLES = {
    "model_baseline": "paper",
    "model_exclude_negative": "paper",
    "model_positive_confirmation": "paper",
    "event_positive": "paper",
    "diagnostic_negative_events": "diagnostic",
}

POSITIVE_RULES = (
    ("buyback", ("回购股份", "股份回购")),
    ("insider_increase", ("增持计划", "增持股份", "完成增持")),
    ("contract_win", ("中标通知书", "项目中标", "签订重大合同", "重大合同")),
    ("earnings_up", ("业绩预增", "扭亏为盈", "大幅增长")),
    ("cash_dividend", ("现金分红", "特别分红")),
)
NEGATIVE_RULES = (
    ("investigation", ("立案调查", "被立案", "立案告知书")),
    ("penalty", ("行政处罚", "纪律处分", "监管警示", "警示函")),
    ("litigation", ("重大诉讼", "重大仲裁")),
    ("earnings_down", ("业绩预亏", "首亏", "大幅下降")),
    ("debt_stress", ("债务逾期", "未能清偿", "账户被冻结", "股份被冻结")),
    ("delisting_risk", ("退市风险", "风险警示")),
    ("restructuring_terminated", ("终止重大资产重组", "终止重组")),
    ("buyback_terminated", ("终止回购",)),
    ("shareholder_reduction", ("减持计划", "减持股份")),
)
NEGATIVE_EXCLUSIONS = ("不减持", "未减持", "终止减持", "提前终止减持")


SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_run (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source_date TEXT NOT NULL,
    observation_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_received INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS announcement_event (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    ts_code TEXT,
    company_name TEXT,
    source_date TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observation_mode TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS ix_announcement_stock_seen
    ON announcement_event(stock_code, first_seen_at);
CREATE INDEX IF NOT EXISTS ix_announcement_source_date
    ON announcement_event(source_date);

CREATE TABLE IF NOT EXISTS signal_run (
    signal_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    previous_trade_date TEXT NOT NULL,
    planned_exit_date TEXT NOT NULL,
    candidate_mode TEXT NOT NULL,
    actionable INTEGER NOT NULL,
    health_passed INTEGER NOT NULL,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_snapshot (
    signal_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    research_rank INTEGER NOT NULL,
    model_rank INTEGER,
    company_name TEXT,
    industry_name TEXT,
    predicted_return REAL,
    predicted_excess_return REAL,
    up_probability REAL,
    prediction_score REAL,
    announcement_score INTEGER NOT NULL,
    announcement_event_count INTEGER NOT NULL,
    announcement_events_json TEXT NOT NULL,
    PRIMARY KEY(signal_date, stock_code),
    FOREIGN KEY(signal_date) REFERENCES signal_run(signal_date)
);

CREATE TABLE IF NOT EXISTS strategy_signal (
    signal_date TEXT NOT NULL,
    strategy TEXT NOT NULL,
    role TEXT NOT NULL,
    selected_count INTEGER NOT NULL,
    paper_capital REAL NOT NULL,
    daily_budget REAL NOT NULL,
    PRIMARY KEY(signal_date, strategy),
    FOREIGN KEY(signal_date) REFERENCES signal_run(signal_date)
);

CREATE TABLE IF NOT EXISTS paper_order (
    order_id TEXT PRIMARY KEY,
    signal_date TEXT NOT NULL,
    strategy TEXT NOT NULL,
    role TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    company_name TEXT,
    research_rank INTEGER,
    announcement_score INTEGER NOT NULL,
    announcement_events_json TEXT NOT NULL,
    requested_notional REAL NOT NULL,
    status TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_source TEXT,
    entry_price REAL,
    entry_shares INTEGER,
    entry_notional REAL,
    entry_capacity_5pct REAL,
    entry_recorded_at TEXT,
    planned_exit_date TEXT NOT NULL,
    actual_exit_date TEXT,
    exit_price REAL,
    exit_recorded_at TEXT,
    gross_pnl REAL,
    transaction_cost REAL,
    net_pnl REAL,
    net_return_on_order REAL,
    UNIQUE(signal_date, strategy, stock_code),
    FOREIGN KEY(signal_date, strategy)
        REFERENCES strategy_signal(signal_date, strategy)
);
CREATE INDEX IF NOT EXISTS ix_paper_order_status_exit
    ON paper_order(status, planned_exit_date);

CREATE TABLE IF NOT EXISTS entry_minute_snapshot (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    source TEXT NOT NULL,
    bar_time TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    amount REAL NOT NULL,
    vwap REAL NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(trade_date, stock_code, source)
);
CREATE INDEX IF NOT EXISTS ix_entry_minute_snapshot_observed
    ON entry_minute_snapshot(trade_date, observed_at);

CREATE TABLE IF NOT EXISTS counterfactual_run (
    experiment_id TEXT PRIMARY KEY,
    signal_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    snapshot_observed_at TEXT NOT NULL,
    previous_trade_date TEXT NOT NULL,
    planned_exit_date TEXT NOT NULL,
    assumption TEXT NOT NULL,
    candidate_mode TEXT NOT NULL,
    actionable INTEGER NOT NULL,
    health_passed INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    UNIQUE(signal_date, assumption)
);

CREATE TABLE IF NOT EXISTS counterfactual_order (
    order_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    role TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    company_name TEXT,
    research_rank INTEGER,
    predicted_return REAL,
    up_probability REAL,
    announcement_score INTEGER NOT NULL,
    announcement_events_json TEXT NOT NULL,
    requested_notional REAL NOT NULL,
    status TEXT NOT NULL,
    entry_source TEXT NOT NULL,
    entry_price REAL,
    entry_shares INTEGER,
    entry_notional REAL,
    entry_capacity_5pct REAL,
    planned_exit_date TEXT NOT NULL,
    actual_exit_date TEXT,
    exit_price REAL,
    exit_recorded_at TEXT,
    gross_pnl REAL,
    transaction_cost REAL,
    net_pnl REAL,
    net_return_on_order REAL,
    UNIQUE(experiment_id, strategy, stock_code),
    FOREIGN KEY(experiment_id) REFERENCES counterfactual_run(experiment_id)
);
CREATE INDEX IF NOT EXISTS ix_counterfactual_order_status_exit
    ON counterfactual_order(status, planned_exit_date);
"""


@dataclass(frozen=True)
class EventLabel:
    event_type: str
    score: int
    keyword: str | None


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _stock_symbol(stock_code: str) -> str:
    suffix = "SH" if str(stock_code).startswith("6") else "SZ"
    return f"{str(stock_code)[:6].zfill(6)}.{suffix}"


def _price_limit_pct(stock_code: str) -> float:
    code = str(stock_code)[:6]
    return 0.195 if code.startswith(("300", "301", "688", "689")) else 0.095


def classify_announcement(title: str) -> EventLabel:
    normalized = str(title or "").replace(" ", "")
    for event_type, keywords in NEGATIVE_RULES:
        for keyword in keywords:
            if keyword not in normalized:
                continue
            if event_type == "shareholder_reduction" and any(
                value in normalized for value in NEGATIVE_EXCLUSIONS
            ):
                continue
            return EventLabel(event_type, -1, keyword)
    for event_type, keywords in POSITIVE_RULES:
        for keyword in keywords:
            if keyword in normalized:
                return EventLabel(event_type, 1, keyword)
    return EventLabel("neutral", 0, None)


@contextmanager
def _command_lock(root: Path, name: str):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / f".{name}.lock").open("w")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except (BlockingIOError, OSError):
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def open_store(root: Path = DEFAULT_FORWARD_ROOT) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "forward.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    paper_order_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(paper_order)")
    }
    if "entry_source" not in paper_order_columns:
        connection.execute("ALTER TABLE paper_order ADD COLUMN entry_source TEXT")
        connection.commit()
    return connection


def fetch_tushare_announcement_pages(
    source_date: date,
    pro=None,
    page_size: int = 6000,
) -> pd.DataFrame:
    pro = pro or get_pro()
    pages: list[pd.DataFrame] = []
    offset = 0
    for _ in range(100):
        page = pro.anns_d(
            ann_date=source_date.strftime("%Y%m%d"),
            fields="ts_code,name,ann_date,title,url",
            limit=page_size,
            offset=offset,
        )
        page = pd.DataFrame() if page is None else page
        if page.empty:
            break
        pages.append(page)
        if len(page) < page_size:
            break
        offset += page_size
    else:
        raise RuntimeError("Tushare announcement pagination exceeded 100 pages")
    if not pages:
        return pd.DataFrame(columns=["ts_code", "name", "ann_date", "title", "url"])
    return pd.concat(pages, ignore_index=True).drop_duplicates(
        ["ts_code", "ann_date", "title", "url"], keep="last"
    )


def store_announcement_frame(
    connection: sqlite3.Connection,
    frame: pd.DataFrame,
    seen_at: datetime,
    observation_mode: str = "live",
) -> dict[str, int]:
    if observation_mode not in {"live", "backfill"}:
        raise ValueError("observation_mode must be live or backfill")
    inserted = 0
    updated = 0
    seen = _iso(seen_at)
    for row in frame.to_dict("records"):
        ts_code = str(row.get("ts_code") or "")
        stock_code = ts_code[:6].zfill(6)
        source_date = str(row.get("ann_date") or "").replace("-", "")[:8]
        if len(stock_code) != 6 or len(source_date) != 8:
            continue
        source_date_iso = (
            f"{source_date[:4]}-{source_date[4:6]}-{source_date[6:8]}"
        )
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip() or None
        source_key = url or hashlib.sha256(
            f"{ts_code}|{source_date}|{title}".encode()
        ).hexdigest()
        event_id = hashlib.sha256(f"tushare|{source_key}".encode()).hexdigest()
        raw_json = _json(row)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO announcement_event (
                event_id, source, source_key, stock_code, ts_code, company_name,
                source_date, title, url, first_seen_at, last_seen_at,
                observation_mode, raw_json
            ) VALUES (?, 'tushare.anns_d', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                source_key,
                stock_code,
                ts_code,
                row.get("name"),
                source_date_iso,
                title,
                url,
                seen,
                seen,
                observation_mode,
                raw_json,
            ),
        )
        inserted += max(cursor.rowcount, 0)
        update_cursor = connection.execute(
            """
            UPDATE announcement_event
            SET last_seen_at = ?, raw_json = ?
            WHERE event_id = ? AND raw_json <> ?
            """,
            (seen, raw_json, event_id, raw_json),
        )
        updated += max(update_cursor.rowcount, 0)
    connection.commit()
    return {
        "received": int(len(frame)),
        "inserted": inserted,
        "updated": updated,
    }


def collect_announcements(
    root: Path = DEFAULT_FORWARD_ROOT,
    source_dates: Iterable[date] | None = None,
    observation_mode: str = "live",
    seen_at: datetime | None = None,
    pro=None,
) -> dict[str, Any]:
    dates = tuple(source_dates or (date.today(),))
    observed_at = seen_at or _now()
    with _command_lock(root, "collect") as acquired:
        if not acquired:
            return {"status": "skipped_locked", "dates": [value.isoformat() for value in dates]}
        connection = open_store(root)
        reports: list[dict[str, Any]] = []
        try:
            for source_date in dates:
                run_id = uuid4().hex
                started = _iso(observed_at)
                connection.execute(
                    """
                    INSERT INTO collector_run (
                        run_id, started_at, source_date, observation_mode, status
                    ) VALUES (?, ?, ?, ?, 'running')
                    """,
                    (run_id, started, source_date.isoformat(), observation_mode),
                )
                connection.commit()
                try:
                    frame = fetch_tushare_announcement_pages(source_date, pro=pro)
                    stats = store_announcement_frame(
                        connection, frame, observed_at, observation_mode
                    )
                    finished = _iso(_now())
                    connection.execute(
                        """
                        UPDATE collector_run
                        SET finished_at=?, status='ok', rows_received=?, rows_inserted=?
                        WHERE run_id=?
                        """,
                        (finished, stats["received"], stats["inserted"], run_id),
                    )
                    reports.append({
                        "source_date": source_date.isoformat(),
                        "status": "ok",
                        **stats,
                    })
                except Exception as exc:
                    connection.execute(
                        """
                        UPDATE collector_run
                        SET finished_at=?, status='error', error=? WHERE run_id=?
                        """,
                        (_iso(_now()), f"{type(exc).__name__}: {exc}", run_id),
                    )
                    connection.commit()
                    raise
                connection.commit()
        finally:
            connection.close()
    return {
        "status": "ok",
        "observed_at": _iso(observed_at),
        "observation_mode": observation_mode,
        "dates": reports,
    }


def _trading_dates(start: date, end: date, pro=None) -> list[date]:
    pro = pro or get_pro()
    frame = pro.trade_cal(
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    if frame is None or frame.empty:
        return []
    return [
        date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        for value in sorted(frame["cal_date"].astype(str).unique())
    ]


def trading_neighbors(signal_date: date, pro=None) -> tuple[date, date]:
    dates = _trading_dates(
        signal_date - timedelta(days=20), signal_date + timedelta(days=20), pro
    )
    previous = [value for value in dates if value < signal_date]
    following = [value for value in dates if value > signal_date]
    if signal_date not in dates:
        raise ValueError(f"{signal_date} is not an SSE trading day")
    if not previous or not following:
        raise RuntimeError(f"cannot determine trading neighbors for {signal_date}")
    return previous[-1], following[0]


def _events_by_stock(
    connection: sqlite3.Connection,
    stock_codes: Iterable[str],
    start_at: datetime,
    cutoff: datetime,
) -> dict[str, dict[str, Any]]:
    codes = sorted({str(value)[:6].zfill(6) for value in stock_codes})
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = connection.execute(
        f"""
        SELECT event_id, stock_code, company_name, source_date, title, url,
               first_seen_at
        FROM announcement_event
        WHERE observation_mode='live'
          AND stock_code IN ({placeholders})
          AND first_seen_at >= ?
          AND first_seen_at <= ?
        ORDER BY first_seen_at, event_id
        """,
        (*codes, _iso(start_at), _iso(cutoff)),
    ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = classify_announcement(row["title"])
        item = {
            "event_id": row["event_id"],
            "source_date": row["source_date"],
            "first_seen_at": row["first_seen_at"],
            "title": row["title"],
            "url": row["url"],
            "event_type": label.event_type,
            "score": label.score,
            "keyword": label.keyword,
        }
        aggregate = output.setdefault(row["stock_code"], {
            "score": 0,
            "events": [],
            "company_name": row["company_name"],
        })
        aggregate["score"] = int(np.clip(aggregate["score"] + label.score, -3, 3))
        aggregate["events"].append(item)
    return output


def _order_id(signal_date: date, strategy: str, stock_code: str) -> str:
    return hashlib.sha256(
        f"{signal_date.isoformat()}|{strategy}|{stock_code}".encode()
    ).hexdigest()


def _strategy_selections(
    candidate_by_code: dict[str, dict[str, Any]],
    events: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    baseline = list(candidate_by_code)
    return {
        "model_baseline": baseline,
        "model_exclude_negative": [
            code for code in baseline if events.get(code, {}).get("score", 0) >= 0
        ],
        "model_positive_confirmation": [
            code for code in baseline if events.get(code, {}).get("score", 0) > 0
        ],
        "event_positive": sorted(
            code for code, event in events.items() if event["score"] > 0
        ),
        "diagnostic_negative_events": sorted(
            code for code, event in events.items() if event["score"] < 0
        ),
    }


def record_signal_report(
    connection: sqlite3.Connection,
    report: dict[str, Any],
    generated_at: datetime,
    previous_trade_date: date,
    planned_exit_date: date,
    universe_codes: Iterable[str],
    paper_capital: float = PAPER_CAPITAL,
) -> dict[str, Any]:
    signal_date = date.fromisoformat(report["signal_date"])
    existing = connection.execute(
        "SELECT signal_date FROM signal_run WHERE signal_date=?",
        (signal_date.isoformat(),),
    ).fetchone()
    if existing:
        return {"status": "already_recorded", "signal_date": signal_date.isoformat()}

    event_start = datetime.combine(previous_trade_date, time(15, 0), SHANGHAI)
    event_cutoff = datetime.combine(signal_date, time(9, 25), SHANGHAI)
    events = _events_by_stock(
        connection, universe_codes, event_start, event_cutoff
    )
    candidates = report.get("candidates") or []
    candidate_by_code = {
        str(item["stock_code"])[:6].zfill(6): item for item in candidates
    }
    connection.execute(
        """
        INSERT INTO signal_run (
            signal_date, generated_at, previous_trade_date, planned_exit_date,
            candidate_mode, actionable, health_passed, report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_date.isoformat(),
            _iso(generated_at),
            previous_trade_date.isoformat(),
            planned_exit_date.isoformat(),
            report["candidate_mode"],
            int(bool(report["actionable"])),
            int(bool(report["strategy_health"]["passed"])),
            _json(report),
        ),
    )
    for stock_code, candidate in candidate_by_code.items():
        event = events.get(stock_code, {"score": 0, "events": []})
        connection.execute(
            """
            INSERT INTO candidate_snapshot (
                signal_date, stock_code, research_rank, model_rank, company_name,
                industry_name, predicted_return, predicted_excess_return,
                up_probability, prediction_score, announcement_score,
                announcement_event_count, announcement_events_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_date.isoformat(),
                stock_code,
                int(candidate["rank"]),
                candidate.get("model_rank"),
                candidate.get("name"),
                candidate.get("industry"),
                candidate.get("predicted_return"),
                candidate.get("predicted_excess_return"),
                candidate.get("up_probability"),
                candidate.get("prediction_score"),
                int(event["score"]),
                len(event["events"]),
                _json(event["events"]),
            ),
        )

    selections = _strategy_selections(candidate_by_code, events)
    daily_budget = paper_capital * DAILY_BUDGET_FRACTION
    for strategy, codes in selections.items():
        role = STRATEGY_ROLES[strategy]
        connection.execute(
            """
            INSERT INTO strategy_signal (
                signal_date, strategy, role, selected_count, paper_capital,
                daily_budget
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                signal_date.isoformat(), strategy, role, len(codes),
                paper_capital, daily_budget,
            ),
        )
        requested = daily_budget / len(codes) if codes else 0.0
        for stock_code in codes:
            candidate = candidate_by_code.get(stock_code, {})
            event = events.get(stock_code, {"score": 0, "events": []})
            company_name = candidate.get("name") or event.get("company_name")
            connection.execute(
                """
                INSERT INTO paper_order (
                    order_id, signal_date, strategy, role, stock_code,
                    company_name, research_rank, announcement_score,
                    announcement_events_json, requested_notional, status,
                    entry_date, planned_exit_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SIGNALLED', ?, ?)
                """,
                (
                    _order_id(signal_date, strategy, stock_code),
                    signal_date.isoformat(),
                    strategy,
                    role,
                    stock_code,
                    company_name,
                    candidate.get("rank"),
                    int(event["score"]),
                    _json(event["events"]),
                    requested,
                    signal_date.isoformat(),
                    planned_exit_date.isoformat(),
                ),
            )
    connection.commit()
    return {
        "status": "recorded",
        "signal_date": signal_date.isoformat(),
        "candidate_mode": report["candidate_mode"],
        "actionable": bool(report["actionable"]),
        "event_window": {"start": _iso(event_start), "cutoff": _iso(event_cutoff)},
        "universe_event_stocks": len(events),
        "strategies": {name: len(codes) for name, codes in selections.items()},
    }


def record_late_auction_counterfactual(
    connection: sqlite3.Connection,
    report: dict[str, Any],
    auction_snapshot: pd.DataFrame,
    snapshot_observed_at: datetime,
    previous_trade_date: date,
    planned_exit_date: date,
    universe_codes: Iterable[str],
    paper_capital: float = PAPER_CAPITAL,
) -> dict[str, Any]:
    """Record a late auction snapshot without contaminating forward samples."""
    signal_date = date.fromisoformat(report["signal_date"])
    assumption = "late_snapshot_assumed_available_by_0930"
    experiment_id = hashlib.sha256(
        f"{signal_date.isoformat()}|{assumption}".encode()
    ).hexdigest()
    existing = connection.execute(
        "SELECT experiment_id FROM counterfactual_run WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    if existing:
        return {
            "status": "already_recorded",
            "experiment_id": experiment_id,
            "signal_date": signal_date.isoformat(),
        }

    event_start = datetime.combine(previous_trade_date, time(15, 0), SHANGHAI)
    event_cutoff = datetime.combine(signal_date, time(9, 25), SHANGHAI)
    events = _events_by_stock(
        connection, universe_codes, event_start, event_cutoff
    )
    candidates = report.get("candidates") or []
    candidate_by_code = {
        str(item["stock_code"])[:6].zfill(6): item for item in candidates
    }
    selections = _strategy_selections(candidate_by_code, events)
    auction_by_code = {
        str(row.get("ts_code") or "")[:6].zfill(6): row
        for row in auction_snapshot.to_dict("records")
    }
    connection.execute(
        """
        INSERT INTO counterfactual_run (
            experiment_id, signal_date, created_at, snapshot_observed_at,
            previous_trade_date, planned_exit_date, assumption,
            candidate_mode, actionable, health_passed, report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            signal_date.isoformat(),
            _iso(_now()),
            _iso(snapshot_observed_at),
            previous_trade_date.isoformat(),
            planned_exit_date.isoformat(),
            assumption,
            report["candidate_mode"],
            int(bool(report["actionable"])),
            int(bool(report["strategy_health"]["passed"])),
            _json(report),
        ),
    )
    daily_budget = paper_capital * DAILY_BUDGET_FRACTION
    status_counts: dict[str, int] = {}
    for strategy, codes in selections.items():
        requested = daily_budget / len(codes) if codes else 0.0
        for stock_code in codes:
            candidate = candidate_by_code.get(stock_code, {})
            event = events.get(stock_code, {"score": 0, "events": []})
            auction = auction_by_code.get(stock_code, {})
            price = float(auction.get("price") or 0.0)
            amount = float(auction.get("amount") or 0.0)
            if not np.isfinite(price) or price <= 0:
                price = 0.0
            if not np.isfinite(amount) or amount <= 0:
                amount = 0.0
            capacity = amount * 0.05
            executable = min(requested, capacity)
            shares = (
                int(math.floor(executable / price / 100.0) * 100)
                if price > 0 else 0
            )
            status = "FILLED" if shares > 0 else (
                "MISSED_CAPACITY" if price > 0 else "MISSED_ENTRY"
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            company_name = candidate.get("name") or event.get("company_name")
            order_id = hashlib.sha256(
                f"{experiment_id}|{strategy}|{stock_code}".encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO counterfactual_order (
                    order_id, experiment_id, strategy, role, stock_code,
                    company_name, research_rank, predicted_return,
                    up_probability, announcement_score,
                    announcement_events_json, requested_notional, status,
                    entry_source, entry_price, entry_shares, entry_notional,
                    entry_capacity_5pct, planned_exit_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, experiment_id, strategy, STRATEGY_ROLES[strategy],
                    stock_code, company_name, candidate.get("rank"),
                    candidate.get("predicted_return"),
                    candidate.get("up_probability"), int(event["score"]),
                    _json(event["events"]), requested, status,
                    "tushare.stk_auction.price", price or None,
                    shares or None, shares * price if shares else None,
                    capacity or None, planned_exit_date.isoformat(),
                ),
            )
    connection.commit()
    return {
        "status": "recorded",
        "experiment_id": experiment_id,
        "signal_date": signal_date.isoformat(),
        "snapshot_observed_at": _iso(snapshot_observed_at),
        "assumption": assumption,
        "official_forward_sample": False,
        "candidate_mode": report["candidate_mode"],
        "actionable": bool(report["actionable"]),
        "strategies": {name: len(codes) for name, codes in selections.items()},
        "orders": status_counts,
    }


def create_late_auction_counterfactual(
    root: Path = DEFAULT_FORWARD_ROOT,
    signal_date: date | None = None,
    auction_history_root: str = "artifacts/auction_history_tushare",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    signal_date = signal_date or _now().date()
    observed_at = observed_at or _now()
    if observed_at.date() != signal_date:
        raise ValueError("counterfactual snapshot must be recorded on signal date")
    with _command_lock(root, "counterfactual") as acquired:
        if not acquired:
            return {
                "status": "skipped_locked",
                "signal_date": signal_date.isoformat(),
            }
        connection = open_store(root)
        try:
            existing = connection.execute(
                "SELECT experiment_id FROM counterfactual_run WHERE signal_date=?",
                (signal_date.isoformat(),),
            ).fetchone()
            if existing:
                return {
                    "status": "already_recorded",
                    "experiment_id": existing["experiment_id"],
                    "signal_date": signal_date.isoformat(),
                }
            pro = get_pro()
            previous, following = trading_neighbors(signal_date, pro)
            db = SessionLocal()
            try:
                universe = load_current_hs300(db, signal_date)
            finally:
                db.close()
            args = build_parser().parse_args([
                "predict-auction",
                "--signal-date", signal_date.isoformat(),
                "--auction-history-root", auction_history_root,
                "--wait-seconds", "0",
                "--json",
            ])
            with redirect_stdout(io.StringIO()):
                report = run_auction_prediction(args)
            snapshot = fetch_tushare_auction_snapshot(
                universe.members, signal_date, universe.missing_codes
            )
            universe_codes = [member.stock_code for member in universe.members]
            universe_codes.extend(universe.missing_codes)
            return record_late_auction_counterfactual(
                connection, report, snapshot, observed_at, previous, following,
                universe_codes,
            )
        finally:
            connection.close()


def generate_signal(
    root: Path = DEFAULT_FORWARD_ROOT,
    signal_date: date | None = None,
    auction_history_root: str = "artifacts/auction_history_tushare",
    wait_seconds: int = 180,
    generated_at: datetime | None = None,
    enforce_forward_time: bool = True,
) -> dict[str, Any]:
    signal_date = signal_date or _now().date()
    observed_at = generated_at or _now()
    if enforce_forward_time:
        if observed_at.date() != signal_date:
            raise ValueError("forward signal date must be today")
        if not time(9, 25) <= observed_at.time() < time(9, 30):
            raise ValueError("forward signal must be generated from 09:25 to 09:29")
    with _command_lock(root, "signal") as acquired:
        if not acquired:
            return {"status": "skipped_locked", "signal_date": signal_date.isoformat()}
        connection = open_store(root)
        try:
            existing = connection.execute(
                "SELECT signal_date FROM signal_run WHERE signal_date=?",
                (signal_date.isoformat(),),
            ).fetchone()
            if existing:
                return {"status": "already_recorded", "signal_date": signal_date.isoformat()}
            pro = get_pro()
            try:
                previous, following = trading_neighbors(signal_date, pro)
            except ValueError:
                return {
                    "status": "skipped_non_trading_day",
                    "signal_date": signal_date.isoformat(),
                }
            db = SessionLocal()
            try:
                universe = load_current_hs300(db, signal_date)
            finally:
                db.close()
            universe_codes = [member.stock_code for member in universe.members]
            universe_codes.extend(universe.missing_codes)
            args = build_parser().parse_args([
                "predict-auction",
                "--signal-date", signal_date.isoformat(),
                "--auction-history-root", auction_history_root,
                "--wait-seconds", str(wait_seconds),
                "--json",
            ])
            with redirect_stdout(io.StringIO()):
                report = run_auction_prediction(args)
            decision_at = generated_at or _now()
            if enforce_forward_time and decision_at.time() >= time(9, 30):
                return {
                    "status": "missed_decision_cutoff",
                    "signal_date": signal_date.isoformat(),
                    "decision_at": _iso(decision_at),
                }
            return record_signal_report(
                connection,
                report,
                decision_at,
                previous,
                following,
                universe_codes,
            )
        finally:
            connection.close()


def apply_entry_fills(
    connection: sqlite3.Connection,
    trade_date: date,
    entries: pd.DataFrame,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    pending = connection.execute(
        """
        SELECT order_id, stock_code, requested_notional
        FROM paper_order WHERE entry_date=? AND status='SIGNALLED'
        """,
        (trade_date.isoformat(),),
    ).fetchall()
    by_code = {}
    if not entries.empty:
        by_code = {
            str(row["stock_code"])[:6].zfill(6): row
            for row in entries.to_dict("records")
        }
    filled = 0
    missed_capacity = 0
    for order in pending:
        entry = by_code.get(order["stock_code"])
        if not entry:
            continue
        price = float(entry["entry_0931_vwap"])
        volume = float(entry["entry_0931_volume"])
        amount = entry.get("entry_0931_amount")
        amount = (
            float(amount)
            if amount is not None and pd.notna(amount)
            else price * volume
        )
        capacity = amount * 0.05
        executable = min(float(order["requested_notional"]), capacity)
        shares = int(math.floor(executable / price / 100.0) * 100)
        if shares <= 0:
            connection.execute(
                "UPDATE paper_order SET status='MISSED_CAPACITY' WHERE order_id=?",
                (order["order_id"],),
            )
            missed_capacity += 1
            continue
        notional = shares * price
        connection.execute(
            """
            UPDATE paper_order
            SET status='FILLED', entry_source=?, entry_price=?,
                entry_shares=?, entry_notional=?,
                entry_capacity_5pct=?, entry_recorded_at=?
            WHERE order_id=?
            """,
            (
                str(entry.get("entry_source") or "unknown"),
                price, shares, notional, capacity,
                _iso(recorded_at or _now()), order["order_id"],
            ),
        )
        filled += 1
    connection.commit()
    return {
        "trade_date": trade_date.isoformat(),
        "pending": len(pending),
        "filled": filled,
        "missed_capacity": missed_capacity,
        "missing_price": len(pending) - filled - missed_capacity,
    }


def store_entry_minute_snapshots(
    connection: sqlite3.Connection,
    entries: pd.DataFrame,
    source: str = "tushare.rt_min",
) -> int:
    """Persist the first observed target-minute row without overwriting it."""
    inserted = 0
    for row in entries.to_dict("records"):
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO entry_minute_snapshot (
                trade_date, stock_code, source, bar_time, observed_at,
                open, high, low, close, volume, amount, vwap, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(row["trade_date"]),
                str(row["stock_code"])[:6].zfill(6),
                source,
                str(row["bar_time"]),
                str(row["observed_at"]),
                float(row["entry_0931_open"]),
                float(row["entry_0931_high"]),
                float(row["entry_0931_low"]),
                float(row["entry_0931_close"]),
                float(row["entry_0931_volume"]),
                float(row["entry_0931_amount"]),
                float(row["entry_0931_vwap"]),
                str(row["raw_json"]),
            ),
        )
        inserted += max(cursor.rowcount, 0)
    connection.commit()
    return inserted


def load_entry_minute_snapshots(
    connection: sqlite3.Connection,
    trade_date: date,
    stock_codes: Iterable[str],
) -> pd.DataFrame:
    codes = sorted({str(value)[:6].zfill(6) for value in stock_codes})
    if not codes:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in codes)
    rows = connection.execute(
        f"""
        SELECT stock_code, trade_date, source AS entry_source,
               open AS entry_0931_open, high AS entry_0931_high,
               low AS entry_0931_low, close AS entry_0931_close,
               volume AS entry_0931_volume, amount AS entry_0931_amount,
               vwap AS entry_0931_vwap, bar_time, observed_at, raw_json
        FROM entry_minute_snapshot
        WHERE trade_date=? AND stock_code IN ({placeholders})
        ORDER BY CASE source WHEN 'tushare.rt_min' THEN 0 ELSE 1 END,
                 observed_at
        """,
        (trade_date.isoformat(), *codes),
    ).fetchall()
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["trade_date"] = date.fromisoformat(item["trade_date"])
        by_code.setdefault(item["stock_code"], item)
    return pd.DataFrame(list(by_code.values()))


def fill_entries(
    root: Path = DEFAULT_FORWARD_ROOT,
    trade_date: date | None = None,
    wait_seconds: float = 90,
    poll_seconds: float = 3,
    pro=None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    trade_date = trade_date or _now().date()
    observed_at = observed_at or _now()
    with _command_lock(root, "fill") as acquired:
        if not acquired:
            return {"status": "skipped_locked", "trade_date": trade_date.isoformat()}
        connection = open_store(root)
        try:
            codes = [
                row["stock_code"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT stock_code FROM paper_order
                    WHERE entry_date=? AND status='SIGNALLED'
                    """,
                    (trade_date.isoformat(),),
                ).fetchall()
            ]
            if not codes:
                return {"status": "nothing_pending", "trade_date": trade_date.isoformat()}
            cached = load_entry_minute_snapshots(connection, trade_date, codes)
            cached_codes = (
                set(cached["stock_code"].astype(str)) if not cached.empty else set()
            )
            missing_codes = [code for code in codes if code not in cached_codes]
            source_report = {
                "source": "tushare.rt_min",
                "attempts": 0,
                "requested_symbols": len(missing_codes),
                "observed_symbols": 0,
                "errors": [],
            }
            inserted = 0
            if missing_codes and observed_at.date() == trade_date:
                fresh, source_report = poll_tushare_rt_min_entries(
                    [_stock_symbol(code) for code in missing_codes],
                    trade_date,
                    pro=pro,
                    wait_seconds=wait_seconds,
                    poll_seconds=poll_seconds,
                )
                inserted = store_entry_minute_snapshots(connection, fresh)
            elif missing_codes:
                source_report["errors"] = [
                    "tushare.rt_min only exposes the current trading day"
                ]
            entries = load_entry_minute_snapshots(connection, trade_date, codes)
            fill_report = apply_entry_fills(
                connection, trade_date, entries, _now()
            )
            status = "ok"
            if fill_report["missing_price"] and entries.empty:
                status = "source_unavailable"
            return {
                "status": status,
                "snapshot_rows": len(entries),
                "snapshot_rows_inserted": inserted,
                "source_report": source_report,
                **fill_report,
            }
        finally:
            connection.close()


def _apparent_limit_down(row: dict[str, Any]) -> bool:
    pre_close = float(row.get("pre_close") or 0)
    close = float(row.get("close") or 0)
    low = float(row.get("low") or close)
    if pre_close <= 0 or close <= 0:
        return False
    threshold = -_price_limit_pct(str(row.get("ts_code") or "")[:6])
    return close / pre_close - 1.0 <= threshold and abs(close - low) <= 0.001


def expire_unfilled_entries(
    connection: sqlite3.Connection,
    before_date: date,
) -> int:
    cursor = connection.execute(
        """
        UPDATE paper_order SET status='MISSED_ENTRY'
        WHERE status='SIGNALLED' AND entry_date < ?
        """,
        (before_date.isoformat(),),
    )
    connection.commit()
    return max(cursor.rowcount, 0)


def apply_exit_prices(
    connection: sqlite3.Connection,
    market_rows_by_date: dict[date, pd.DataFrame],
    as_of_date: date,
    recorded_at: datetime | None = None,
    cost_bps: int = ROUND_TRIP_COST_BPS,
) -> dict[str, Any]:
    pending = connection.execute(
        """
        SELECT order_id, stock_code, entry_price, entry_shares, entry_notional,
               planned_exit_date
        FROM paper_order
        WHERE status='FILLED' AND planned_exit_date <= ?
        """,
        (as_of_date.isoformat(),),
    ).fetchall()
    normalized: dict[date, dict[str, dict[str, Any]]] = {}
    for trade_date, frame in market_rows_by_date.items():
        if frame is None or frame.empty:
            normalized[trade_date] = {}
            continue
        normalized[trade_date] = {
            str(row["ts_code"])[:6].zfill(6): row
            for row in frame.to_dict("records")
        }
    closed = 0
    blocked = 0
    for order in pending:
        planned = date.fromisoformat(order["planned_exit_date"])
        for trade_date in sorted(value for value in normalized if planned <= value <= as_of_date):
            row = normalized[trade_date].get(order["stock_code"])
            if not row:
                continue
            if _apparent_limit_down(row):
                blocked += 1
                continue
            exit_price = float(row["close"])
            if exit_price <= 0:
                continue
            shares = int(order["entry_shares"])
            entry_price = float(order["entry_price"])
            entry_notional = float(order["entry_notional"])
            gross_pnl = shares * (exit_price - entry_price)
            transaction_cost = entry_notional * cost_bps / 10_000.0
            net_pnl = gross_pnl - transaction_cost
            connection.execute(
                """
                UPDATE paper_order
                SET status='CLOSED', actual_exit_date=?, exit_price=?,
                    exit_recorded_at=?, gross_pnl=?, transaction_cost=?,
                    net_pnl=?, net_return_on_order=?
                WHERE order_id=?
                """,
                (
                    trade_date.isoformat(), exit_price,
                    _iso(recorded_at or _now()), gross_pnl, transaction_cost,
                    net_pnl, net_pnl / entry_notional, order["order_id"],
                ),
            )
            closed += 1
            break
    connection.commit()
    return {
        "as_of_date": as_of_date.isoformat(),
        "pending": len(pending),
        "closed": closed,
        "blocked_observations": blocked,
        "still_open": len(pending) - closed,
    }


def apply_counterfactual_exit_prices(
    connection: sqlite3.Connection,
    market_rows_by_date: dict[date, pd.DataFrame],
    as_of_date: date,
    recorded_at: datetime | None = None,
    cost_bps: int = ROUND_TRIP_COST_BPS,
) -> dict[str, Any]:
    pending = connection.execute(
        """
        SELECT order_id, stock_code, entry_price, entry_shares, entry_notional,
               planned_exit_date
        FROM counterfactual_order
        WHERE status='FILLED' AND planned_exit_date <= ?
        """,
        (as_of_date.isoformat(),),
    ).fetchall()
    normalized: dict[date, dict[str, dict[str, Any]]] = {}
    for trade_date, frame in market_rows_by_date.items():
        if frame is None or frame.empty:
            normalized[trade_date] = {}
            continue
        normalized[trade_date] = {
            str(row["ts_code"])[:6].zfill(6): row
            for row in frame.to_dict("records")
        }
    closed = 0
    blocked = 0
    for order in pending:
        planned = date.fromisoformat(order["planned_exit_date"])
        for trade_date in sorted(
            value for value in normalized if planned <= value <= as_of_date
        ):
            row = normalized[trade_date].get(order["stock_code"])
            if not row:
                continue
            if _apparent_limit_down(row):
                blocked += 1
                continue
            exit_price = float(row["close"])
            if exit_price <= 0:
                continue
            shares = int(order["entry_shares"])
            entry_price = float(order["entry_price"])
            entry_notional = float(order["entry_notional"])
            gross_pnl = shares * (exit_price - entry_price)
            transaction_cost = entry_notional * cost_bps / 10_000.0
            net_pnl = gross_pnl - transaction_cost
            connection.execute(
                """
                UPDATE counterfactual_order
                SET status='CLOSED', actual_exit_date=?, exit_price=?,
                    exit_recorded_at=?, gross_pnl=?, transaction_cost=?,
                    net_pnl=?, net_return_on_order=?
                WHERE order_id=?
                """,
                (
                    trade_date.isoformat(), exit_price,
                    _iso(recorded_at or _now()), gross_pnl, transaction_cost,
                    net_pnl, net_pnl / entry_notional, order["order_id"],
                ),
            )
            closed += 1
            break
    connection.commit()
    return {
        "as_of_date": as_of_date.isoformat(),
        "pending": len(pending),
        "closed": closed,
        "blocked_observations": blocked,
        "still_open": len(pending) - closed,
    }


def settle_positions(
    root: Path = DEFAULT_FORWARD_ROOT,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    as_of_date = as_of_date or _now().date()
    with _command_lock(root, "settle") as acquired:
        if not acquired:
            return {"status": "skipped_locked", "as_of_date": as_of_date.isoformat()}
        connection = open_store(root)
        try:
            expired_entries = expire_unfilled_entries(connection, as_of_date)
            row = connection.execute(
                """
                SELECT MIN(planned_exit_date) AS first_date FROM paper_order
                WHERE status='FILLED' AND planned_exit_date <= ?
                """,
                (as_of_date.isoformat(),),
            ).fetchone()
            if not row or not row["first_date"]:
                return {
                    "status": "nothing_pending",
                    "as_of_date": as_of_date.isoformat(),
                    "expired_entries": expired_entries,
                }
            first_date = date.fromisoformat(row["first_date"])
            pro = get_pro()
            dates = _trading_dates(first_date, as_of_date, pro)
            market_rows = {
                value: pro.daily(
                    trade_date=value.strftime("%Y%m%d"),
                    fields="ts_code,trade_date,pre_close,low,close",
                )
                for value in dates
            }
            return {
                "status": "ok",
                "expired_entries": expired_entries,
                **apply_exit_prices(connection, market_rows, as_of_date),
            }
        finally:
            connection.close()


def settle_counterfactual_positions(
    root: Path = DEFAULT_FORWARD_ROOT,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    as_of_date = as_of_date or _now().date()
    with _command_lock(root, "counterfactual_settle") as acquired:
        if not acquired:
            return {
                "status": "skipped_locked",
                "as_of_date": as_of_date.isoformat(),
            }
        connection = open_store(root)
        try:
            row = connection.execute(
                """
                SELECT MIN(planned_exit_date) AS first_date
                FROM counterfactual_order
                WHERE status='FILLED' AND planned_exit_date <= ?
                """,
                (as_of_date.isoformat(),),
            ).fetchone()
            if not row or not row["first_date"]:
                return {
                    "status": "nothing_pending",
                    "as_of_date": as_of_date.isoformat(),
                }
            first_date = date.fromisoformat(row["first_date"])
            pro = get_pro()
            dates = _trading_dates(first_date, as_of_date, pro)
            market_rows = {
                value: pro.daily(
                    trade_date=value.strftime("%Y%m%d"),
                    fields="ts_code,trade_date,pre_close,low,close",
                )
                for value in dates
            }
            return {
                "status": "ok",
                **apply_counterfactual_exit_prices(
                    connection, market_rows, as_of_date
                ),
            }
        finally:
            connection.close()


def build_forward_report(
    connection: sqlite3.Connection,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    cutoff = (as_of_date or _now().date()).isoformat()
    collector = connection.execute(
        """
        SELECT COUNT(*) AS runs,
               SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_runs,
               MAX(finished_at) AS last_finished_at
        FROM collector_run
        """
    ).fetchone()
    announcement_count = connection.execute(
        "SELECT COUNT(*) AS count FROM announcement_event"
    ).fetchone()["count"]
    signal_rows = connection.execute(
        """
        SELECT ss.signal_date, ss.strategy, ss.role, ss.selected_count,
               ss.paper_capital,
               COUNT(po.order_id) AS orders,
               SUM(CASE WHEN po.status='FILLED' THEN 1 ELSE 0 END) AS open_orders,
               SUM(CASE WHEN po.status='CLOSED' THEN 1 ELSE 0 END) AS closed_orders,
               SUM(CASE WHEN po.status IN (
                   'CLOSED', 'MISSED_ENTRY', 'MISSED_CAPACITY'
               ) THEN 1 ELSE 0 END) AS terminal_orders,
               COALESCE(SUM(po.entry_notional), 0) AS invested,
               COALESCE(SUM(po.gross_pnl), 0) AS gross_pnl,
               COALESCE(SUM(po.transaction_cost), 0) AS transaction_cost,
               COALESCE(SUM(po.net_pnl), 0) AS net_pnl
        FROM strategy_signal ss
        LEFT JOIN paper_order po
          ON po.signal_date=ss.signal_date AND po.strategy=ss.strategy
        WHERE ss.signal_date <= ?
        GROUP BY ss.signal_date, ss.strategy
        ORDER BY ss.signal_date, ss.strategy
        """,
        (cutoff,),
    ).fetchall()
    daily: list[dict[str, Any]] = []
    for row in signal_rows:
        status = "cash" if row["selected_count"] == 0 else (
            "closed" if row["terminal_orders"] == row["orders"] else "open"
        )
        daily.append({
            "signal_date": row["signal_date"],
            "strategy": row["strategy"],
            "role": row["role"],
            "status": status,
            "selected_count": row["selected_count"],
            "orders": row["orders"],
            "open_orders": row["open_orders"],
            "closed_orders": row["closed_orders"],
            "invested": float(row["invested"]),
            "gross_pnl": float(row["gross_pnl"]),
            "transaction_cost": float(row["transaction_cost"]),
            "net_pnl": float(row["net_pnl"]),
            "portfolio_return": float(row["net_pnl"]) / float(row["paper_capital"]),
        })
    strategy_summary: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGY_ROLES:
        rows = [
            item for item in daily
            if item["strategy"] == strategy and item["status"] in {"closed", "cash"}
        ]
        returns = np.array([item["portfolio_return"] for item in rows], dtype=float)
        cumulative = float(np.prod(1.0 + returns) - 1.0) if len(returns) else None
        t_stat = None
        if len(returns) >= 2 and float(returns.std(ddof=1)) > 0:
            t_stat = float(returns.mean() / (returns.std(ddof=1) / math.sqrt(len(returns))))
        strategy_summary[strategy] = {
            "role": STRATEGY_ROLES[strategy],
            "completed_cohorts": len(rows),
            "cumulative_return": cumulative,
            "mean_daily_return": float(returns.mean()) if len(returns) else None,
            "daily_win_rate": float((returns > 0).mean()) if len(returns) else None,
            "daily_t_stat": t_stat,
        }
    baseline_cohorts = strategy_summary["model_baseline"]["completed_cohorts"]
    version_refs = {}
    for row in connection.execute("SELECT report_json FROM signal_run WHERE signal_date <= ?", (cutoff,)):
        report = json.loads(row["report_json"])
        for ref in report.get("data_versions", []):
            version_refs[ref["version"]] = ref
    return {
        "data_versions": list(version_refs.values()),
        "as_of_date": cutoff,
        "paper_capital_per_strategy": PAPER_CAPITAL,
        "daily_budget_fraction": DAILY_BUDGET_FRACTION,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "collector": {
            "runs": int(collector["runs"] or 0),
            "ok_runs": int(collector["ok_runs"] or 0),
            "last_finished_at": collector["last_finished_at"],
            "announcement_events": int(announcement_count),
        },
        "evaluation_gate": {
            "completed_baseline_cohorts": baseline_cohorts,
            "preliminary_minimum": PRELIMINARY_EVALUATION_COHORTS,
            "preliminary_ready": baseline_cohorts >= PRELIMINARY_EVALUATION_COHORTS,
            "robustness_minimum": ROBUST_EVALUATION_COHORTS,
            "robustness_ready": baseline_cohorts >= ROBUST_EVALUATION_COHORTS,
        },
        "strategies": strategy_summary,
        "daily": daily,
        "limitations": [
            "Paper trading only; no order is sent to a broker.",
            "Entries use observed Tushare 09:30-09:31 VWAP and are capped at 5% of minute turnover.",
            "Limit-down exits are carried, but queue position cannot be modeled without Level-2.",
            "Announcement labels are fixed title rules and remain experimental.",
        ],
    }


def _loads_json(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _build_counterfactual_dashboard(
    connection: sqlite3.Connection,
    limit: int,
) -> list[dict[str, Any]]:
    run_rows = connection.execute(
        """
        SELECT experiment_id, signal_date, created_at, snapshot_observed_at,
               previous_trade_date, planned_exit_date, assumption,
               candidate_mode, actionable, health_passed
        FROM counterfactual_run ORDER BY signal_date DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for run_row in run_rows:
        run = dict(run_row)
        order_rows = connection.execute(
            """
            SELECT strategy, role, stock_code, company_name, research_rank,
                   predicted_return, up_probability, announcement_score,
                   announcement_events_json, requested_notional, status,
                   entry_source, entry_price, entry_shares, entry_notional,
                   entry_capacity_5pct, planned_exit_date, actual_exit_date,
                   exit_price, gross_pnl, transaction_cost, net_pnl,
                   net_return_on_order
            FROM counterfactual_order WHERE experiment_id=?
            ORDER BY strategy, COALESCE(research_rank, 9999), stock_code
            """,
            (run["experiment_id"],),
        ).fetchall()
        orders = []
        for order_row in order_rows:
            order = dict(order_row)
            order["announcement_events"] = _loads_json(
                order.pop("announcement_events_json"), []
            )
            orders.append(order)
        strategy_results = []
        for strategy, role in STRATEGY_ROLES.items():
            strategy_orders = [
                order for order in orders if order["strategy"] == strategy
            ]
            terminal = sum(
                order["status"] in {
                    "CLOSED", "MISSED_ENTRY", "MISSED_CAPACITY",
                }
                for order in strategy_orders
            )
            closed = sum(
                order["status"] == "CLOSED" for order in strategy_orders
            )
            invested = sum(
                float(order["entry_notional"] or 0.0)
                for order in strategy_orders
            )
            net_pnl = sum(
                float(order["net_pnl"] or 0.0) for order in strategy_orders
            )
            status = "cash" if not strategy_orders else (
                "closed" if terminal == len(strategy_orders) else "open"
            )
            strategy_results.append({
                "strategy": strategy,
                "role": role,
                "selected_count": len(strategy_orders),
                "orders": len(strategy_orders),
                "closed_orders": closed,
                "invested": invested,
                "net_pnl": net_pnl,
                "portfolio_return": net_pnl / PAPER_CAPITAL,
                "status": status,
            })
        terminal_orders = sum(
            order["status"] in {
                "CLOSED", "MISSED_ENTRY", "MISSED_CAPACITY",
            }
            for order in orders
        )
        output.append({
            **run,
            "official_forward_sample": False,
            "strategy_results": strategy_results,
            "orders": orders,
            "process": {
                "total_orders": len(orders),
                "terminal_orders": terminal_orders,
                "settlement": (
                    "completed" if terminal_orders == len(orders)
                    else "partial" if any(
                        order["status"] == "CLOSED" for order in orders
                    )
                    else "pending"
                ),
            },
        })
    return output


def build_forward_dashboard(
    connection: sqlite3.Connection,
    limit: int = 60,
) -> dict[str, Any]:
    latest_stored_date = connection.execute(
        """
        SELECT MAX(record_date) AS value FROM (
            SELECT signal_date AS record_date FROM signal_run
            UNION ALL
            SELECT actual_exit_date AS record_date FROM paper_order
            WHERE actual_exit_date IS NOT NULL
        )
        """
    ).fetchone()["value"]
    report_date = _now().date()
    if latest_stored_date:
        report_date = max(report_date, date.fromisoformat(latest_stored_date))
    report = build_forward_report(connection, report_date)
    collector_runs = [dict(row) for row in connection.execute(
        """
        SELECT run_id, started_at, finished_at, source_date, observation_mode,
               status, rows_received, rows_inserted, error
        FROM collector_run ORDER BY started_at DESC LIMIT 20
        """
    ).fetchall()]
    signal_runs = connection.execute(
        """
        SELECT signal_date, generated_at, previous_trade_date,
               planned_exit_date, candidate_mode, actionable, health_passed
        FROM signal_run ORDER BY signal_date DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    daily_lookup = {
        (item["signal_date"], item["strategy"]): item for item in report["daily"]
    }
    days: list[dict[str, Any]] = []
    for signal in signal_runs:
        signal_date = signal["signal_date"]
        strategy_rows = connection.execute(
            """
            SELECT strategy, role, selected_count, paper_capital, daily_budget
            FROM strategy_signal WHERE signal_date=? ORDER BY strategy
            """,
            (signal_date,),
        ).fetchall()
        strategy_results = []
        for strategy_row in strategy_rows:
            metrics = daily_lookup.get(
                (signal_date, strategy_row["strategy"]), {}
            )
            strategy_results.append({
                **dict(strategy_row),
                "status": metrics.get("status", "open"),
                "orders": metrics.get("orders", 0),
                "open_orders": metrics.get("open_orders", 0),
                "closed_orders": metrics.get("closed_orders", 0),
                "invested": metrics.get("invested", 0.0),
                "net_pnl": metrics.get("net_pnl", 0.0),
                "portfolio_return": metrics.get("portfolio_return", 0.0),
            })
        order_rows = connection.execute(
            """
            SELECT strategy, role, stock_code, company_name, research_rank,
                   announcement_score, announcement_events_json,
                   requested_notional, status, entry_source, entry_price,
                   entry_shares, entry_notional, entry_capacity_5pct,
                   entry_recorded_at, planned_exit_date,
                   actual_exit_date, exit_price, net_pnl, net_return_on_order
            FROM paper_order WHERE signal_date=?
            ORDER BY strategy, COALESCE(research_rank, 9999), stock_code
            """,
            (signal_date,),
        ).fetchall()
        orders = []
        orders_by_stock: dict[str, list[dict[str, Any]]] = {}
        for row in order_rows:
            item = dict(row)
            item["announcement_events"] = _loads_json(
                item.pop("announcement_events_json"), []
            )
            orders.append(item)
            orders_by_stock.setdefault(item["stock_code"], []).append(item)

        candidate_rows = connection.execute(
            """
            SELECT stock_code, research_rank, model_rank, company_name,
                   industry_name, predicted_return, predicted_excess_return,
                   up_probability, prediction_score, announcement_score,
                   announcement_event_count, announcement_events_json
            FROM candidate_snapshot WHERE signal_date=? ORDER BY research_rank
            """,
            (signal_date,),
        ).fetchall()
        candidates = []
        candidate_codes: set[str] = set()
        for row in candidate_rows:
            candidate = dict(row)
            candidate["announcement_events"] = _loads_json(
                candidate.pop("announcement_events_json"), []
            )
            stock_code = candidate["stock_code"]
            candidate_codes.add(stock_code)
            stock_orders = orders_by_stock.get(stock_code, [])
            candidate["strategies"] = [item["strategy"] for item in stock_orders]
            candidate["baseline_order"] = next(
                (
                    item for item in stock_orders
                    if item["strategy"] == "model_baseline"
                ),
                None,
            )
            candidates.append(candidate)
        event_stocks = []
        for stock_code, stock_orders in sorted(orders_by_stock.items()):
            if stock_code in candidate_codes:
                continue
            representative = stock_orders[0]
            event_stocks.append({
                "stock_code": stock_code,
                "company_name": representative["company_name"],
                "announcement_score": representative["announcement_score"],
                "announcement_events": representative["announcement_events"],
                "strategies": [item["strategy"] for item in stock_orders],
                "order": representative,
            })
        total_orders = len(orders)
        terminal_orders = sum(
            item["status"] in {"CLOSED", "MISSED_ENTRY", "MISSED_CAPACITY"}
            for item in orders
        )
        entry_resolved_orders = sum(
            item["status"] in {
                "FILLED", "CLOSED", "MISSED_ENTRY", "MISSED_CAPACITY",
            }
            for item in orders
        )
        filled_or_closed = sum(
            item["status"] in {"FILLED", "CLOSED"} for item in orders
        )
        days.append({
            **dict(signal),
            "strategy_results": strategy_results,
            "candidates": candidates,
            "event_stocks": event_stocks,
            "orders": orders,
            "process": {
                "signal": "completed",
                "entry": (
                    "completed" if total_orders == entry_resolved_orders
                    else "partial" if filled_or_closed
                    else "pending"
                ),
                "settlement": (
                    "completed" if total_orders == terminal_orders
                    else "partial" if any(item["status"] == "CLOSED" for item in orders)
                    else "pending"
                ),
                "total_orders": total_orders,
                "terminal_orders": terminal_orders,
            },
        })

    completed_daily = [
        item for item in report["daily"] if item["status"] in {"closed", "cash"}
    ]
    dates = sorted({item["signal_date"] for item in completed_daily})
    cumulative = {strategy: 1.0 for strategy in STRATEGY_ROLES}
    daily_return_lookup = {
        (item["signal_date"], item["strategy"]): item["portfolio_return"]
        for item in completed_daily
    }
    equity_curve = []
    for signal_date in dates:
        point: dict[str, Any] = {"signal_date": signal_date}
        for strategy in STRATEGY_ROLES:
            daily_return = daily_return_lookup.get((signal_date, strategy))
            if daily_return is not None:
                cumulative[strategy] *= 1.0 + daily_return
            point[strategy] = cumulative[strategy] - 1.0
        equity_curve.append(point)
    return {
        "generated_at": _iso(_now()),
        "summary": {key: value for key, value in report.items() if key != "daily"},
        "equity_curve": equity_curve,
        "days": days,
        "counterfactual_runs": _build_counterfactual_dashboard(
            connection, limit
        ),
        "collector_runs": collector_runs,
    }


def write_forward_report(
    root: Path = DEFAULT_FORWARD_ROOT,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    connection = open_store(root)
    try:
        report = build_forward_report(connection, as_of_date)
    finally:
        connection.close()
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# CSI 300 forward paper-trading report",
        "",
        f"As of: {report['as_of_date']}",
        f"Announcements observed: {report['collector']['announcement_events']}",
        "",
        "| Strategy | Role | Completed | Cumulative | Daily mean | Win rate | Daily t |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in report["strategies"].items():
        def percent(value):
            return "-" if value is None else f"{value:.3%}"
        t_value = "-" if metrics["daily_t_stat"] is None else f"{metrics['daily_t_stat']:.2f}"
        lines.append(
            f"| {name} | {metrics['role']} | {metrics['completed_cohorts']} | "
            f"{percent(metrics['cumulative_return'])} | "
            f"{percent(metrics['mean_daily_return'])} | "
            f"{percent(metrics['daily_win_rate'])} | {t_value} |"
        )
    lines.extend(["", "Paper trading only. No broker orders are submitted.", ""])
    (reports / "latest.md").write_text("\n".join(lines), encoding="utf-8")
    return report
