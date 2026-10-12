"""trades table entity (Databento trades schema fields + our metadata)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from charles_trade.entities.data.utils import price_raw_to_float, utc_now


class Side(str, Enum):
    """Aggressor side."""

    BUY = "B"  # buy aggressor (lift offer)
    SELL = "A"  # sell aggressor (hit bid)
    NONE = "N"  # unknown / not provided


class DataSource(str, Enum):
    HISTORICAL = "historical"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class Trade:
    """One trade row for storage / footprint.

    Wire fields follow Databento `trades` (ts in Unix ns, price = 1e-9 fixed-point).
    Dedup key:
      (dataset, publisher_id, instrument_id, ts_event, sequence, price, size, side)
    """

    # ours
    instrument_pk: str
    dataset: str
    source: DataSource

    # databento trades
    publisher_id: int
    instrument_id: int
    ts_event: int  # ns since epoch UTC
    price: int  # price_raw
    size: int
    side: Side
    sequence: int
    ts_recv: Optional[int] = None
    flags: int = 0

    trade_id: Optional[int] = None
    ingested_at: datetime = field(default_factory=utc_now)

    @property
    def price_display(self) -> float:
        return price_raw_to_float(self.price)

    @property
    def price_raw(self) -> int:
        return self.price

    @property
    def ts_event_dt(self) -> datetime:
        return datetime.fromtimestamp(self.ts_event / 1_000_000_000, tz=timezone.utc)

    @property
    def ts_recv_dt(self) -> Optional[datetime]:
        if self.ts_recv is None:
            return None
        return datetime.fromtimestamp(self.ts_recv / 1_000_000_000, tz=timezone.utc)

    @property
    def dedup_key(self) -> tuple:
        return (
            self.dataset,
            self.publisher_id,
            self.instrument_id,
            self.ts_event,
            self.sequence,
            self.price,
            self.size,
            self.side.value,
        )
