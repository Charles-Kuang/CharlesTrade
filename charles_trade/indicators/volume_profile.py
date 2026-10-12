"""Volume profile over a half-open trade range [start, end)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from charles_trade.data.store import TradeStore
from charles_trade.entities.data.utils import price_raw_to_float
from charles_trade.indicators._range import to_event_ns


@dataclass(frozen=True, slots=True)
class VolumeProfileLevel:
    """One price level of volume inside the requested range."""

    price_raw: int
    buy_vol: int
    sell_vol: int
    unknown_vol: int
    trade_count: int

    @property
    def price(self) -> float:
        return price_raw_to_float(self.price_raw)

    @property
    def total_vol(self) -> int:
        return self.buy_vol + self.sell_vol + self.unknown_vol

    @property
    def delta(self) -> int:
        return self.buy_vol - self.sell_vol


def volume_profile(
    store: TradeStore,
    *,
    instrument_pk: str,
    start: datetime,
    end: datetime,
) -> list[VolumeProfileLevel]:
    """Aggregate trade size by price. Levels are sorted by price ascending."""
    start_ns = to_event_ns(start)
    end_ns = to_event_ns(end)
    if end_ns <= start_ns:
        return []

    rows = store._conn.execute(
        """
        SELECT
            price,
            SUM(CASE WHEN side = 'B' THEN size ELSE 0 END),
            SUM(CASE WHEN side = 'A' THEN size ELSE 0 END),
            SUM(CASE WHEN side = 'N' THEN size ELSE 0 END),
            COUNT(*)
        FROM trades
        WHERE instrument_pk = ? AND ts_event >= ? AND ts_event < ?
        GROUP BY price
        ORDER BY price
        """,
        (instrument_pk, start_ns, end_ns),
    )
    return [
        VolumeProfileLevel(
            price_raw=int(price),
            buy_vol=int(buy_vol),
            sell_vol=int(sell_vol),
            unknown_vol=int(unknown_vol),
            trade_count=int(trade_count),
        )
        for price, buy_vol, sell_vol, unknown_vol, trade_count in rows
    ]
