"""Preregistered daily-state/auction interactions; isolated analytical ledgers.

No provider calls live here. Inputs, minute fills and exits are supplied by the
existing auction commands. Historical replay is development evidence only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

import numpy as np
import pandas as pd

from deepstock.data.store import ROOT, digest, write_json
from .auction import (
    AuctionBacktestConfig, CONTEXT_AUCTION_FEATURE_COLUMNS,
    fit_latest_auction_ensemble, rank_auction_strategy,
)
from .tushare_minute import SHANGHAI

POLICY = "auction_state_strength_v1"
CONFIG_PATH = ROOT / "config/auction_state_strength_v1.json"
TARGET = "target_0931_vwap_to_next_close"
STATES = ("previous_range_cs", "volume_ratio_20d_cs", "previous_close_position_cs")
STRENGTH = "study_gap_volume"
INTERACTIONS = tuple(f"study_{name}_gap_volume" for name in STATES)
FEATURES = {
    "A": CONTEXT_AUCTION_FEATURE_COLUMNS,
    "B": CONTEXT_AUCTION_FEATURE_COLUMNS + STATES + ("auction_gap_cs", STRENGTH),
    "C": CONTEXT_AUCTION_FEATURE_COLUMNS + STATES + ("auction_gap_cs", STRENGTH) + INTERACTIONS,
}
ARMS = tuple(FEATURES)
DATE_COLUMNS = ("trade_date", "target_date", "target_next_date")


@dataclass(frozen=True)
class PreparedAuctionInputs:
    feature_frame: pd.DataFrame
    config: AuctionBacktestConfig
    available_at: datetime
    evidence: dict[str, Any]


def specification() -> dict:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if value["policy_version"] != POLICY or tuple(value["state_features"]) != STATES:
        raise ValueError("Study specification does not match its version")
    return value


def model_config() -> AuctionBacktestConfig:
    spec = specification()
    return AuctionBacktestConfig(
        training_window_days=60, half_life_days=spec["half_life_days"],
        ridge_alpha=spec["ridge_alpha"], target_clip=spec["target_clip"],
        minimum_cross_section=spec["minimum_cross_section"], top_k=spec["top_k"],
        refit_frequency="daily", cost_bps=(0, 20, 50),
    )


def root_for(base_root: Path) -> Path:
    return Path(base_root).parent / POLICY


def activation(root: Path) -> dict | None:
    path = Path(root) / "activation.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["config_sha256"] != digest(CONFIG_PATH):
        raise ValueError("Study configuration changed after preregistration")
    return value


def initialize(root: Path, *, note_id: str, now: datetime | None = None) -> dict:
    """Freeze before replay; raw user notes remain local, untracked and intact."""
    root = Path(root)
    previous = activation(root)
    if previous:
        return previous
    now = now or datetime.now(SHANGHAI)
    notes = []
    for path in sorted((ROOT / "notebook").glob("*.md")):
        notes.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    if len(notes) != 5:
        raise ValueError("Expected the five original notebook files for preregistration")
    value = {
        "policy_version": POLICY, "config_sha256": digest(CONFIG_PATH),
        "frozen_at": now.isoformat(), "enabled": True,
        "user_decision": "approved_for_test", "research_note_id": note_id,
        "source_notes": notes, "feature_sets": {k: list(v) for k, v in FEATURES.items()},
        "adopted": "Prior daily state interacting with auction strength, tested without assumed direction",
        "deferred": ["9:20 order cancellations: no process data", "theme leader/related-stock trades: no PIT theme map"],
        "rejected": ["Fixed main-player accumulation/manipulation cycle"],
        "actionable": False,
    }
    write_json(root / "activation.json", value)
    return value


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    # Existing inputs are clipped and cross-sectionalized within PIT membership.
    # Preserve missing values; Ridge scales products using known training rows.
    out[STRENGTH] = out["auction_gap_cs"] * out["auction_volume_log_ratio_cs"]
    for source, target in zip(STATES, INTERACTIONS):
        out[target] = out[source] * out[STRENGTH]
    return out.replace([np.inf, -np.inf], np.nan)


def score(
    frame: pd.DataFrame, signal_date: date, config: AuctionBacktestConfig,
    *, include_full: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Same decision universe and training rows for A/B/C; never filter on fills."""
    augmented = add_features(frame)
    common = augmented.dropna(subset=list(FEATURES["C"]))
    groups = dict(FEATURES)
    if include_full:
        groups["A_full"] = FEATURES["A"]
    scored = {}
    training = {}
    for arm, names in groups.items():
        inputs = augmented if arm == "A_full" else common
        absolute = fit_latest_auction_ensemble(
            inputs, config, target_column=TARGET, feature_names=names,
            windows=tuple(specification()["ensemble_windows"]), as_of_date=signal_date,
        )
        excess = fit_latest_auction_ensemble(
            inputs, config, target_column=f"{TARGET}_universe_excess", feature_names=names,
            probability_target_column=TARGET,
            windows=tuple(specification()["ensemble_windows"]), as_of_date=signal_date,
        )
        scored[arm] = rank_auction_strategy(
            absolute, excess, inputs, signal_date, config.minimum_cross_section,
            top_k=config.top_k, cost_bps=20, minimum_model_agreement=0.5,
            max_per_industry=2,
        )
        training[arm] = {
            "last_target_date": str(inputs.loc[
                inputs["target_next_date"].lt(signal_date), "target_next_date"
            ].max()),
            "rows_by_window": {str(w): m.training_rows for w, m in zip(absolute.windows, absolute.models)},
        }
    return scored, {
        "full_decision_rows": int(augmented["trade_date"].eq(signal_date).sum()),
        "common_decision_rows": int(common["trade_date"].eq(signal_date).sum()),
        "training": training,
    }


def open_ledger(root: Path) -> sqlite3.Connection:
    from .forward import open_store
    connection = open_store(Path(root))
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS study_day (
            signal_date TEXT PRIMARY KEY, mode TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS study_attempt (
            signal_date TEXT NOT NULL, attempted_at TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY(signal_date, attempted_at)
        );
    """)
    return connection


def _json(value: Any) -> str:
    def fallback(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError(type(item).__name__)
    # DataFrame.to_json converts nonfinite floats to null.
    return json.dumps(value, ensure_ascii=False, default=fallback, allow_nan=False)


def _attempt(root: Path, day: date, status: str, reason: str, now: datetime) -> dict:
    with open_ledger(root) as db:
        db.execute("INSERT OR REPLACE INTO study_attempt VALUES (?, ?, ?, ?)",
                   (day.isoformat(), now.isoformat(), status, reason))
    db.close()
    return {"status": status, "reason": reason, "policy_version": POLICY}


def save_inputs(root: Path, prepared: PreparedAuctionInputs, day: date,
                previous: date, following: date, baseline_health: dict) -> None:
    """Keep the first shared observation; retries cannot replace it with late data."""
    root = Path(root)
    descriptor = root / "inputs" / f"{day}.json"
    if descriptor.exists():
        return
    folder = descriptor.parent
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.frame.json.gz"
    raw = prepared.feature_frame.to_json(
        orient="table", date_format="iso", double_precision=15,
    ).encode()
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(gzip.compress(raw, mtime=0))
    temporary.replace(path)
    write_json(descriptor, {
        "frame_sha256": digest(path), "config": asdict(prepared.config),
        "code_sha256": {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "available_at": prepared.available_at.isoformat(), "evidence": prepared.evidence,
        "previous_trade_date": previous.isoformat(), "planned_exit_date": following.isoformat(),
        "baseline_health": baseline_health, "policy_version": POLICY,
    })


def load_inputs(root: Path, day: date) -> tuple[PreparedAuctionInputs, dict]:
    descriptor = json.loads((root / "inputs" / f"{day}.json").read_text())
    path = root / "inputs" / f"{day}.frame.json.gz"
    if digest(path) != descriptor["frame_sha256"]:
        raise ValueError("Shared study input hash mismatch")
    frame = pd.read_json(io.StringIO(gzip.decompress(path.read_bytes()).decode()), orient="table")
    for name in DATE_COLUMNS:
        if name in frame:
            frame[name] = pd.to_datetime(frame[name]).dt.date
    return PreparedAuctionInputs(
        frame, AuctionBacktestConfig(**descriptor["config"]),
        datetime.fromisoformat(descriptor["available_at"]), descriptor["evidence"],
    ), descriptor


def record_decisions(
    db: sqlite3.Connection, scored: dict[str, pd.DataFrame], day: date,
    previous: date, following: date, decided_at: datetime, metadata: dict,
    *, mode: str,
    persistence_clock: Callable[[], datetime] | None = None,
) -> dict:
    existing = db.execute("SELECT 1 FROM signal_run WHERE signal_date=?", (str(day),)).fetchone()
    if existing:
        return {"status": "already_recorded", "signal_date": str(day)}
    if following <= day or previous >= day:
        raise ValueError("Study must respect trading neighbors and A-share T+1")
    if mode == "forward":
        _require_preopen(decided_at, day)
    report = {
        "policy_version": POLICY, "actionable": False, "mode": mode,
        "evidence_label": "prospective_diagnostic" if mode == "forward" else "historical_development_diagnostic",
        **metadata,
        "scores": {arm: json.loads(rows.to_json(orient="records", date_format="iso")) for arm, rows in scored.items()},
    }
    spec = specification()
    budget = spec["paper_capital"] * spec["daily_budget_fraction"]
    counts = {}
    with db:
        db.execute("INSERT INTO signal_run VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            str(day), decided_at.isoformat(), str(previous), str(following),
            "state_strength_diagnostic", 0, 0, _json(report),
        ))
        db.execute("INSERT INTO study_day VALUES (?, ?, ?)", (str(day), mode, _json(metadata)))
        for arm, rows in scored.items():
            selected = rows[rows["strategy_selected"]].copy()
            counts[arm] = len(selected)
            db.execute("INSERT INTO strategy_signal VALUES (?, ?, ?, ?, ?, ?)",
                       (str(day), arm, "diagnostic", len(selected), spec["paper_capital"], budget))
            for row in selected.to_dict("records"):
                code = str(row["stock_code"])[:6].zfill(6)
                order_id = hashlib.sha256(f"{POLICY}|{mode}|{day}|{arm}|{code}".encode()).hexdigest()
                db.execute("""INSERT INTO paper_order (
                    order_id, signal_date, strategy, role, stock_code, company_name,
                    research_rank, announcement_score, announcement_events_json,
                    requested_notional, status, entry_date, planned_exit_date
                ) VALUES (?, ?, ?, 'diagnostic', ?, ?, ?, 0, '[]', ?, 'SIGNALLED', ?, ?)""", (
                    order_id, str(day), arm, code, row.get("company_name"),
                    int(row["strategy_rank"]), budget / len(selected), str(day), str(following),
                ))
        if mode == "forward" and persistence_clock is not None:
            _require_preopen(persistence_clock(), day)
    if mode == "forward" and persistence_clock is not None:
        persisted_at = persistence_clock()
        try:
            _require_preopen(persisted_at, day)
        except ValueError:
            # A commit crossing the boundary is retained solely as late evidence;
            # it cannot receive analytical fills or count as a forward decision.
            report.update(mode="late_diagnostic", official_forward_sample=False,
                          evidence_label="late_persistence_diagnostic")
            with db:
                db.execute("UPDATE study_day SET mode='late_diagnostic' WHERE signal_date=?", (str(day),))
                db.execute("UPDATE paper_order SET status='MISSED_DEADLINE' WHERE signal_date=? AND status='SIGNALLED'", (str(day),))
                db.execute("UPDATE signal_run SET generated_at=?, candidate_mode='late_diagnostic', report_json=? WHERE signal_date=?",
                           (persisted_at.isoformat(), _json(report), str(day)))
            return {"status": "missed_decision_cutoff", "signal_date": str(day), "actionable": False}
    return {"status": "recorded", "signal_date": str(day), "selected": counts, "actionable": False}


def _require_preopen(now: datetime, day: date) -> None:
    if now.tzinfo is None:
        raise ValueError("Study decision time must be timezone-aware")
    local = now.astimezone(SHANGHAI)
    if local.date() != day or not time(9, 25) <= local.time().replace(tzinfo=None) < time(9, 30):
        raise ValueError("Study decision missed 09:25-09:30 cutoff")


def run_forward(root: Path, day: date, *, now_fn: Callable[[], datetime] | None = None) -> dict:
    now_fn = now_fn or (lambda: datetime.now(SHANGHAI))
    active = activation(root)
    if not active or not active["enabled"]:
        return {"status": "disabled"}
    now = now_fn()
    with open_ledger(root) as db:
        existing = db.execute("SELECT 1 FROM signal_run WHERE signal_date=?", (str(day),)).fetchone()
    db.close()
    if existing:
        return {"status": "already_recorded", "signal_date": str(day)}
    try:
        _require_preopen(now, day)
        frozen_at = datetime.fromisoformat(active["frozen_at"])
        if now < frozen_at:
            raise ValueError("Study cannot predate preregistration")
        prepared, descriptor = load_inputs(root, day)
        _require_preopen(prepared.available_at, day)
        if prepared.available_at > now:
            raise ValueError("Shared input observed after decision time")
        scored, diagnostics = score(prepared.feature_frame, day, model_config())
        # Check again after fitting: saved inputs cannot justify a late decision.
        decided_at = now_fn()
        _require_preopen(decided_at, day)
        db = open_ledger(root)
        try:
            return record_decisions(
                db, scored, day, date.fromisoformat(descriptor["previous_trade_date"]),
                date.fromisoformat(descriptor["planned_exit_date"]), decided_at,
                {**prepared.evidence, "input_sha256": descriptor["frame_sha256"],
                 "diagnostics": diagnostics, "baseline_health": descriptor["baseline_health"],
                 "frozen_at": active["frozen_at"], "config_sha256": active["config_sha256"],
                 "code_sha256": descriptor.get("code_sha256", {})},
                mode="forward", persistence_clock=now_fn,
            )
        finally:
            db.close()
    except Exception as error:
        return _attempt(root, day, "input_blocked", f"{type(error).__name__}: {error}", now_fn())


def after_baseline(base_root: Path, day: date, prepared: PreparedAuctionInputs | None = None,
                   previous: date | None = None, following: date | None = None,
                   health: dict | None = None) -> dict | None:
    """Optional side branch: errors never invalidate a completed frozen signal."""
    root = root_for(base_root)
    try:
        active = activation(root)
        if not active or not active["enabled"]:
            return None
        if prepared is not None:
            save_inputs(root, prepared, day, previous, following, health or {})
        return run_forward(root, day)
    except Exception as error:
        return {"status": "input_blocked", "reason": f"{type(error).__name__}: {error}"}


def pending_codes(root: Path, day: date) -> list[str]:
    path = root / "forward.sqlite3"
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute(
            "SELECT DISTINCT stock_code FROM paper_order WHERE entry_date=? AND status='SIGNALLED' ORDER BY stock_code",
            (str(day),),
        )]


def apply_shared_fills(root: Path, day: date, entries: pd.DataFrame) -> dict | None:
    if not (root / "forward.sqlite3").exists():
        return None
    from .forward import apply_entry_fills, store_entry_minute_snapshots
    db = open_ledger(root)
    try:
        if not entries.empty and "bar_time" in entries:
            store_entry_minute_snapshots(db, entries)
        return apply_entry_fills(db, day, entries)
    finally:
        db.close()


def first_exit_date(root: Path, day: date) -> date | None:
    if not (root / "forward.sqlite3").exists():
        return None
    with sqlite3.connect(f"file:{root / 'forward.sqlite3'}?mode=ro", uri=True) as db:
        row = db.execute("SELECT MIN(planned_exit_date) FROM paper_order WHERE status='FILLED' AND planned_exit_date<=?", (str(day),)).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def apply_shared_exits(root: Path, day: date, market_rows: dict) -> dict | None:
    if not (root / "forward.sqlite3").exists():
        return None
    from .forward import apply_exit_prices, expire_unfilled_entries
    db = open_ledger(root)
    try:
        expired = expire_unfilled_entries(db, day)
        return {"expired_entries": expired, **apply_exit_prices(db, market_rows, day)}
    finally:
        db.close()


def _stats(values: list[float]) -> dict:
    array = np.array(values, dtype=float)
    std = float(array.std(ddof=1)) if len(array) >= 2 else 0.0
    return {
        "days": len(values), "mean_daily_return": float(array.mean()) if len(array) else None,
        "cohort_compounded_return": float(np.prod(1 + array) - 1) if len(array) else None,
        "daily_t_stat": float(array.mean() / (std / np.sqrt(len(array)))) if std > 0 else None,
    }


def build_report(root: Path, *, mode: str = "forward", as_of: date | None = None) -> dict:
    spec = specification()
    result = {
        "policy_version": POLICY, "mode": mode, "actionable": False,
        "evidence_label": "prospective_diagnostic" if mode == "forward" else "historical_development_diagnostic",
        "annualized_return": None, "return_basis": "signal-date cohort P&L / fixed paper capital; not account NAV",
        "as_of_date": str(as_of or datetime.now(SHANGHAI).date()),
        "activation": activation(root), "arms": {}, "daily": [], "attempts": [],
    }
    if not (root / "forward.sqlite3").exists():
        result["status"] = "awaiting_forward_signals"
        return result
    db = open_ledger(root)
    try:
        rows = db.execute("""SELECT ss.*, sd.metadata_json FROM strategy_signal ss
            JOIN study_day sd USING(signal_date) WHERE sd.mode=? AND ss.signal_date<=?
            ORDER BY ss.signal_date, ss.strategy""", (mode, result["as_of_date"])).fetchall()
        for row in rows:
            orders = db.execute("SELECT * FROM paper_order WHERE signal_date=? AND strategy=?",
                                (row["signal_date"], row["strategy"])).fetchall()
            counts = {state: sum(o["status"] == state for o in orders) for state in (
                "SIGNALLED", "FILLED", "CLOSED", "MISSED_ENTRY", "MISSED_CAPACITY",
            )}
            status = "cash" if not orders else (
                "missing_entry" if counts["MISSED_ENTRY"] else (
                    "open" if counts["SIGNALLED"] or counts["FILLED"] else "closed"
                )
            )
            net = sum(o["net_pnl"] or 0 for o in orders) / row["paper_capital"]
            invested = sum(o["entry_notional"] or 0 for o in orders)
            metadata = json.loads(row["metadata_json"])
            evaluable = status in ("cash", "closed") and not metadata.get("entry_input_blocked", False)
            result["daily"].append({
                "signal_date": row["signal_date"], "arm": row["strategy"], "status": status,
                "evaluable": evaluable, "selected_count": row["selected_count"], **counts,
                "net_return_20bp": net if evaluable else None,
                "net_return_50bp": net - invested / row["paper_capital"] * 0.003 if evaluable else None,
                "baseline_health_passed": metadata.get("baseline_health", {}).get("passed"),
            })
        result["attempts"] = [dict(row) for row in db.execute("SELECT * FROM study_attempt ORDER BY attempted_at DESC LIMIT 100")]
        result["late_persistence_days"] = [r[0] for r in db.execute(
            "SELECT signal_date FROM study_day WHERE mode='late_diagnostic' ORDER BY signal_date"
        )]
        result["data_versions"] = list({
            ref["version"]: ref for row in rows for ref in json.loads(row["metadata_json"]).get("data_versions", [])
        }.values())
    finally:
        db.close()
    daily = result["daily"]
    by_arm = {arm: {r["signal_date"]: r for r in daily if r["arm"] == arm and r["evaluable"]} for arm in ARMS}
    common_days = sorted(set.intersection(*(set(v) for v in by_arm.values())))
    for arm in (*ARMS, "A_full"):
        arm_rows = [r for r in daily if r["arm"] == arm]
        valid = [r for r in arm_rows if r["evaluable"]]
        result["arms"][arm] = {
            **_stats([r["net_return_20bp"] for r in valid]),
            "active_day_rate": sum(r["selected_count"] > 0 for r in valid) / len(valid) if valid else None,
            "signal_days": len(arm_rows), "missing_entry_days": sum(r["status"] == "missing_entry" for r in arm_rows),
            "open_days": sum(r["status"] == "open" for r in arm_rows),
            "completed_orders": sum(r["CLOSED"] for r in valid),
            "stress_50bp": _stats([r["net_return_50bp"] for r in valid]),
            "common_days_20bp": _stats([by_arm[arm][d]["net_return_20bp"] for d in common_days]) if arm in ARMS else None,
        }
    result["comparisons"] = {
        f"C_minus_{other}": {
            str(cost): _stats([by_arm["C"][d][f"net_return_{cost}bp"] - by_arm[other][d][f"net_return_{cost}bp"] for d in common_days])
            for cost in (20, 50)
        } for other in ("A", "B")
    }
    c = result["arms"]["C"]
    checks = {
        "minimum_common_completed_days": len(common_days) >= spec["review_completed_days"],
        "positive_candidate_mean": (c["common_days_20bp"]["mean_daily_return"] or 0) > 0,
        "positive_increment_over_A": (result["comparisons"]["C_minus_A"]["20"]["mean_daily_return"] or 0) > 0,
        "positive_increment_over_B": (result["comparisons"]["C_minus_B"]["20"]["mean_daily_return"] or 0) > 0,
        "daily_t_at_least_one": (c["common_days_20bp"]["daily_t_stat"] or 0) >= 1,
        "active_share_at_least_20pct": (
            sum(by_arm["C"][d]["selected_count"] > 0 for d in common_days) / len(common_days)
            if common_days else 0
        ) >= 0.2,
    }
    result["review"] = {"common_completed_days": len(common_days), "checks": checks,
                        "ready_for_manual_review": mode == "forward" and all(checks.values()),
                        "execution_health_passed": False,
                        "health_limitation": "Independent execution health/universe spread requires separate review; baseline health is not inherited",
                        "automatic_promotion": False}
    result["status"] = "observing" if daily else "awaiting_forward_signals"
    return result


def write_report(root: Path, *, mode: str = "forward", as_of: date | None = None) -> dict:
    report = build_report(root, mode=mode, as_of=as_of)
    folder = root / "reports"
    write_json(folder / f"{mode}.json", report)
    lines = [f"# {POLICY}", "", f"Evidence: {report['evidence_label']}", "",
             "| Arm | Completed | Mean after 20bp | Daily t | Active share |", "|---|---:|---:|---:|---:|"]
    for arm, metrics in report["arms"].items():
        def fmt(v):
            return "-" if v is None else f"{v:.4%}"
        lines.append(f"| {arm} | {metrics['days']} | {fmt(metrics['mean_daily_return'])} | {metrics['daily_t_stat']} | {fmt(metrics['active_day_rate'])} |")
    lines.extend(["", "Research only. Cohort returns are not account NAV and are not annualized.",
                  "No automatic promotion or broker orders. Missing quotes are not cash returns.", ""])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{mode}.md").write_text("\n".join(lines), encoding="utf-8")
    return report
