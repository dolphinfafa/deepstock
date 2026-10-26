"""One fixed, paper-only ETF pilot with durable, at-most-once submission.

This is an execution plumbing test, separate from strategy authorizations and
the general execution agent. USD 200 is the securities-notional ceiling; fees
are reported separately and require an additional USD 5 of virtual cash.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import hashlib
import json
import os
from pathlib import Path
from typing import Any

PILOT_ID = "paper-etf-200-70-30-v1"
LEGS = (("VOO", 136155102, "ARCA", "140"),
        ("QQQM", 449738108, "NASDAQ", "60"))
TERMINAL_FAILURES = {"REJECTED", "CANCELLED", "APICANCELLED", "INACTIVE"}


def number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite numeric evidence")
    return result


def reference(symbol: str) -> str:
    return "dsp-" + hashlib.sha256(f"{PILOT_ID}:{symbol}".encode()).hexdigest()[:24]


def durable_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def pilot_lock(path: Path):
    """OS-owned lock: process death releases it; stale files are harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_snapshot(snapshot: dict, now: datetime, *, execution: bool = True) -> None:
    if (snapshot.get("host"), snapshot.get("port")) != ("127.0.0.1", 7497):
        raise ValueError("paper pilot requires localhost:7497")
    if snapshot.get("paper_account_verified") is not True:
        raise ValueError("a single pinned DU paper account is required")
    if not snapshot.get("account_hash"):
        raise ValueError("paper account pin is missing")
    if snapshot.get("reconciliation_complete") is not True:
        raise ValueError("broker reconciliation is incomplete")
    if any(number(snapshot["positions"][symbol]) < 0 for symbol, *_ in LEGS):
        raise ValueError("pilot instruments must not have short positions")
    if not execution:
        return
    if snapshot.get("server_version", 0) < 163:
        raise ValueError("API protocol predates fractional-share order support (163)")
    if snapshot.get("in_regular_hours") is not True:
        raise ValueError("outside the exchange-calendar pilot execution window")
    for field in ("cash_balance_usd", "available_usd"):
        if number(snapshot.get(field, "NaN")) < Decimal("205"):
            raise ValueError("insufficient USD virtual cash, including fee reserve")
    # The account snapshot is local and ephemeral, never serialized with IDs.
    if snapshot.get("conflicting_open_orders"):
        raise ValueError("another open order exists for a pilot instrument")
    for symbol, _, _, _ in LEGS:
        quote = snapshot["quotes"][symbol]
        bid, ask = number(quote["bid"]), number(quote["ask"])
        observed = datetime.fromisoformat(quote["observed_at"])
        age = (now - observed).total_seconds()
        if (quote.get("market_data_type") != 1 or not 0 <= age <= 15
                or not 0 < bid <= ask or (ask - bid) / ask > Decimal("0.005")):
            raise ValueError("fresh, real-time, narrow-spread bid/ask required")


def create_plan(snapshot: dict, now: datetime) -> list[dict]:
    validate_snapshot(snapshot, now)
    rows = []
    for symbol, conid, exchange, budget in LEGS:
        ask = number(snapshot["quotes"][symbol]["ask"])
        limit = (ask * Decimal("1.001")).quantize(Decimal("0.01"), rounding=ROUND_UP)
        quantity = (Decimal(budget) / limit).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if quantity <= 0:
            raise ValueError("budget cannot buy the minimum fractional quantity")
        rows.append(dict(symbol=symbol, conid=conid, primary_exchange=exchange,
                         budget_usd=budget, quantity=str(quantity), limit_price=str(limit),
                         order_ref=reference(symbol)))
    return rows


def validate_rows(rows: list[dict]) -> None:
    if len(rows) != len(LEGS):
        raise ValueError("pilot must contain exactly the two fixed legs")
    for row, (symbol, conid, exchange, budget) in zip(rows, LEGS, strict=True):
        if any(row.get(key) != expected for key, expected in (
            ("symbol", symbol), ("conid", conid), ("primary_exchange", exchange),
            ("budget_usd", budget), ("order_ref", reference(symbol)),
        )):
            raise ValueError("fixed pilot definition changed")
        quantity, limit = number(row["quantity"]), number(row["limit_price"])
        if not 0 < quantity * limit <= Decimal(budget) or quantity <= 0 or limit <= 0:
            raise ValueError("order exceeds its fixed notional budget")


def run_cycle(broker, path: Path, trade_date: str, now: datetime) -> dict:
    """Caller holds pilot_lock. Broker calls never precede durable intent.

    A missing broker acknowledgement leaves INTENT_RECORDED. Its absence from
    later broker snapshots is never treated as evidence that it was not sent.
    """
    snapshot = broker.snapshot()
    validate_snapshot(snapshot, now, execution=False)
    journal = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    observed = snapshot.get("orders", {})
    if journal is None:
        if any(reference(symbol) in observed for symbol, *_ in LEGS):
            raise ValueError("orphan broker order: local journal must be recovered")
        rows = create_plan(snapshot, now)
        journal = dict(schema=1, pilot_id=PILOT_ID, trade_date=trade_date,
                       account_hash=snapshot["account_hash"], plan=rows, orders={},
                       position_baseline=snapshot["positions"])
        durable_json(path, journal)
    if (journal.get("schema"), journal.get("pilot_id"), journal.get("account_hash"),
        journal.get("trade_date")) != (1, PILOT_ID, snapshot["account_hash"], trade_date):
        raise ValueError("journal identity changed; automatic reset is forbidden")
    rows = journal["plan"]
    validate_rows(rows)
    orders = journal["orders"]
    for row in rows:
        ref = row["order_ref"]
        if ref not in observed:
            continue
        if ref not in orders:
            raise ValueError("broker order without local intent requires review")
        evidence = observed[ref]
        if evidence.get("matches_plan") is not True:
            raise ValueError("broker order differs from immutable pilot plan")
        filled = number(evidence.get("filled", "0"))
        if not 0 <= filled <= number(row["quantity"]):
            raise ValueError("broker filled quantity exceeds pilot plan")
        if filled < number(orders[ref].get("filled", "0")):
            continue  # A duplicate/older callback cannot reverse a fill.
        orders[ref].update({key: value for key, value in evidence.items()
                            if value is not None or key not in orders[ref]})
        if filled == number(row["quantity"]):
            orders[ref]["status"] = "FILLED"
        elif orders[ref]["status"] == "FILLED":
            orders[ref]["status"] = "UNKNOWN"
    journal["updated_at"] = now.isoformat()
    durable_json(path, journal)
    statuses = {item["status"] for item in orders.values()}
    if len(orders) == 2 and statuses == {"FILLED"}:
        reconciled = all(abs(number(snapshot["positions"][row["symbol"]]) -
                             number(journal["position_baseline"][row["symbol"]]) -
                             number(row["quantity"])) <= Decimal("0.000001") for row in rows)
        if not reconciled:
            return dict(status="AWAITING_POSITION_RECONCILIATION", pilot_id=PILOT_ID, orders=orders)
        return dict(status="FILLED", pilot_id=PILOT_ID, orders=orders)
    if statuses & TERMINAL_FAILURES:
        return dict(status="STOPPED", pilot_id=PILOT_ID, orders=orders)
    if statuses - {"SUBMITTED", "PRESUBMITTED", "PARTIAL", "FILLED"}:
        return dict(status="NEEDS_RECONCILIATION", pilot_id=PILOT_ID, orders=orders)
    pending = [row for row in rows if row["order_ref"] not in orders]
    if not pending:
        return dict(status="WORKING", pilot_id=PILOT_ID, orders=orders)
    validate_snapshot(snapshot, now)
    if now.date().isoformat() != trade_date:
        raise ValueError("pilot trade date expired")
    for row in pending:
        ask = number(snapshot["quotes"][row["symbol"]]["ask"])
        limit = number(row["limit_price"])
        if not limit * Decimal("0.99") <= ask <= limit:
            raise ValueError("price moved outside immutable limit band")
        broker.preview(row)  # All pending legs must pass before submitting any.
    for row in pending:
        ref = row["order_ref"]
        orders[ref] = dict(status="INTENT_RECORDED", filled="0",
                           intent_at=datetime.now(timezone.utc).isoformat())
        durable_json(path, journal)
        # Exceptions, timeouts and process death all retain the durable intent.
        broker.submit(row)
        return dict(status="SUBMITTED", pilot_id=PILOT_ID, orders=orders)
    raise AssertionError("unreachable")
