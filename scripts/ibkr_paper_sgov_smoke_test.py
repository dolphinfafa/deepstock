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
SAFE_LIMIT_PRICE = 1.00


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test Paper order submission and cancellation with exactly one "
            "deliberately non-marketable SGOV limit order."
        )
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--cancel-after", type=float, default=10.0)
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
        self.network_thread: threading.Thread | None = None
        self.order_id: int | None = None
        self.statuses: list[str] = []
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
            if errorCode in {502, 504, 1100, 1300}:
                self.ready.set()

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
        if status in {"PreSubmitted", "Submitted"}:
            self.submitted.set()
        if status in {"Cancelled", "ApiCancelled", "Inactive"}:
            self.cancelled.set()

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
        order.transmit = True

        order_id = self.order_id
        self.placeOrder(order_id, contract, order)
        if not self.submitted.wait(self.timeout):
            raise TimeoutError("Order was not acknowledged as submitted.")

        if not self.cancelled.wait(cancel_after):
            self.cancelOrder(order_id, "")
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

    def close(self) -> None:
        try:
            if self.isConnected():
                self.disconnect()
        finally:
            if self.network_thread is not None:
                self.network_thread.join(timeout=2)


def main() -> int:
    args = parse_args()
    if not args.submit or args.confirm != REQUIRED_CONFIRMATION:
        print(
            "Refusing to submit. Pass --submit --confirm " + REQUIRED_CONFIRMATION,
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.cancel_after <= 60:
        print("Configuration error: --cancel-after must be between 1 and 60 seconds.", file=sys.stderr)
        return 2

    try:
        config = Config.from_env(Path(args.env_file).resolve())
        test = PaperSgovSmokeTest(timeout=args.timeout)
        test.connect_and_start(config)
        result = test.run_test(args.cancel_after)
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
