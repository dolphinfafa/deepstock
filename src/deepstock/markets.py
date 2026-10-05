"""Strategy coverage categories; not exchange permissions or trading authority."""
from enum import StrEnum


class StrategyMarket(StrEnum):
    US = "US"
    CN = "CN"
    BOTH = "Both"


MARKET_LABELS = {StrategyMarket.US: "美股", StrategyMarket.CN: "A股", StrategyMarket.BOTH: "Both"}
