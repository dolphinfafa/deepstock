#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.wrapper import EWrapper

ACCOUNT_SUMMARY_REQ_ID = 9001
ACCOUNT_SUMMARY_TAGS = ",".join(
    [
        "AccountType",
        "NetLiquidation",
        "TotalCashValue",
        "BuyingPower",
        "AvailableFunds",
        "ExcessLiquidity",
        "MaintMarginReq",
    ]
)
INFO_ERROR_CODES = {2104, 2106, 2107, 2108, 2158}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a read-only IBKR connectivity probe against TWS or IB Gateway. "
            "The probe requests server time, account summary, positions, and open "
            "orders without exposing any order-placement capability."
        )
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the local env file. Defaults to ./.env.",
    )
    parser.add_argument("--host", help="Override the IBKR host from .env.")
    parser.add_argument(
        "--port",
        type=int,
        help="Override the IBKR API port from .env.",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        help="Override the IBKR client id from .env.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for each IBKR response set. Defaults to 10.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the probe result as JSON.",
    )
    return parser.parse_args()


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def first_present(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return None


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value!r}")


@dataclass
class ProbeConfig:
    mode: str
    host: str
    port: int
    client_id: int
    read_only: bool
    env_file: Path

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ProbeConfig":
        env_file = Path(args.env_file).expanduser().resolve()
        env_values = load_env_file(env_file)

        mode = args.host and env_values.get("IBKR_MODE", "paper") or env_values.get(
            "IBKR_MODE", "paper"
        )
        host = args.host or first_present(env_values, "IBKR_HOST")
        port_value = (
            str(args.port)
            if args.port is not None
            else first_present(env_values, "IBKR_PORT")
        )
        client_id_value = (
            str(args.client_id)
            if args.client_id is not None
            else first_present(env_values, "IBKR_CLIENT_ID", "IBKR_*CLIENT*_ID")
        )
        read_only_value = first_present(
            env_values,
            "IBKR_READ_ONLY",
            "IBKR_*READ*_ONLY",
        )

        if not host:
            raise ValueError("Missing IBKR host. Set IBKR_HOST in .env or pass --host.")
        if not port_value:
            raise ValueError("Missing IBKR port. Set IBKR_PORT in .env or pass --port.")
        if not client_id_value:
            raise ValueError(
                "Missing IBKR client id. Set IBKR_CLIENT_ID in .env or pass --client-id."
            )
        if read_only_value is None:
            raise ValueError(
                "Missing IBKR read-only guard. Set IBKR_READ_ONLY=true in .env."
            )

        return cls(
            mode=mode,
            host=host,
            port=int(port_value),
            client_id=int(client_id_value),
            read_only=parse_bool(read_only_value),
            env_file=env_file,
        )


@dataclass
class AccountValue:
    account: str
    tag: str
    value: str
    currency: str


@dataclass
class PositionValue:
    account: str
    symbol: str
    sec_type: str
    currency: str
    exchange: str
    quantity: float
    avg_cost: float


@dataclass
class OpenOrderValue:
    order_id: int
    account: str
    symbol: str
    sec_type: str
    currency: str
    exchange: str
    action: str
    order_type: str
    total_quantity: float
    limit_price: float
    aux_price: float
    tif: str
    status: str


class ReadOnlyIbkrProbe(EWrapper, EClient):
    def __init__(self, timeout: float) -> None:
        EClient.__init__(self, wrapper=self)
        self.timeout = timeout
        self.ready_event = threading.Event()
        self.current_time_event = threading.Event()
        self.account_summary_event = threading.Event()
        self.positions_event = threading.Event()
        self.open_orders_event = threading.Event()
        self.network_thread: threading.Thread | None = None
        self.server_time: str | None = None
        self.account_summary: list[AccountValue] = []
        self.positions: list[PositionValue] = []
        self.open_orders: list[OpenOrderValue] = []
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.connection_ready = False

    def placeOrder(self, orderId: int, contract: Contract, order: Order) -> None:  # type: ignore[override]
        raise RuntimeError("Order placement is disabled in this read-only probe.")

    def error(  # type: ignore[override]
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        message = f"IBKR {errorCode} (req {reqId}): {errorString}"
        if advancedOrderRejectJson:
            message = f"{message} | {advancedOrderRejectJson}"

        if errorCode in INFO_ERROR_CODES:
            self.messages.append(message)
            return

        self.errors.append(message)
        if errorCode in {502, 504, 1100, 1300}:
            self.ready_event.set()
            self.current_time_event.set()
            self.account_summary_event.set()
            self.positions_event.set()
            self.open_orders_event.set()

    def nextValidId(self, orderId: int) -> None:  # type: ignore[override]
        self.connection_ready = True
        self.ready_event.set()

    def currentTime(self, time_: int) -> None:  # type: ignore[override]
        self.server_time = datetime.fromtimestamp(time_, tz=timezone.utc).isoformat()
        self.current_time_event.set()

    def accountSummary(  # type: ignore[override]
        self,
        reqId: int,
        account: str,
        tag: str,
        value: str,
        currency: str,
    ) -> None:
        if reqId != ACCOUNT_SUMMARY_REQ_ID:
            return
        self.account_summary.append(
            AccountValue(account=account, tag=tag, value=value, currency=currency)
        )

    def accountSummaryEnd(self, reqId: int) -> None:  # type: ignore[override]
        if reqId == ACCOUNT_SUMMARY_REQ_ID:
            self.account_summary_event.set()

    def position(  # type: ignore[override]
        self,
        account: str,
        contract: Contract,
        position: float,
        avgCost: float,
    ) -> None:
        self.positions.append(
            PositionValue(
                account=account,
                symbol=contract.symbol,
                sec_type=contract.secType,
                currency=contract.currency,
                exchange=contract.exchange,
                quantity=position,
                avg_cost=avgCost,
            )
        )

    def positionEnd(self) -> None:  # type: ignore[override]
        self.positions_event.set()

    def openOrder(  # type: ignore[override]
        self,
        orderId: int,
        contract: Contract,
        order: Order,
        orderState: OrderState,
    ) -> None:
        self.open_orders.append(
            OpenOrderValue(
                order_id=orderId,
                account=order.account,
                symbol=contract.symbol,
                sec_type=contract.secType,
                currency=contract.currency,
                exchange=contract.exchange,
                action=order.action,
                order_type=order.orderType,
                total_quantity=float(order.totalQuantity),
                limit_price=float(order.lmtPrice),
                aux_price=float(order.auxPrice),
                tif=order.tif,
                status=orderState.status,
            )
        )

    def openOrderEnd(self) -> None:  # type: ignore[override]
        self.open_orders_event.set()

    def connect_and_start(self, host: str, port: int, client_id: int) -> None:
        self.connect(host, port, client_id)
        self.network_thread = threading.Thread(target=self.run, daemon=True)
        self.network_thread.start()

    def wait_for(self, event: threading.Event, label: str) -> None:
        if not event.wait(self.timeout):
            raise TimeoutError(f"Timed out waiting for {label}.")

    def request_read_only_snapshot(self) -> dict[str, Any]:
        self.wait_for(self.ready_event, "IBKR connection readiness")
        if self.errors:
            raise RuntimeError(self.errors[0])
        if not self.connection_ready:
            raise RuntimeError("IBKR connection never became ready.")

        self.reqCurrentTime()
        self.reqAccountSummary(ACCOUNT_SUMMARY_REQ_ID, "All", ACCOUNT_SUMMARY_TAGS)
        self.reqPositions()
        self.reqAllOpenOrders()

        self.wait_for(self.current_time_event, "server time")
        self.wait_for(self.account_summary_event, "account summary")
        self.wait_for(self.positions_event, "positions")
        self.wait_for(self.open_orders_event, "open orders")

        self.cancelAccountSummary(ACCOUNT_SUMMARY_REQ_ID)
        self.cancelPositions()

        # Keep account identifiers, balances, quantities, and order details out
        # of console/JSON output. The probe only needs connectivity health.
        return {
            "server_time_utc": self.server_time,
            "account_summary_rows": len(self.account_summary),
            "positions_count": len(self.positions),
            "open_orders_count": len(self.open_orders),
            "messages": self.messages,
            "errors": self.errors,
        }

    def close(self) -> None:
        try:
            if self.isConnected():
                self.disconnect()
        finally:
            if self.network_thread is not None:
                self.network_thread.join(timeout=2)


def render_text(result: dict[str, Any], config: ProbeConfig) -> str:
    lines = [
        "IBKR read-only probe completed.",
        f"Mode: {config.mode}",
        f"Host: {config.host}:{config.port}",
        f"Client ID: {config.client_id}",
        f"Server time (UTC): {result['server_time_utc']}",
        f"Account summary rows: {result['account_summary_rows']}",
        f"Positions: {result['positions_count']}",
        f"Open orders: {result['open_orders_count']}",
    ]
    if result["messages"]:
        lines.append("Messages:")
        lines.extend(f"  - {message}" for message in result["messages"])
    if result["errors"]:
        lines.append("Errors:")
        lines.extend(f"  - {message}" for message in result["errors"])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        config = ProbeConfig.from_args(args)
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if config.mode.lower() != "paper":
        print(
            "Configuration error: IBKR_MODE must remain 'paper' for this probe.",
            file=sys.stderr,
        )
        return 2
    if not config.read_only:
        print(
            "Configuration error: IBKR_READ_ONLY must remain true for this probe.",
            file=sys.stderr,
        )
        return 2

    probe = ReadOnlyIbkrProbe(timeout=args.timeout)
    try:
        probe.connect_and_start(config.host, config.port, config.client_id)
        result = probe.request_read_only_snapshot()
    except Exception as exc:  # noqa: BLE001
        print(f"Probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        probe.close()

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_text(result, config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
