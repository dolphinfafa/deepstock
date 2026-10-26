from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from deepstock.ibkr_pilot import (
    LEGS, create_plan, durable_json, pilot_lock, reference, run_cycle, validate_rows,
    validate_snapshot,
)
from scripts.ibkr_pilot_tws import PaperClient, execution_window, order_for

NOW = datetime(2026, 10, 26, 13, 35, tzinfo=timezone.utc)


def snapshot():
    return dict(host="127.0.0.1", port=7497, paper_account_verified=True,
        account_hash="test-only-pin", server_version=178, reconciliation_complete=True,
        in_regular_hours=True, cash_balance_usd="1000", available_usd="1000",
        conflicting_open_orders=False, orders={}, positions={"VOO": "0", "QQQM": "0"}, quotes={
            symbol: dict(bid=price, ask=price, market_data_type=1, observed_at=NOW.isoformat())
            for symbol, price in (("VOO", "600"), ("QQQM", "250"))})


class Broker:
    def __init__(self, value=None):
        self.value = value or snapshot()
        self.submissions = []
        self.previews = []
        self.die = False
        self.reject_preview = False

    def snapshot(self):
        return copy.deepcopy(self.value)

    def preview(self, row):
        self.previews.append(row["symbol"])
        if self.reject_preview and row["symbol"] == "QQQM":
            raise ValueError("fractional trading unavailable")

    def submit(self, row):
        self.submissions.append(copy.deepcopy(row))
        if self.die:
            raise SystemExit("simulate process death after send")
        self.value["orders"][row["order_ref"]] = dict(status="SUBMITTED", filled="0", matches_plan=True)

    def fill(self, index):
        row = self.submissions[index]
        self.value["orders"][row["order_ref"]].update(status="FILLED", filled=row["quantity"])
        self.value["positions"][row["symbol"]] = str(
            Decimal(self.value["positions"][row["symbol"]]) + Decimal(row["quantity"]))


def test_two_leg_process_fills_and_repeated_restart_never_resubmits(tmp_path):
    broker = Broker()
    path = tmp_path / "journal.json"
    assert run_cycle(broker, path, "2026-10-26", NOW)["status"] == "SUBMITTED"
    assert broker.previews == ["VOO", "QQQM"]
    broker.fill(0)
    assert run_cycle(broker, path, "2026-10-26", NOW)["status"] == "SUBMITTED"
    broker.fill(1)
    for _ in range(3):
        restarted = Broker(broker.snapshot())
        assert run_cycle(restarted, path, "2026-10-26", NOW)["status"] == "FILLED"
        assert not restarted.submissions
    assert [row["symbol"] for row in broker.submissions] == ["VOO", "QQQM"]
    assert sum(Decimal(row["quantity"]) * Decimal(row["limit_price"])
               for row in broker.submissions) <= 200


def test_intent_survives_death_and_missing_ack_is_not_retried(tmp_path):
    broker = Broker()
    broker.die = True
    path = tmp_path / "journal.json"
    with pytest.raises(SystemExit):
        run_cycle(broker, path, "2026-10-26", NOW)
    assert json.loads(path.read_text())["orders"][reference("VOO")]["status"] == "INTENT_RECORDED"
    restarted = Broker()
    for _ in range(3):
        assert run_cycle(restarted, path, "2026-10-26", NOW)["status"] == "NEEDS_RECONCILIATION"
    assert not restarted.submissions
    row = broker.submissions[0]
    restarted.value["orders"][reference("VOO")] = dict(
        status="FILLED", filled=row["quantity"], matches_plan=True)
    assert run_cycle(restarted, path, "2026-10-26", NOW)["status"] == "SUBMITTED"
    assert [row["symbol"] for row in restarted.submissions] == ["QQQM"]


def test_preview_failure_sends_neither_leg_and_creates_no_intent(tmp_path):
    broker = Broker()
    broker.reject_preview = True
    path = tmp_path / "journal.json"
    with pytest.raises(ValueError, match="fractional"):
        run_cycle(broker, path, "2026-10-26", NOW)
    assert not broker.submissions
    assert not json.loads(path.read_text())["orders"]


@pytest.mark.parametrize("field,value", [
    ("port", 7496), ("host", "localhost"), ("paper_account_verified", False),
    ("reconciliation_complete", False), ("server_version", 157),
    ("in_regular_hours", False), ("cash_balance_usd", "204.99"),
    ("available_usd", "NaN"), ("conflicting_open_orders", True),
])
def test_bad_account_cash_market_or_protocol_never_sends(tmp_path, field, value):
    broker = Broker()
    broker.value[field] = value
    with pytest.raises(ValueError):
        run_cycle(broker, tmp_path / "journal.json", "2026-10-26", NOW)
    assert not broker.submissions


@pytest.mark.parametrize("changes", [
    dict(ask="NaN"), dict(bid="Infinity"), dict(market_data_type=3),
    dict(bid="580"), dict(observed_at=(NOW - timedelta(seconds=16)).isoformat()),
    dict(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
])
def test_unusable_quotes_never_send(tmp_path, changes):
    broker = Broker()
    broker.value["quotes"]["VOO"].update(changes)
    with pytest.raises(ValueError):
        run_cycle(broker, tmp_path / "journal.json", "2026-10-26", NOW)
    assert not broker.submissions


def test_journal_or_broker_conflict_cannot_reset_pilot(tmp_path):
    path = tmp_path / "journal.json"
    broker = Broker()
    run_cycle(broker, path, "2026-10-26", NOW)
    changed = Broker(broker.snapshot())
    changed.value["account_hash"] = "different-account"
    with pytest.raises(ValueError, match="identity"):
        run_cycle(changed, path, "2026-10-26", NOW)
    with pytest.raises(ValueError, match="identity"):
        run_cycle(broker, path, "2026-10-27", NOW)
    broker.value["orders"][reference("VOO")]["matches_plan"] = False
    with pytest.raises(ValueError, match="differs"):
        run_cycle(broker, path, "2026-10-26", NOW)
    assert len(broker.submissions) == 1


def test_rejection_partial_fill_and_stale_callback_do_not_resubmit(tmp_path):
    path = tmp_path / "journal.json"
    broker = Broker()
    run_cycle(broker, path, "2026-10-26", NOW)
    broker.value["orders"][reference("VOO")].update(status="PARTIAL", filled="0.01")
    run_cycle(broker, path, "2026-10-26", NOW)
    broker.value["orders"][reference("VOO")].update(status="SUBMITTED", filled="0")
    broker.value["orders"][reference("QQQM")].update(status="REJECTED")
    result = run_cycle(broker, path, "2026-10-26", NOW)
    assert result["status"] == "STOPPED"
    assert result["orders"][reference("VOO")]["filled"] == "0.01"
    assert len(broker.submissions) == 2


def test_filled_label_without_fill_quantity_is_ambiguous(tmp_path):
    path = tmp_path / "journal.json"
    broker = Broker()
    run_cycle(broker, path, "2026-10-26", NOW)
    broker.value["orders"][reference("VOO")].update(status="FILLED", filled="0")
    assert run_cycle(broker, path, "2026-10-26", NOW)["status"] == "NEEDS_RECONCILIATION"
    assert len(broker.submissions) == 1


def test_completed_orders_wait_for_actual_position_reconciliation(tmp_path):
    path = tmp_path / "journal.json"
    broker = Broker()
    run_cycle(broker, path, "2026-10-26", NOW)
    broker.fill(0)
    run_cycle(broker, path, "2026-10-26", NOW)
    row = broker.submissions[1]
    broker.value["orders"][row["order_ref"]].update(status="FILLED", filled=row["quantity"])
    assert run_cycle(broker, path, "2026-10-26", NOW)["status"] == "AWAITING_POSITION_RECONCILIATION"
    broker.value["positions"]["QQQM"] = row["quantity"]
    assert run_cycle(broker, path, "2026-10-26", NOW)["status"] == "FILLED"
    assert len(broker.submissions) == 2


def test_altered_quantities_and_orphan_orders_are_rejected(tmp_path):
    rows = create_plan(snapshot(), NOW)
    rows[0]["quantity"] = "1"
    with pytest.raises(ValueError, match="budget"):
        validate_rows(rows)
    broker = Broker()
    broker.value["orders"][reference("VOO")] = dict(status="SUBMITTED", matches_plan=True)
    with pytest.raises(ValueError, match="orphan"):
        run_cycle(broker, tmp_path / "missing.json", "2026-10-26", NOW)
    assert not broker.submissions


def test_lock_excludes_other_process_and_releases(tmp_path):
    path = tmp_path / "pilot.lock"
    code = ("from pathlib import Path; from deepstock.ibkr_pilot import pilot_lock; "
            "ctx=pilot_lock(Path(__import__('sys').argv[1])); "
            "print(ctx.__enter__()); ctx.__exit__(None,None,None)")
    with pilot_lock(path) as acquired:
        assert acquired
        other = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=10)
        assert other.returncode == 0, other.stderr
        assert other.stdout.strip() == "False"
    with pilot_lock(path) as acquired:
        assert acquired


@pytest.mark.parametrize("accounts", [["U123"], ["DU123", "U123"], [], ["DUMMY"]])
def test_tws_adapter_rejects_live_or_unknown_account(monkeypatch, accounts):
    client = PaperClient("test-salt", "wrong-hash")
    client.accounts = accounts
    client.ready.set()
    client.managed.set()
    monkeypatch.setattr(client, "connect", lambda *args: None)
    monkeypatch.setattr(client, "run", lambda: None)
    with pytest.raises(ValueError, match="DU paper"):
        client.start()


def test_tws_order_guard_blocks_real_submission_even_with_valid_paper_pin(monkeypatch):
    client = PaperClient("salt")
    client.account = "DU123456"
    client.account_hash = client.expected_hash = hashlib.sha256(b"salt:DU123").hexdigest()
    monkeypatch.setattr(client, "isConnected", lambda: True)
    from scripts.ibkr_pilot_tws import contract_for
    row = create_plan(snapshot(), NOW)[0]
    with pytest.raises(ValueError, match="unapproved"):
        client.placeOrder(1, contract_for(row), order_for(row, client.account, preview=False))
    client.enabled = True
    client.enable_check = lambda: False
    with pytest.raises(ValueError, match="enable flag"):
        client.placeOrder(1, contract_for(row), order_for(row, client.account, preview=False))
    client.account = "U123"
    with pytest.raises(ValueError, match="paper-only"):
        client.placeOrder(1, contract_for(row), order_for(row, client.account, preview=True))
    with pytest.raises(ValueError, match="never cancels"):
        client.reqGlobalCancel()


def test_tws_callbacks_reconcile_unset_quantity_execution_and_duplicate_fee():
    from scripts.ibkr_pilot_tws import contract_for
    from ibapi.order_state import OrderState
    row = create_plan(snapshot(), NOW)[0]
    client = PaperClient("salt", plan=[row])
    client.account = "DU123456"
    order = order_for(row, client.account, preview=False)
    order.permId = 123
    state = OrderState()
    state.status = "Submitted"
    contract = contract_for(row)
    client.openOrder(12, contract, order, state)
    assert client.orders[row["order_ref"]]["filled"] == "0"
    assert client.orders[row["order_ref"]]["matches_plan"]
    execution = SimpleNamespace(orderRef=row["order_ref"], acctNumber=client.account,
        side="BOT", price=600, execId="test-fill", permId=123, orderId=12,
        cumQty=Decimal(row["quantity"]), avgPrice=600)
    client.execDetails(11, contract, execution)
    client.execDetails(11, contract, execution)
    fee = SimpleNamespace(currency="USD", execId="test-fill", commissionAndFees=0.35)
    client.commissionAndFeesReport(fee)
    client.commissionAndFeesReport(fee)
    observed = client.snapshot()["orders"][row["order_ref"]]
    assert observed["filled"] == row["quantity"]
    assert observed["reported_commission_usd"] == "0.35"
    assert observed["matches_plan"]


def test_sdk_error_signatures_and_private_account_redaction():
    client = PaperClient("salt")
    client.accounts = ["DU123"]
    client.error(1, 10243, "DU123 fractional unavailable")
    client.error(2, 1234567890, 10248, "DU123 permission unavailable", "")
    assert [row["code"] for row in client.errors] == [10243, 10248]
    assert "DU123" not in json.dumps(client.errors)


def test_alphanumeric_paper_account_is_pinned_without_loosening_live_guard(monkeypatch):
    account = "DUABCDEFQ"  # Synthetic fixture, never an actual account ID.
    expected = hashlib.sha256(f"test-salt:{account}".encode()).hexdigest()
    client = PaperClient("test-salt", expected)
    client.accounts = [account]
    client.ready.set()
    client.managed.set()
    monkeypatch.setattr(client, "connect", lambda *args: None)
    monkeypatch.setattr(client, "run", lambda: None)
    client.start()
    assert client.account_hash == expected
    client.expected_hash = "changed-pin"
    with pytest.raises(ValueError, match="local pin"):
        client.start()


def test_sdk_archive_and_source_tampering_are_rejected(tmp_path, monkeypatch):
    from scripts import prepare_ibkr_paper_sdk as sdk
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not the pinned SDK")
    with pytest.raises(ValueError, match="checksum"):
        sdk.prepare(bad, tmp_path / "target")
    root = tmp_path / "sdk"
    root.mkdir()
    (root / "client.py").write_text("# test-only fixture\n")
    (root / "bundle-manifest.json").write_text(json.dumps(dict(sdk_version="1051.1", protobuf_version="5.29.5")))
    monkeypatch.setattr(sdk, "TREE_SHA256", sdk.tree_digest(root))
    sdk.verify_sdk(root)
    (root / "extra.py").write_text("# unexpected source\n")
    with pytest.raises(ValueError, match="content"):
        sdk.verify_sdk(root)


def test_calendar_handles_weekend_holiday_dst_and_early_close():
    assert execution_window(NOW)
    assert not execution_window(NOW.replace(day=25))  # Sunday
    assert not execution_window(datetime(2026, 11, 26, 15, tzinfo=timezone.utc))  # Thanksgiving
    assert execution_window(datetime(2026, 11, 2, 14, 35, tzinfo=timezone.utc))  # EST
    assert not execution_window(datetime(2026, 11, 27, 18, tzinfo=timezone.utc))  # 13:00 early close
