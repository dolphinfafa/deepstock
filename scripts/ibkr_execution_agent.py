#!/usr/bin/env python3
"""Run the fail-closed Deepstock execution node beside TWS.

The agent always publishes account snapshots. It only claims and submits plans
when read-only mode is explicitly disabled and every local/server guard passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper


INFO_ERROR_CODES = {2104, 2106, 2107, 2108, 2158}
TERMINAL_STATUSES = {"FILLED", "CANCELLED", "APICANCELLED", "INACTIVE", "REJECTED", "ERROR"}
LIVE_CONFIRMATION = "I UNDERSTAND LIVE ORDERS ARE REAL"
ACCOUNT_GUARD_CONFIRMATION = "PIN-SOLE-TWS-ACCOUNT"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    normalized = value.lower().strip()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


class AccountDiscovery(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, wrapper=self)
        self.ready = threading.Event()
        self.accounts_ready = threading.Event()
        self.accounts: list[str] = []
        self.error_message = ""

    def nextValidId(self, orderId: int) -> None:  # type: ignore[override]
        self.ready.set()

    def managedAccounts(self, accountsList: str) -> None:  # type: ignore[override]
        self.accounts = [row for row in accountsList.split(",") if row]
        self.accounts_ready.set()

    def error(  # type: ignore[override]
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        if errorCode not in INFO_ERROR_CODES:
            self.error_message = f"IBKR {errorCode}: {errorString}"
            if errorCode in {502, 504, 1100, 1300}:
                self.ready.set()
                self.accounts_ready.set()


def bootstrap_account_guard(env_path: Path, timeout: float, confirmation: str) -> None:
    if confirmation != ACCOUNT_GUARD_CONFIRMATION:
        raise ValueError(
            f"account bootstrap requires --confirm {ACCOUNT_GUARD_CONFIRMATION}"
        )
    values = load_env(env_path)
    missing = [
        key
        for key in ("IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID")
        if not values.get(key)
    ]
    if missing:
        raise ValueError(f"missing TWS settings: {', '.join(missing)}")
    discovery = AccountDiscovery()
    discovery.connect(
        values["IBKR_HOST"], int(values["IBKR_PORT"]), int(values["IBKR_CLIENT_ID"])
    )
    thread = threading.Thread(target=discovery.run, daemon=True)
    thread.start()
    try:
        if not discovery.ready.wait(timeout) or not discovery.accounts_ready.wait(timeout):
            raise TimeoutError(discovery.error_message or "TWS account discovery timed out")
        if len(discovery.accounts) != 1:
            raise RuntimeError(
                "account bootstrap requires exactly one TWS managed account; "
                f"found {len(discovery.accounts)}"
            )
        account = discovery.accounts[0]
        salt = secrets.token_urlsafe(32)
        account_hash = hashlib.sha256(f"{salt}:{account}".encode("utf-8")).hexdigest()
        update_env(
            env_path,
            {
                "IBKR_ACCOUNT": account,
                "IBKR_ACCOUNT_HASH_SALT": salt,
                "IBKR_EXPECTED_ACCOUNT_HASH": account_hash,
            },
        )
    finally:
        if discovery.isConnected():
            discovery.disconnect()
        thread.join(timeout=2)


@dataclass(frozen=True)
class AgentConfig:
    mode: str
    host: str
    port: int
    client_id: int
    account: str
    account_hash_salt: str
    expected_account_hash: str
    read_only: bool
    live_execution_enabled: bool
    live_confirmation: str
    api_base_url: str
    node_token: str
    local_notional_cap_usd: float
    max_data_age_days: int
    state_path: Path

    @classmethod
    def from_env(cls, path: Path) -> "AgentConfig":
        values = load_env(path)
        required = (
            "IBKR_HOST",
            "IBKR_PORT",
            "IBKR_CLIENT_ID",
            "IBKR_ACCOUNT",
            "IBKR_ACCOUNT_HASH_SALT",
            "IBKR_EXPECTED_ACCOUNT_HASH",
            "DEEPSTOCK_API_BASE_URL",
            "DEEPSTOCK_NODE_TOKEN",
        )
        missing = [key for key in required if not values.get(key)]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        mode = values.get("IBKR_MODE", "paper").lower()
        if mode not in {"paper", "live"}:
            raise ValueError("IBKR_MODE must be paper or live")
        port = int(values["IBKR_PORT"])
        expected_port = 7497 if mode == "paper" else 7496
        if port != expected_port and not as_bool(values.get("IBKR_ALLOW_NONSTANDARD_PORT")):
            raise ValueError(
                f"{mode} mode expects TWS port {expected_port}; set "
                "IBKR_ALLOW_NONSTANDARD_PORT=true only after verifying the TWS profile"
            )
        project_root = Path(__file__).resolve().parents[1]
        state_value = values.get(
            "IBKR_AGENT_STATE_PATH", "artifacts/ibkr_agent/execution_state.json"
        )
        state_path = Path(state_value)
        if not state_path.is_absolute():
            state_path = project_root / state_path
        return cls(
            mode=mode,
            host=values["IBKR_HOST"],
            port=port,
            client_id=int(values["IBKR_CLIENT_ID"]),
            account=values["IBKR_ACCOUNT"],
            account_hash_salt=values["IBKR_ACCOUNT_HASH_SALT"],
            expected_account_hash=values["IBKR_EXPECTED_ACCOUNT_HASH"],
            read_only=as_bool(values.get("IBKR_READ_ONLY"), True),
            live_execution_enabled=as_bool(
                values.get("IBKR_LIVE_EXECUTION_ENABLED"), False
            ),
            live_confirmation=values.get("IBKR_LIVE_CONFIRMATION", ""),
            api_base_url=values["DEEPSTOCK_API_BASE_URL"].rstrip("/"),
            node_token=values["DEEPSTOCK_NODE_TOKEN"],
            local_notional_cap_usd=float(
                values.get("IBKR_LOCAL_NOTIONAL_CAP_USD", "1000")
            ),
            max_data_age_days=int(values.get("IBKR_MAX_DATA_AGE_DAYS", "3")),
            state_path=state_path.resolve(),
        )

    def account_hash(self) -> str:
        return hashlib.sha256(
            f"{self.account_hash_salt}:{self.account}".encode("utf-8")
        ).hexdigest()


class TradingSession(EWrapper, EClient):
    def __init__(self, timeout: float) -> None:
        EClient.__init__(self, wrapper=self)
        self.timeout = timeout
        self.ready = threading.Event()
        self.managed_ready = threading.Event()
        self.account_ready = threading.Event()
        self.positions_ready = threading.Event()
        self.open_orders_ready = threading.Event()
        self.completed_orders_ready = threading.Event()
        self.network_thread: threading.Thread | None = None
        self.next_order_id: int | None = None
        self.managed_accounts: list[str] = []
        self.account_values: dict[tuple[str, str], float] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.orders_by_ref: dict[str, dict[str, Any]] = {}
        self.order_ref_by_id: dict[int, str] = {}
        self.errors: list[str] = []
        self.order_errors: dict[int, str] = {}

    def nextValidId(self, orderId: int) -> None:  # type: ignore[override]
        self.next_order_id = orderId
        self.ready.set()

    def managedAccounts(self, accountsList: str) -> None:  # type: ignore[override]
        self.managed_accounts = [row for row in accountsList.split(",") if row]
        self.managed_ready.set()

    def error(  # type: ignore[override]
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        message = f"IBKR {errorCode} (req {reqId}): {errorString}"
        if advancedOrderRejectJson:
            message += f" | {advancedOrderRejectJson}"
        if errorCode in INFO_ERROR_CODES:
            return
        if reqId >= 0:
            self.order_errors[reqId] = message
            order_ref = self.order_ref_by_id.get(reqId)
            if order_ref:
                self.orders_by_ref[order_ref] = {
                    **self.orders_by_ref.get(order_ref, {}),
                    "broker_order_id": str(reqId),
                    "status": "ERROR",
                    "last_error": message,
                }
        self.errors.append(message)
        if errorCode in {502, 504, 1100, 1300}:
            self.ready.set()

    def accountSummary(  # type: ignore[override]
        self, reqId: int, account: str, tag: str, value: str, currency: str
    ) -> None:
        try:
            self.account_values[(tag, currency)] = float(value)
        except ValueError:
            pass

    def accountSummaryEnd(self, reqId: int) -> None:  # type: ignore[override]
        self.account_ready.set()

    def updatePortfolio(  # type: ignore[override]
        self,
        contract: Contract,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:
        if contract.secType not in {"STK", "ETF"}:
            return
        self.positions[contract.symbol] = {
            "symbol": contract.symbol,
            "quantity": float(position),
            "market_price": float(marketPrice),
            "market_value": float(marketValue),
            "average_cost": float(averageCost),
            "currency": contract.currency or "USD",
        }

    def accountDownloadEnd(self, accountName: str) -> None:  # type: ignore[override]
        self.positions_ready.set()

    def _record_order(
        self, order_id: int, contract: Contract, order: Order, status: str
    ) -> None:
        order_ref = str(order.orderRef or "")
        if not order_ref:
            return
        self.order_ref_by_id[order_id] = order_ref
        existing = self.orders_by_ref.get(order_ref, {})
        self.orders_by_ref[order_ref] = {
            **existing,
            "broker_order_id": str(order_id),
            "symbol": contract.symbol,
            "action": order.action,
            "quantity": float(order.totalQuantity),
            "order_type": order.orderType,
            "limit_price": float(order.lmtPrice) if order.orderType == "LMT" else None,
            "status": status or existing.get("status", "UNKNOWN"),
        }

    def openOrder(  # type: ignore[override]
        self, orderId: int, contract: Contract, order: Order, orderState: Any
    ) -> None:
        self._record_order(orderId, contract, order, str(orderState.status))

    def openOrderEnd(self) -> None:  # type: ignore[override]
        self.open_orders_ready.set()

    def completedOrder(  # type: ignore[override]
        self, contract: Contract, order: Order, orderState: Any
    ) -> None:
        self._record_order(int(order.orderId), contract, order, str(orderState.completedStatus))

    def completedOrdersEnd(self) -> None:  # type: ignore[override]
        self.completed_orders_ready.set()

    def orderStatus(  # type: ignore[override]
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        order_ref = self.order_ref_by_id.get(orderId)
        if not order_ref:
            return
        self.orders_by_ref[order_ref] = {
            **self.orders_by_ref.get(order_ref, {}),
            "broker_order_id": str(orderId),
            "status": status,
            "filled_quantity": float(filled),
            "average_fill_price": float(avgFillPrice) if avgFillPrice else None,
        }

    def connect_and_snapshot(self, config: AgentConfig) -> None:
        self.connect(config.host, config.port, config.client_id)
        self.network_thread = threading.Thread(target=self.run, daemon=True)
        self.network_thread.start()
        if not self.ready.wait(self.timeout) or self.next_order_id is None:
            raise TimeoutError(f"TWS connection was not ready: {self.errors[-1:]}")
        if not self.managed_ready.wait(self.timeout):
            raise TimeoutError("TWS did not return managed accounts")
        if config.account not in self.managed_accounts:
            raise RuntimeError("configured IBKR account is not managed by this TWS session")
        self.reqAccountSummary(
            7201, "All", "NetLiquidation,TotalCashValue,BuyingPower"
        )
        self.reqAccountUpdates(True, config.account)
        self.reqAllOpenOrders()
        self.reqCompletedOrders(True)
        if not self.account_ready.wait(self.timeout):
            raise TimeoutError("account summary timed out")
        if not self.positions_ready.wait(self.timeout):
            raise TimeoutError("portfolio snapshot timed out")
        if not self.open_orders_ready.wait(self.timeout):
            raise TimeoutError("open-order snapshot timed out")
        self.completed_orders_ready.wait(min(self.timeout, 5))

    def account_value(self, tag: str) -> float | None:
        for currency in ("USD", "BASE", ""):
            if (tag, currency) in self.account_values:
                return self.account_values[(tag, currency)]
        candidates = [value for (name, _), value in self.account_values.items() if name == tag]
        return candidates[0] if candidates else None

    def allocate_order_id(self) -> int:
        if self.next_order_id is None:
            raise RuntimeError("no valid TWS order ID")
        value = self.next_order_id
        self.next_order_id += 1
        return value

    def submit(self, row: dict[str, Any], account: str) -> None:
        contract = Contract()
        contract.symbol = row["symbol"]
        contract.secType = "STK"
        contract.exchange = row.get("exchange", "SMART")
        contract.currency = row.get("currency", "USD")

        order = Order()
        order.action = row["action"]
        order.totalQuantity = row["quantity"]
        order.orderType = "LMT"
        order.lmtPrice = row["limit_price"]
        order.tif = "DAY"
        order.outsideRth = False
        order.account = account
        order.orderRef = row["order_id"]
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.transmit = True

        order_id = self.allocate_order_id()
        self.order_ref_by_id[order_id] = row["order_id"]
        self.orders_by_ref[row["order_id"]] = {
            "broker_order_id": str(order_id),
            "symbol": row["symbol"],
            "action": row["action"],
            "quantity": float(row["quantity"]),
            "order_type": "LMT",
            "limit_price": float(row["limit_price"]),
            "status": "PENDING_ACK",
        }
        self.placeOrder(order_id, contract, order)

    def close(self, config: AgentConfig) -> None:
        try:
            if self.isConnected():
                self.reqAccountUpdates(False, config.account)
                self.cancelAccountSummary(7201)
                self.disconnect()
        finally:
            if self.network_thread:
                self.network_thread.join(timeout=2)


def validate_plan(
    plan: dict[str, Any],
    config: AgentConfig,
    control: dict[str, Any],
    account_hash: str,
    positions: dict[str, dict[str, Any]],
) -> None:
    if plan.get("mode") != config.mode or control.get("mode") != config.mode:
        raise ValueError("execution mode mismatch")
    if account_hash != config.expected_account_hash:
        raise ValueError("the connected account hash does not match the local allowlist")
    latest_hash = control.get("latest_account_hash")
    if latest_hash and latest_hash != account_hash:
        raise ValueError("the server's latest account hash differs from this TWS account")
    plan_date = date.fromisoformat(plan["data_date"])
    age = (date.today() - plan_date).days
    if age < 0 or age > config.max_data_age_days:
        raise ValueError(f"plan data is stale or future-dated: age={age}")
    server_cap = float(control.get("live_notional_cap_usd", 1000))
    effective_cap = min(server_cap, config.local_notional_cap_usd)
    if float(plan["total_notional_usd"]) > effective_cap:
        raise ValueError("plan exceeds the local/server notional cap")
    if config.mode == "live":
        if control.get("global_kill_switch", True):
            raise ValueError("server global kill switch is enabled")
        if not control.get("live_trading_enabled", False):
            raise ValueError("server live trading is disabled")
        if not control.get("wechat_test_passed", False):
            raise ValueError("server alert channel has not passed its test")
        if not config.live_execution_enabled:
            raise ValueError("local live execution is disabled")
        if config.live_confirmation != LIVE_CONFIRMATION:
            raise ValueError("local live confirmation is missing")

    remaining = {
        symbol: max(float(position.get("quantity", 0)), 0.0)
        for symbol, position in positions.items()
    }
    seen: set[str] = set()
    for order in plan.get("orders", []):
        order_ref = order.get("order_id", "")
        if not order_ref or order_ref in seen:
            raise ValueError("plan contains a missing or duplicate deterministic order ID")
        seen.add(order_ref)
        if order.get("order_type") != "LMT" or float(order.get("limit_price", 0)) <= 0:
            raise ValueError("only positive-price limit orders are allowed")
        if order.get("currency", "USD") != "USD" or order.get("exchange", "SMART") != "SMART":
            raise ValueError("only USD SMART stock/ETF orders are allowed")
        action = order.get("action")
        quantity = float(order.get("quantity", 0))
        if action not in {"BUY", "SELL"} or quantity <= 0:
            raise ValueError("invalid order direction or quantity")
        symbol = order["symbol"]
        if action == "SELL":
            if quantity > remaining.get(symbol, 0):
                raise ValueError(f"SELL would create a short position in {symbol}")
            remaining[symbol] -= quantity
        else:
            remaining[symbol] = remaining.get(symbol, 0) + quantity


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"orders": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {"orders": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


class ControlPlane:
    def __init__(self, config: AgentConfig) -> None:
        self.client = httpx.Client(
            base_url=config.api_base_url,
            headers={"Authorization": f"Bearer {config.node_token}"},
            timeout=20,
        )

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()


def report_order(api: ControlPlane, plan: dict[str, Any], row: dict[str, Any]) -> None:
    planned = next(order for order in plan["orders"] if order["order_id"] == row["order_id"])
    api.post(
        "/agent/orders",
        {
            "order_id": row["order_id"],
            "plan_id": plan["id"],
            "broker_order_id": row.get("broker_order_id"),
            "symbol": planned["symbol"],
            "action": planned["action"],
            "quantity": planned["quantity"],
            "order_type": planned["order_type"],
            "limit_price": planned["limit_price"],
            "status": row.get("status", "UNKNOWN"),
            "filled_quantity": row.get("filled_quantity", 0),
            "average_fill_price": row.get("average_fill_price"),
            "last_error": row.get("last_error"),
        },
    )


def run_cycle(config: AgentConfig, timeout: float) -> dict[str, Any]:
    account_hash = config.account_hash()
    session = TradingSession(timeout)
    api = ControlPlane(config)
    state = load_state(config.state_path)
    state.setdefault("orders", {})
    try:
        session.connect_and_snapshot(config)
        api.post(
            "/agent/snapshot",
            {
                "mode": config.mode,
                "account_hash": account_hash,
                "net_liquidation": session.account_value("NetLiquidation"),
                "cash": session.account_value("TotalCashValue"),
                "buying_power": session.account_value("BuyingPower"),
                "currency": "USD",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source_node": "DESKTOP-ORNLESD",
                "positions": list(session.positions.values()),
            },
        )
        control = api.get("/agent/control", mode=config.mode)
        plans = api.get("/agent/plans", mode=config.mode)
        result = {"mode": config.mode, "read_only": config.read_only, "plans": []}
        for plan in plans:
            try:
                validate_plan(plan, config, control, account_hash, session.positions)
            except Exception as error:
                result["plans"].append(
                    {"id": plan.get("id"), "status": "rejected_locally", "reason": str(error)}
                )
                continue

            existing_refs = set(session.orders_by_ref) | set(state["orders"])
            for order in plan["orders"]:
                ref = order["order_id"]
                observed = session.orders_by_ref.get(ref) or state["orders"].get(ref)
                if observed:
                    report_order(api, plan, {"order_id": ref, **observed})
            missing = [row for row in plan["orders"] if row["order_id"] not in existing_refs]
            if not missing:
                result["plans"].append({"id": plan["id"], "status": "reconciled"})
                continue
            if config.read_only:
                result["plans"].append(
                    {"id": plan["id"], "status": "observed_read_only", "orders": len(missing)}
                )
                continue
            if plan["status"] == "ready":
                api.post(f"/agent/plans/{plan['id']}/claim")
            for order in missing:
                session.submit(order, config.account)
            time.sleep(4)
            for order in missing:
                ref = order["order_id"]
                observed = session.orders_by_ref[ref]
                state["orders"][ref] = {
                    **observed,
                    "plan_id": plan["id"],
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                report_order(api, plan, {"order_id": ref, **observed})
            result["plans"].append(
                {"id": plan["id"], "status": "submitted", "orders": len(missing)}
            )
        save_state(config.state_path, state)
        return result
    finally:
        session.close(config)
        api.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--bootstrap-account-guard", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        env_path = args.env_file.resolve()
        if args.bootstrap_account_guard:
            bootstrap_account_guard(env_path, args.timeout, args.confirm)
            print("IBKR account guard configured (account identifier not displayed)")
            return 0
        config = AgentConfig.from_env(env_path)
        while True:
            try:
                result = run_cycle(config, args.timeout)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            except Exception as error:
                print(f"execution agent cycle failed: {error}", file=sys.stderr, flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(max(args.poll_seconds, 10))
    except Exception as error:
        print(f"execution agent configuration failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
