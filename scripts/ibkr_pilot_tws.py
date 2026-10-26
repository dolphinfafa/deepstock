"""Localhost TWS adapter for the fixed paper pilot; no live connection path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import re
import threading
from typing import Any

import exchange_calendars as calendars
import pandas as pd
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import ExecutionFilter
from ibapi.order import Order
from ibapi.wrapper import EWrapper

from deepstock.ibkr_pilot import LEGS, number, reference, validate_rows, validate_snapshot

INFO_CODES = {2104, 2106, 2107, 2108, 2158}


def execution_window(now: datetime) -> bool:
    calendar = calendars.get_calendar("XNYS")
    day = pd.Timestamp(now.date())
    return bool(calendar.is_session(day) and
                calendar.session_open(day) + timedelta(minutes=5) <= now <
                calendar.session_close(day) - timedelta(minutes=10))


def contract_for(row: dict) -> Contract:
    contract = Contract()
    contract.conId = row["conid"]
    contract.symbol = row["symbol"]
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.primaryExchange = row["primary_exchange"]
    contract.currency = "USD"
    return contract


def order_for(row: dict, account: str, *, preview: bool) -> Order:
    order = Order()
    order.account = account
    order.action = "BUY"
    order.totalQuantity = Decimal(row["quantity"])
    order.orderType = "LMT"
    order.lmtPrice = float(row["limit_price"])
    order.tif = "DAY"
    order.outsideRth = False
    order.eTradeOnly = False
    order.firmQuoteOnly = False
    order.orderRef = ("preview-" if preview else "") + row["order_ref"]
    order.whatIf = preview
    order.transmit = True
    return order


class PaperClient(EWrapper, EClient):
    def __init__(self, salt: str, expected_hash: str = "", *, enabled: bool = False,
                 timeout: float = 8, plan: list[dict] | None = None, enable_check=None):
        EClient.__init__(self, self)
        self.salt, self.expected_hash = salt, expected_hash
        self.enabled, self.timeout = enabled, timeout
        self.enable_check = enable_check
        self.plan = {row["order_ref"]: row for row in (plan or [])}
        self.ready, self.managed = threading.Event(), threading.Event()
        self.events = {name: threading.Event() for name in ("cash", "open", "completed", "executions", "positions")}
        self.accounts: list[str] = []
        self.account = ""
        self.account_hash = ""
        self.next_id: int | None = None
        self.cash: dict[str, str] = {}
        self.quotes: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.positions = {symbol: "0" for symbol, *_ in LEGS}
        self.execution_refs: dict[str, str] = {}
        self.commissions: dict[str, str] = {}
        self.other_open_orders: set[int] = set()
        self.previews: dict[int, dict] = {}
        self.errors: list[dict] = []
        self.unsafe_connection = False
        self.thread: threading.Thread | None = None
        self._mutex = threading.RLock()

    def nextValidId(self, orderId):
        with self._mutex:
            self.next_id = max(self.next_id or 0, orderId)
        self.ready.set()

    def managedAccounts(self, accountsList):
        self.accounts = [item for item in accountsList.split(",") if item]
        if self.account and self.accounts != [self.account]:
            self.unsafe_connection = True
        self.managed.set()

    def error(self, reqId, *args):
        # SDK 10.51 adds errorTime ahead of errorCode; 9.81 omits it.
        if len(args) >= 3 and isinstance(args[1], int):
            _, errorCode, errorString, *_ = args
        else:
            errorCode, errorString, *_ = args
        if errorCode in INFO_CODES:
            return
        message = errorString
        for account in self.accounts:
            message = message.replace(account, "[redacted]")
        with self._mutex:
            self.errors.append(dict(request=reqId, code=errorCode, message=message))
            if reqId in self.previews:
                self.previews[reqId]["error"] = f"IBKR {errorCode}: {message}"
                self.previews[reqId]["done"].set()
        if errorCode in {502, 504, 1100, 1101, 1102, 1300, 2103, 2105}:
            self.unsafe_connection = True

    def connectionClosed(self):
        self.unsafe_connection = True

    def accountSummary(self, reqId, account, tag, value, currency):
        if account == self.account and currency == "USD":
            with self._mutex:
                self.cash[tag] = value

    def accountSummaryEnd(self, reqId):
        self.events["cash"].set()

    def openOrderEnd(self):
        self.events["open"].set()

    def completedOrdersEnd(self):
        self.events["completed"].set()

    def execDetailsEnd(self, reqId):
        self.events["executions"].set()

    def position(self, account, contract, pos, avgCost):
        if account == self.account:
            for symbol, conid, _, _ in LEGS:
                if contract.conId == conid and contract.symbol == symbol:
                    with self._mutex:
                        self.positions[symbol] = str(pos)

    def positionEnd(self):
        self.events["positions"].set()

    def commissionReport(self, report):
        with self._mutex:
            if report.currency == "USD" and 0 <= number(report.commission) < 100:
                self.commissions[report.execId] = str(report.commission)

    def commissionAndFeesReport(self, report):
        with self._mutex:
            if report.currency == "USD" and 0 <= number(report.commissionAndFees) < 100:
                self.commissions[report.execId] = str(report.commissionAndFees)

    def marketDataType(self, reqId, marketDataType):
        if 100 <= reqId < 102:
            with self._mutex:
                self.quotes.setdefault(LEGS[reqId - 100][0], {})["market_data_type"] = marketDataType

    def tickPrice(self, reqId, tickType, price, attrib):
        if 100 <= reqId < 102 and tickType in {1, 2} and price > 0:
            with self._mutex:
                quote = self.quotes.setdefault(LEGS[reqId - 100][0], {})
                field = "bid" if tickType == 1 else "ask"
                quote[field] = str(price)
                quote[field + "_at"] = datetime.now(timezone.utc).isoformat()
                if "bid_at" in quote and "ask_at" in quote:
                    quote["observed_at"] = min(quote["bid_at"], quote["ask_at"])

    def _observe(self, orderId, contract, order, state):
        ref = order.orderRef
        if ref not in {reference(symbol) for symbol, *_ in LEGS}:
            return
        if order.account != self.account:
            self.unsafe_connection = True
            return
        row = self.plan.get(ref)
        matches = bool(row and contract.conId == row["conid"] and
                       contract.symbol == row["symbol"] and contract.currency == "USD" and
                       contract.secType == "STK" and order.action == "BUY" and
                       number(order.totalQuantity) == number(row["quantity"]) and
                       order.orderType == "LMT" and
                       number(order.lmtPrice) == number(row["limit_price"]) and
                       order.tif == "DAY" and not order.outsideRth)
        with self._mutex:
            prior = self.orders.get(ref, {})
            if prior.get("perm_id") and order.permId and prior["perm_id"] != order.permId:
                self.unsafe_connection = True
            reported = number(getattr(order, "filledQuantity", 0))
            if not 0 <= reported <= number(order.totalQuantity):
                reported = Decimal("0")  # The SDK's UNSET_DOUBLE is not a fill.
            filled = max(number(prior.get("filled", "0")), reported)
            self.orders[ref] = dict(status=state.status.upper(), filled=str(filled),
                                    broker_order_id=orderId, perm_id=order.permId,
                                    matches_plan=matches)

    def openOrder(self, orderId, contract, order, orderState):
        with self._mutex:
            if orderId in self.previews:
                preview = self.previews[orderId]
                preview.update(status=orderState.status, warning=orderState.warningText)
                commission = number(getattr(orderState, "commissionAndFees",
                                             getattr(orderState, "commission", "NaN")))
                preview["estimated_commission_usd"] = str(commission) if 0 <= commission < 100 else None
                preview["done"].set()
                return
        if (contract.conId in {leg[1] for leg in LEGS} and
                order.orderRef not in {reference(symbol) for symbol, *_ in LEGS}):
            self.other_open_orders.add(orderId)
        self._observe(orderId, contract, order, orderState)

    def completedOrder(self, contract, order, orderState):
        self._observe(order.orderId, contract, order, orderState)

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        with self._mutex:
            for item in self.orders.values():
                if item["broker_order_id"] == orderId:
                    if number(filled) >= number(item["filled"]):
                        item.update(status=status.upper(), filled=str(filled),
                                    average_fill_price=str(avgFillPrice))

    def execDetails(self, reqId, contract, execution):
        ref = execution.orderRef
        if ref not in {reference(symbol) for symbol, *_ in LEGS}:
            return
        row = self.plan.get(ref)
        matches = bool(row and execution.acctNumber == self.account and
                       contract.conId == row["conid"] and contract.symbol == row["symbol"] and
                       contract.currency == "USD" and execution.side == "BOT" and
                       number(execution.price) <= number(row["limit_price"]))
        with self._mutex:
            self.execution_refs[execution.execId] = ref
            prior = self.orders.get(ref, {})
            if prior.get("perm_id") and execution.permId != prior["perm_id"]:
                self.unsafe_connection = True
            if number(execution.cumQty) >= number(prior.get("filled", "0")):
                self.orders[ref] = dict(status="PARTIAL", filled=str(execution.cumQty),
                    broker_order_id=execution.orderId, perm_id=execution.permId,
                    average_fill_price=str(execution.avgPrice), matches_plan=matches)

    def start(self, client_id: int = 9365):
        self.connect("127.0.0.1", 7497, client_id)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(self.timeout) or not self.managed.wait(self.timeout):
            raise TimeoutError("paper TWS handshake timed out")
        if len(self.accounts) != 1 or not re.fullmatch(r"DU\d+", self.accounts[0]):
            raise ValueError("localhost:7497 did not identify a single DU paper account")
        self.account = self.accounts[0]
        self.account_hash = hashlib.sha256(f"{self.salt}:{self.account}".encode()).hexdigest()
        if self.expected_hash and self.account_hash != self.expected_hash:
            raise ValueError("paper account does not match local pin")

    def collect(self, *, quotes: bool = False):
        with self._mutex:
            self.cash.clear()
        for event in self.events.values():
            event.clear()
        self.reqAccountSummary(10, "All", "SettledCash,AvailableFunds,$LEDGER:USD")
        self.reqAllOpenOrders()
        self.reqCompletedOrders(False)
        filter_ = ExecutionFilter()
        filter_.acctCode = self.account
        self.reqExecutions(11, filter_)
        self.reqPositions()
        if quotes:
            self.reqMarketDataType(1)
            for index, (symbol, conid, exchange, _) in enumerate(LEGS):
                self.reqMktData(100 + index, contract_for(dict(symbol=symbol, conid=conid,
                    primary_exchange=exchange)), "", False, False, [])
        for name, event in self.events.items():
            if not event.wait(self.timeout):
                raise TimeoutError(f"paper {name} snapshot incomplete")
        self.cancelAccountSummary(10)
        self.cancelPositions()
        if quotes:
            # Wait on an event with a bounded timeout, without blocking the API thread.
            import time
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                with self._mutex:
                    if all("observed_at" in self.quotes.get(symbol, {}) for symbol, *_ in LEGS):
                        break
                threading.Event().wait(0.1)

    def snapshot(self):
        with self._mutex:
            import copy
            orders = copy.deepcopy(self.orders)
            for ref, item in orders.items():
                fees = [number(self.commissions[execution_id])
                        for execution_id, order_ref in self.execution_refs.items()
                        if order_ref == ref and execution_id in self.commissions]
                item["reported_commission_usd"] = str(sum(fees)) if fees else None
            return dict(host="127.0.0.1", port=7497,
                paper_account_verified=bool(self.account and self.expected_hash and
                    self.account_hash == self.expected_hash and not self.unsafe_connection),
                account_hash=self.account_hash,
                server_version=self.serverVersion(),
                reconciliation_complete=all(event.is_set() for event in self.events.values()),
                cash_balance_usd=self.cash.get("$LEDGER-CashBalance", "NaN"),
                available_usd=self.cash.get("AvailableFunds", "NaN"),
                settled_cash_evidence_available="SettledCash" in self.cash,
                in_regular_hours=execution_window(datetime.now(timezone.utc)),
                conflicting_open_orders=bool(self.other_open_orders),
                quotes=copy.deepcopy(self.quotes), orders=orders,
                positions=copy.deepcopy(self.positions))

    def _allocate(self):
        with self._mutex:
            if self.next_id is None:
                raise ValueError("missing API order ID")
            value = self.next_id
            self.next_id += 1
            return value

    def preview(self, row):
        self.plan[row["order_ref"]] = row
        order_id = self._allocate()
        self.previews[order_id] = {"done": threading.Event()}
        self.placeOrder(order_id, contract_for(row), order_for(row, self.account, preview=True))
        item = self.previews[order_id]
        if not item["done"].wait(self.timeout):
            raise TimeoutError("paper what-if response timed out")
        if item.get("error") or item.get("warning") or item.get("status", "").upper() in {"INACTIVE", "REJECTED"}:
            raise ValueError(item.get("error") or item.get("warning") or "paper what-if rejected")
        return {key: value for key, value in item.items() if key != "done"}

    def submit(self, row):
        if not self.enabled:
            raise ValueError("paper submission is disabled")
        validate_snapshot(self.snapshot(), datetime.now(timezone.utc))
        self.plan[row["order_ref"]] = row
        self.placeOrder(self._allocate(), contract_for(row), order_for(row, self.account, preview=False))

    def placeOrder(self, orderId, contract, order):
        if (not self.isConnected() or self.unsafe_connection or not self.expected_hash or
                self.account_hash != self.expected_hash or order.account != self.account or
                not re.fullmatch(r"DU\d+", self.account)):
            raise ValueError("paper-only order guard failed")
        leg = next((leg for leg in LEGS if leg[1] == contract.conId), None)
        if (leg is None or contract.symbol != leg[0] or contract.secType != "STK" or
                contract.exchange != "SMART" or contract.currency != "USD" or
                contract.primaryExchange != leg[2] or order.action != "BUY" or
                order.orderType != "LMT" or order.tif != "DAY" or order.outsideRth or
                number(order.totalQuantity) <= 0 or number(order.lmtPrice) <= 0 or
                number(order.totalQuantity) * number(order.lmtPrice) > Decimal(leg[3]) or
                order.cashQty < 1e100):
            raise ValueError("fixed fractional limit-order guard failed")
        prefix = "preview-" if order.whatIf else ""
        if order.orderRef != prefix + reference(leg[0]) or (not order.whatIf and not self.enabled):
            raise ValueError("unapproved paper order reference")
        if not order.whatIf:
            if self.enable_check is None or not self.enable_check():
                raise ValueError("local paper enable flag is no longer true")
            validate_snapshot(self.snapshot(), datetime.now(timezone.utc))
        super().placeOrder(orderId, contract, order)

    def cancelOrder(self, *args, **kwargs):
        raise ValueError("pilot never cancels or replaces orders")

    def reqGlobalCancel(self, *args, **kwargs):
        raise ValueError("pilot never cancels other orders")

    def close(self):
        self.disconnect()
        if self.thread:
            self.thread.join(timeout=2)
