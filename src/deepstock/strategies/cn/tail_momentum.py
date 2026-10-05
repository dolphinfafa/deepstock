"""Independent, cash/lot-aware A-share ETF afternoon-momentum research."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


STRATEGY_ID = "cn_etf_tail_momentum"
MARKET = "CN"
VERSION = "tail-momentum-t1-v1-20261006"
PATHS = ("r6_next_open", "r6_next_close", "r6_r7_next_open")


@dataclass(frozen=True)
class TailMomentumConfig:
    symbol: str = "510300.SH"
    initial_capital: float = 100_000.0
    lot_size: int = 100
    participation_rate: float = 0.01
    commission_bps: float = 2.5
    minimum_commission: float = 5.0
    slippage_bps: float = 2.5

    def __post_init__(self) -> None:
        if self.symbol != "510300.SH":
            raise ValueError("This frozen A-share ETF candidate supports only 510300.SH; no US extension")
        numbers = (self.initial_capital, self.participation_rate, self.commission_bps,
                   self.minimum_commission, self.slippage_bps)
        if not all(np.isfinite(value) for value in numbers):
            raise ValueError("Configuration must be finite")
        if self.initial_capital <= 0 or self.lot_size < 1 or not 0 < self.participation_rate <= 1:
            raise ValueError("Invalid capital, lot size or participation")
        if min(self.commission_bps, self.minimum_commission, self.slippage_bps) < 0 or self.slippage_bps >= 10000:
            raise ValueError("Invalid transaction costs")


def cost_cases() -> dict[str, TailMomentumConfig]:
    return {
        "primary_10bp": TailMomentumConfig(),
        "stress_6bp": TailMomentumConfig(slippage_bps=0.5),
        "stress_20bp": TailMomentumConfig(slippage_bps=7.5),
        "video_1bp_idealized": TailMomentumConfig(commission_bps=0.5, minimum_commission=0, slippage_bps=0),
    }


def prepare_sessions(bars: pd.DataFrame, calendar: pd.DatetimeIndex, symbol: str = "510300.SH") -> pd.DataFrame:
    """Validate end-labeled raw one-minute bars; never stitch across absent days."""
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume", "amount"}
    if missing := required.difference(bars.columns):
        raise ValueError(f"Minute data missing columns: {sorted(missing)}")
    dates = pd.DatetimeIndex(calendar).normalize()
    if dates.empty or dates.has_duplicates or not dates.is_monotonic_increasing or dates.tz is not None:
        raise ValueError("Calendar must be nonempty, unique, sorted Shanghai session dates")
    selected = bars[bars["symbol"].eq(symbol)].copy()
    if selected.empty:
        raise ValueError(f"No minute data for {symbol}")
    timestamps = pd.to_datetime(selected["timestamp"], format="mixed")
    if timestamps.dt.tz is not None:
        timestamps = timestamps.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    selected["timestamp"] = timestamps
    if timestamps.duplicated().any() or timestamps.isna().any():
        raise ValueError("Minute timestamps must be unique and valid")
    if (timestamps.dt.second.ne(0) | timestamps.dt.microsecond.ne(0)).any():
        raise ValueError("Expected exact end-labeled minute timestamps")
    selected = selected.set_index("timestamp").sort_index()
    values = selected[["open", "high", "low", "close", "volume", "amount"]].astype(float)
    if not np.isfinite(values.to_numpy()).all() or (values[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Minute prices must be finite and positive")
    if (values[["volume", "amount"]] < 0).any().any() or values["volume"].eq(0).ne(values["amount"].eq(0)).any():
        raise ValueError("Invalid minute activity")
    if (values["high"] < values[["open", "close", "low"]].max(axis=1)).any() or (values["low"] > values[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid minute OHLC range")
    active = values["volume"].gt(0)
    vwap = values.loc[active, "amount"] / values.loc[active, "volume"]
    if not vwap.between(values.loc[active, "low"] * 0.999, values.loc[active, "high"] * 1.001).all():
        raise ValueError("Minute VWAP inconsistent with OHLC: check amount/volume units")
    if not selected.index.normalize().isin(dates).all():
        raise ValueError("Bars fall outside the declared exchange calendar")
    selected.loc[:, values.columns] = values
    records = []
    for day in dates:
        frame = selected[selected.index.normalize() == day]
        if frame.empty or pd.Timestamp(f"{day.date()} 15:00") not in frame.index:
            raise ValueError(f"Missing session/closing mark on {day.date()}; cannot fabricate NAV")
        def price(clock: str) -> float:
            stamp = pd.Timestamp(f"{day.date()} {clock}")
            return float(frame.at[stamp, "close"]) if stamp in frame.index else np.nan
        def complete(start: str, end: str) -> bool:
            return pd.date_range(f"{day.date()} {start}", f"{day.date()} {end}", freq="min").isin(frame.index).all()
        row: dict[str, Any] = {"date": day, "close": price("15:00"), "p5": price("13:30"), "p6": price("14:00"), "p7": price("14:30")}
        row["r6_valid"] = bool(np.isfinite(row["p5"]) and complete("13:31", "14:00"))
        row["r7_valid"] = bool(np.isfinite(row["p6"]) and complete("14:01", "14:30"))
        row["r6"] = row["p6"] / row["p5"] - 1 if row["r6_valid"] else np.nan
        row["r7"] = row["p7"] / row["p6"] - 1 if row["r7_valid"] else np.nan
        for name, clock in (("entry", "14:02"), ("confirm_entry", "14:32"), ("open_exit", "09:31"), ("close_exit", "15:00")):
            stamp = pd.Timestamp(f"{day.date()} {clock}")
            volume = float(frame.at[stamp, "volume"]) if stamp in frame.index else 0.0
            amount = float(frame.at[stamp, "amount"]) if stamp in frame.index else 0.0
            row[f"{name}_volume"] = volume
            row[f"{name}_price"] = amount / volume if volume > 0 else np.nan
        records.append(row)
    return pd.DataFrame(records).set_index("date")


def summarize(daily: pd.DataFrame) -> dict[str, Any]:
    if daily.empty:
        raise ValueError("Cannot summarize an empty period")
    net = daily["portfolio_net_return"]
    equity = (1 + net).cumprod()
    peak = equity.cummax().clip(lower=1)
    volatility = float(net.std(ddof=0))
    return {
        "start": daily.index[0].date().isoformat(), "end": daily.index[-1].date().isoformat(),
        "trading_days": len(daily), "total_return": float(equity.iloc[-1] - 1),
        "annualized_return": float(equity.iloc[-1] ** (252 / len(daily)) - 1),
        "sharpe_ratio": float(net.mean() / volatility * np.sqrt(252)) if volatility > 0 else None,
        "maximum_drawdown": float((equity / peak - 1).min()),
        "total_turnover": float(daily["turnover"].sum()),
        "annualized_turnover": float(daily["turnover"].sum() * 252 / len(daily)),
        "commission_cny": float(daily["commission_cny"].sum()),
        "slippage_cny": float(daily["slippage_cny"].sum()),
        "average_exposure": float(daily["exposure"].mean()),
        "entry_fills": int(daily["entry_fill"].sum()), "exit_fills": int(daily["exit_fill"].sum()),
        "blocked_entries": int(daily["blocked_entry"].sum()),
        "delayed_exit_sessions": int(daily["delayed_exit"].sum()),
        "invalid_signal_sessions": int(daily["invalid_signal"].sum()),
    }


@dataclass
class TailMomentumResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    summary: dict[str, Any]


def run_tail_momentum(
    sessions: pd.DataFrame, path: str = "r6_next_open", config: TailMomentumConfig | None = None,
    *, always_enter: bool = False, dividends: pd.DataFrame | None = None,
) -> TailMomentumResult:
    """Single-position ledger with next-session exits and modeled partial fills.

    Complete future-bar volume/VWAP affects fills only, never signal decisions.
    The next-close case processes its old exit after the afternoon entry check.
    """
    config = config or TailMomentumConfig()
    if path not in (*PATHS, "buy_hold") or sessions.empty:
        raise ValueError("Unknown path or empty sessions")
    if sessions.index.has_duplicates or not sessions.index.is_monotonic_increasing:
        raise ValueError("Sessions must be unique and sorted")
    required = {"close", "r6", "r7", "r6_valid", "r7_valid"}
    required.update(f"{name}_{field}" for name in ("entry", "confirm_entry", "open_exit", "close_exit") for field in ("price", "volume"))
    if required.difference(sessions.columns):
        raise ValueError("Session panel is incomplete")
    if not np.isfinite(sessions["close"]).all() or sessions["close"].le(0).any():
        raise ValueError("Invalid closing marks")
    dividends = dividends if dividends is not None else pd.DataFrame(columns=["ex_date", "pay_date", "cash_per_share"])
    events: dict[pd.Timestamp, list[dict]] = {}
    for event in dividends.to_dict("records"):
        ex, pay = pd.Timestamp(event["ex_date"]).normalize(), pd.Timestamp(event["pay_date"]).normalize()
        amount = float(event["cash_per_share"])
        if not np.isfinite(amount) or amount < 0 or pay < ex:
            raise ValueError("Invalid point-in-time dividend")
        events.setdefault(ex, []).append({"pay_date": pay, "cash_per_share": amount})
    cash = previous_nav = config.initial_capital
    position: dict[str, Any] | None = None
    receivables: list[dict] = []
    records, trades = [], []
    entry_ids: set[int] = set()
    for index, (day, row) in enumerate(sessions.iterrows()):
        for receivable in list(receivables):
            if receivable["pay_date"] <= day:
                cash += receivable["amount"]
                receivables.remove(receivable)
        for event in events.get(day, []):
            if position is not None:
                income = position["shares"] * event["cash_per_share"]
                position["dividend_per_share"] += event["cash_per_share"]
                if event["pay_date"] <= day:
                    cash += income
                else:
                    receivables.append({"pay_date": event["pay_date"], "amount": income})
        fees = slippage = notional = 0.0
        entry_fill = exit_fill = blocked_entry = delayed_exit = 0
        valid = bool(row["r6_valid"] and (row["r7_valid"] if path == "r6_r7_next_open" else True))
        signal = valid and row["r6"] > 0 and (row["r7"] > 0 if path == "r6_r7_next_open" else True)
        signal = bool(signal or always_enter or (path == "buy_hold" and index == 0))
        if path == "buy_hold":
            signal = index == 0

        def sell(name: str) -> None:
            nonlocal cash, position, fees, slippage, notional, exit_fill, delayed_exit
            if position is None or index <= position["entry_index"]:
                return
            raw = float(row[f"{name}_price"])
            capacity = int(row[f"{name}_volume"] * config.participation_rate) // config.lot_size * config.lot_size
            quantity = min(position["shares"], capacity)
            if quantity <= 0 or not np.isfinite(raw) or raw <= 0:
                delayed_exit = 1
                return
            executed = raw * (1 - config.slippage_bps / 10000)
            commission = max(config.minimum_commission, quantity * executed * config.commission_bps / 10000)
            cash += quantity * executed - commission
            fees += commission
            slippage += quantity * (raw - executed)
            notional += quantity * raw
            cost_basis = quantity * (position["entry_price"] + position["entry_commission_per_share"])
            pnl = quantity * (executed + position["dividend_per_share"]) - commission - cost_basis
            trades.append({
                "entry_id": position["entry_index"], "entry_date": position["entry_date"].date().isoformat(),
                "exit_date": day.date().isoformat(), "signal_time": position["signal_time"],
                "entry_bar_end": position["entry_bar_end"], "exit_bar_end": "09:31" if name == "open_exit" else "15:00",
                "shares": quantity, "net_pnl_cny": pnl, "net_trade_return": pnl / cost_basis,
                "holding_sessions": index - position["entry_index"],
                "entry_to_close_return": position["entry_close"] / position["raw_entry_price"] - 1,
                "entry_close_to_exit_return": raw / position["entry_close"] - 1,
                "entry_commission_cny": quantity * position["entry_commission_per_share"], "exit_commission_cny": commission,
                "dividend_entitlement_cny": quantity * position["dividend_per_share"],
            })
            position["shares"] -= quantity
            exit_fill = 1
            if position["shares"] == 0:
                position = None
            else:
                delayed_exit = 1

        if path in ("r6_next_open", "r6_r7_next_open"):
            sell("open_exit")
        if signal:
            if position is not None:
                blocked_entry = 1
            else:
                name = "open_exit" if path == "buy_hold" else "confirm_entry" if path == "r6_r7_next_open" else "entry"
                raw = float(row[f"{name}_price"])
                if np.isfinite(raw) and raw > 0:
                    executed = raw * (1 + config.slippage_bps / 10000)
                    affordable = max(0, min((cash - config.minimum_commission) / executed, cash / (executed * (1 + config.commission_bps / 10000))))
                    capacity = float(row[f"{name}_volume"]) * config.participation_rate
                    quantity = int(min(affordable, capacity)) // config.lot_size * config.lot_size
                    if quantity:
                        commission = max(config.minimum_commission, quantity * executed * config.commission_bps / 10000)
                        cash -= quantity * executed + commission
                        fees += commission
                        slippage += quantity * (executed - raw)
                        notional += quantity * raw
                        position = {
                            "entry_index": index, "entry_date": day, "shares": quantity, "entry_price": executed,
                            "raw_entry_price": raw, "entry_commission_per_share": commission / quantity,
                            "entry_close": float(row["close"]), "dividend_per_share": 0.0,
                            "signal_time": "14:30" if path == "r6_r7_next_open" else "14:00" if path != "buy_hold" else "preopen",
                            "entry_bar_end": "14:32" if path == "r6_r7_next_open" else "14:02" if path != "buy_hold" else "09:31",
                        }
                        entry_ids.add(index)
                        entry_fill = 1
                    else:
                        blocked_entry = 1
                else:
                    blocked_entry = 1
        if path == "r6_next_close":
            sell("close_exit")
        inventory = position["shares"] * float(row["close"]) if position else 0.0
        nav = cash + inventory + sum(item["amount"] for item in receivables)
        if cash < -1e-7 or nav <= 0:
            raise ValueError("Ledger violated no-borrowing/positive-capital constraints")
        records.append({
            "date": day, "nav_cny": nav, "cash_cny": cash, "shares": position["shares"] if position else 0,
            "receivable_cny": sum(item["amount"] for item in receivables),
            "portfolio_net_return": nav / previous_nav - 1,
            "portfolio_gross_return_same_fills": (nav + fees + slippage) / previous_nav - 1,
            "commission_cny": fees, "slippage_cny": slippage, "turnover": notional / previous_nav,
            "exposure": inventory / nav, "entry_fill": entry_fill, "exit_fill": exit_fill,
            "blocked_entry": blocked_entry, "delayed_exit": delayed_exit,
            "invalid_signal": int(not valid), "signal": bool(signal),
        })
        previous_nav = nav
    daily = pd.DataFrame(records).set_index("date")
    fills = pd.DataFrame(trades)
    complete = entry_ids - ({position["entry_index"]} if position else set())
    pnl_by_entry = fills.groupby("entry_id")["net_pnl_cny"].sum() if len(fills) else pd.Series(dtype=float)
    completed_pnl = pnl_by_entry[pnl_by_entry.index.isin(complete)]
    summary = summarize(daily)
    summary.update({
        "strategy_id": STRATEGY_ID, "version": VERSION, "path": path, "always_enter": always_enter,
        "config": asdict(config), "completed_trades": len(completed_pnl),
        "trade_win_rate": float(completed_pnl.gt(0).mean()) if len(completed_pnl) else None,
        "unclosed_shares": position["shares"] if position else 0, "unpaid_dividends_cny": float(daily.iloc[-1]["receivable_cny"]),
        "cash_interest": 0, "execution_status": "research_only_no_orders",
    })
    return TailMomentumResult(daily, fills, summary)


def walk_forward(daily: pd.DataFrame, train: int = 504, test: int = 252, step: int = 252) -> list[dict]:
    if min(train, test, step) < 1:
        raise ValueError("Invalid fixed reporting windows")
    return [{"window": number, "train_start": daily.index[start - train].date().isoformat(),
             "train_end": daily.index[start - 1].date().isoformat(), **summarize(daily.iloc[start:start + test])}
            for number, start in enumerate(range(train, len(daily) - test + 1, step))]
