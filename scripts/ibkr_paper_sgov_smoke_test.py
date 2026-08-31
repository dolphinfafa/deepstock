#!/usr/bin/env python3
"""Submit then cancel one deliberately non-marketable SGOV Paper order."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

INFO_ERROR_CODES = {2104, 2106, 2107, 2108, 2158}
EXPECTED_CANCEL_ERROR = 202
REQUIRED_CONFIRMATION = "PAPER-SGOV-SMOKE-TEST"
REQUIRED_CANCEL_CONFIRMATION = "PAPER-SGOV-SMOKE-CANCEL"
REQUIRED_FILL_CONFIRMATION = "PAPER-SGOV-FILL-TEST"
SAFE_LIMIT_PRICE = 1.00
FILL_TEST_MAX_LIMIT_PRICE = 150.00


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test Paper order submission and cancellation with exactly one "
            "deliberately non-marketable SGOV limit order."
        )
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--cancel-pending", action="store_true")
    parser.add_argument("--submit-fill-test", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--cancel-after", type=float, default=10.0)
    parser.add_argument("--fill-timeout", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    client_id: int

    @classmethod
    def from_env(cls, path: Path) -> "Config":
        values = load_env(path)
        if values.get("IBKR_MODE", "").lower() != "paper":
            raise ValueError("IBKR_MODE must be 'paper'.")
        if parse_bool(values.get("IBKR_READ_ONLY", "true")):
            raise ValueError("IBKR_READ_ONLY must be false for this Paper-only test.")
        missing = [key for key in ("IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID") if not values.get(key)]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        return cls(
            host=values["IBKR_HOST"],
            port=int(values["IBKR_PORT"]),
            client_id=int(values["IBKR_CLIENT_ID"]),
        )


class PaperSgovSmokeTest(EWrapper, EClient):
    def __init__(self, timeout: float) -> None:
        EClient.__init__(self, wrapper=self)
        self.timeout = timeout
        self.ready = threading.Event()
        self.submitted = threading.Event()
        self.cancelled = threading.Event()
        self.filled = threading.Event()
        self.open_orders_complete = threading.Event()
        self.network_thread: threading.Thread | None = None
        self.order_id: int | None = None
        self.statuses: list[str] = []
        self.matching_order_ids: list[int] = []
        self.messages: list[str] = []
        self.errors: list[str] = []

    def nextValidId(self, orderId: int) -> None:  # type: ignore[override]
        self.order_id = orderId
        self.ready.set()

    def error(  # type: ignore[override]
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        message = f"IBKR {errorCode} (req {reqId}): {errorString}"
        if errorCode in INFO_ERROR_CODES:
            self.messages.append(message)
        elif errorCode == EXPECTED_CANCEL_ERROR:
            self.messages.append(message)
            self.cancelled.set()
        else:
            self.errors.append(message)
            if reqId == self.order_id:
                self.submitted.set()
            if errorCode in {502, 504, 1100, 1300}:
                self.ready.set()

    def openOrder(  # type: ignore[override]
        self,
        orderId: int,
        contract: Contract,
        order: Order,
        orderState: Any,
    ) -> None:
        if orderId == self.order_id:
            self.statuses.append(f"openOrder:{orderState.status}")
            self.submitted.set()
        if (
            contract.symbol == "SGOV"
            and contract.secType == "STK"
            and order.action == "BUY"
            and float(order.totalQuantity) == 1
            and order.orderType == "LMT"
            and abs(float(order.lmtPrice) - SAFE_LIMIT_PRICE) < 0.0001
            and order.tif == "DAY"
        ):
            self.matching_order_ids.append(orderId)

    def openOrderEnd(self) -> None:  # type: ignore[override]
        self.open_orders_complete.set()

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
        if orderId != self.order_id:
            return
        self.statuses.append(status)
        if status in {"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted"}:
            self.submitted.set()
        if status in {"Cancelled", "ApiCancelled", "Inactive"}:
            self.cancelled.set()
        if status == "Filled":
            self.filled.set()

    def connect_and_start(self, config: Config) -> None:
        self.connect(config.host, config.port, config.client_id)
        self.network_thread = threading.Thread(target=self.run, daemon=True)
        self.network_thread.start()

    def run_test(self, cancel_after: float) -> dict[str, Any]:
        if not self.ready.wait(self.timeout) or self.order_id is None:
            raise TimeoutError("Timed out waiting for a valid API order ID.")
        if self.errors:
            raise RuntimeError(self.errors[0])

        contract = Contract()
        contract.symbol = "SGOV"
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.primaryExchange = "ARCA"
        contract.currency = "USD"

        order = Order()
        order.action = "BUY"
        order.totalQuantity = 1
        order.orderType = "LMT"
        order.lmtPrice = SAFE_LIMIT_PRICE
        order.tif = "DAY"
        order.outsideRth = False
        # Current TWS rejects the legacy defaults when they are serialized.
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.transmit = True

        order_id = self.order_id
        self.placeOrder(order_id, contract, order)
        if not self.submitted.wait(self.timeout):
            raise TimeoutError(
                "Order was not acknowledged as submitted. "
                f"statuses={self.statuses!r}, errors={self.errors!r}"
            )
        if self.errors:
            raise RuntimeError(self.errors[0])

        if not self.cancelled.wait(cancel_after):
            self.cancelOrder(order_id)
            if not self.cancelled.wait(self.timeout):
                raise TimeoutError("Timed out waiting for cancellation confirmation.")

        if "Filled" in self.statuses:
            raise RuntimeError("Unsafe outcome: the deliberately non-marketable order filled.")

        return {
            "symbol": "SGOV",
            "action": "BUY",
            "quantity": 1,
            "limit_price": SAFE_LIMIT_PRICE,
            "order_id": order_id,
            "statuses": self.statuses,
            "messages": self.messages,
            "errors": self.errors,
        }

    def cancel_matching_order(self) -> dict[str, Any]:
        if not self.ready.wait(self.timeout):
            raise TimeoutError("Timed out waiting for API connection readiness.")
        if self.errors:
            raise RuntimeError(self.errors[0])

        self.reqOpenOrders()
        if not self.open_orders_complete.wait(self.timeout):
            raise TimeoutError("Timed out listing this API client's open orders.")
        if len(self.matching_order_ids) != 1:
            raise RuntimeError(
                "Refusing cancellation: expected exactly one matching SGOV smoke-test "
                f"order, found {len(self.matching_order_ids)}."
            )

        order_id = self.matching_order_ids[0]
        self.order_id = order_id
        self.cancelOrder(order_id)
        if not self.cancelled.wait(self.timeout):
            raise TimeoutError("Timed out waiting for cancellation confirmation.")
        return {
            "symbol": "SGOV",
            "action": "BUY",
            "quantity": 1,
            "limit_price": SAFE_LIMIT_PRICE,
            "cancelled_order_id": order_id,
            "statuses": self.statuses,
            "messages": self.messages,
            "errors": self.errors,
        }

    def run_fill_test(self, fill_timeout: float) -> dict[str, Any]:
        if not self.ready.wait(self.timeout) or self.order_id is None:
            raise TimeoutError("Timed out waiting for a valid API order ID.")
        if self.errors:
            raise RuntimeError(self.errors[0])

        contract = Contract()
        contract.symbol = "SGOV"
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.primaryExchange = "ARCA"
        contract.currency = "USD"

        order = Order()
        order.action = "BUY"
        order.totalQuantity = 1
        order.orderType = "LMT"
        order.lmtPrice = FILL_TEST_MAX_LIMIT_PRICE
        order.tif = "DAY"
        order.outsideRth = False
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.transmit = True

        order_id = self.order_id
        self.placeOrder(order_id, contract, order)
        if not self.submitted.wait(self.timeout):
            raise TimeoutError(
                "Order was not acknowledged as submitted. "
                f"statuses={self.statuses!r}, errors={self.errors!r}"
            )
        if self.errors:
            raise RuntimeError(self.errors[0])
        if not self.filled.wait(fill_timeout):
            self.cancelOrder(order_id)
            if not self.cancelled.wait(self.timeout):
                raise TimeoutError("Fill test did not fill and cancellation was unconfirmed.")
            raise TimeoutError("Fill test did not fill before its automatic cancellation.")

        return {
            "symbol": "SGOV",
            "action": "BUY",
            "quantity": 1,
            "max_limit_price": FILL_TEST_MAX_LIMIT_PRICE,
            "order_id": order_id,
            "statuses": self.statuses,
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


def main() -> int:
    args = parse_args()
    requested_actions = int(args.submit) + int(args.cancel_pending) + int(args.submit_fill_test)
    if requested_actions != 1:
        print(
            "Choose exactly one of --submit, --cancel-pending, or --submit-fill-test.",
            file=sys.stderr,
        )
        return 2
    expected_confirmation = {
        "submit": REQUIRED_CONFIRMATION,
        "cancel": REQUIRED_CANCEL_CONFIRMATION,
        "fill": REQUIRED_FILL_CONFIRMATION,
    }["submit" if args.submit else "cancel" if args.cancel_pending else "fill"]
    if args.confirm != expected_confirmation:
        print(
            "Refusing action. Pass the required --confirm value for the selected action.",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.cancel_after <= 60:
        print("Configuration error: --cancel-after must be between 1 and 60 seconds.", file=sys.stderr)
        return 2
    if not 15 <= args.fill_timeout <= 300:
        print("Configuration error: --fill-timeout must be between 15 and 300 seconds.", file=sys.stderr)
        return 2

    try:
        config = Config.from_env(Path(args.env_file).resolve())
        test = PaperSgovSmokeTest(timeout=args.timeout)
        test.connect_and_start(config)
        result = (
            test.run_test(args.cancel_after)
            if args.submit
            else test.cancel_matching_order()
            if args.cancel_pending
            else test.run_fill_test(args.fill_timeout)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Paper smoke test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if "test" in locals():
            test.close()

    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
