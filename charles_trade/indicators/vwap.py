"""Running VWAP over [start, end), one point per New York 30-minute bar.

From range start, accumulate trades; at each NY :00 / :30 bar that has
trades in-range, emit the cumulative VWAP (± 1 volume-weighted σ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from charles_trade.data.store import TradeStore
from charles_trade.entities.data.utils import price_raw_to_float
from charles_trade.indicators._range import to_event_ns

NY = ZoneInfo("America/New_York")
BAR_MINUTES = 30


@dataclass(frozen=True, slots=True)
class VwapPoint:
    """One running-VWAP sample at a 30m bar open (unix seconds, NY aligned)."""

    time: int
    vwap_raw: int
    first_higher_raw: int
    first_lower_raw: int
    volume: int  # cumulative size from range start through this bar
    trade_count: int

    @property
    def vwap(self) -> float:
        return price_raw_to_float(self.vwap_raw)

    @property
    def first_higher(self) -> float:
        return price_raw_to_float(self.first_higher_raw)

    @property
    def first_lower(self) -> float:
        return price_raw_to_float(self.first_lower_raw)


def _bar_open_unix(ts_event_ns: int) -> int:
    dt = datetime.fromtimestamp(ts_event_ns / 1_000_000_000, tz=timezone.utc).astimezone(NY)
    minute = (dt.minute // BAR_MINUTES) * BAR_MINUTES
    opened = dt.replace(minute=minute, second=0, microsecond=0)
    return int(opened.timestamp())


def _point_from_cum(
    *,
    time: int,
    notional: float,
    sum_sq: float,
    volume: int,
    trade_count: int,
) -> VwapPoint | None:
    if volume <= 0:
        return None
    mean = notional / volume
    variance = sum_sq / volume - mean * mean
    if variance < 0:
        variance = 0.0
    stdev = math.sqrt(variance)
    return VwapPoint(
        time=time,
        vwap_raw=round(mean),
        first_higher_raw=round(mean + stdev),
        first_lower_raw=round(mean - stdev),
        volume=volume,
        trade_count=trade_count,
    )


def vwap(
    store: TradeStore,
    *,
    instrument_pk: str,
    start: datetime,
    end: datetime,
) -> list[VwapPoint]:
    """Running VWAP from ``start``, one point per NY 30m bar in ``[start, end)``.

    Each point is cumulative VWAP (±1σ) using all trades from ``start`` through
    that 30m bar. ``time`` is the New York bar open (unix seconds).
    """
    start_ns = to_event_ns(start)
    end_ns = to_event_ns(end)
    if end_ns <= start_ns:
        return []

    rows = store._conn.execute(
        """
        SELECT ts_event, price, size
        FROM trades
        WHERE instrument_pk = ? AND ts_event >= ? AND ts_event < ?
        ORDER BY ts_event, sequence
        """,
        (instrument_pk, start_ns, end_ns),
    )

    points: list[VwapPoint] = []
    bucket: int | None = None
    notional = 0.0
    sum_sq = 0.0
    volume = 0
    trade_count = 0

    def flush(bar_time: int) -> None:
        pt = _point_from_cum(
            time=bar_time,
            notional=notional,
            sum_sq=sum_sq,
            volume=volume,
            trade_count=trade_count,
        )
        if pt is not None:
            points.append(pt)

    for ts_event, price, size in rows:
        opened = _bar_open_unix(int(ts_event))
        price_i = int(price)
        size_i = int(size)

        if bucket is None:
            bucket = opened
        elif opened != bucket:
            flush(bucket)
            bucket = opened

        notional += price_i * size_i
        sum_sq += price_i * price_i * size_i
        volume += size_i
        trade_count += 1

    if bucket is not None:
        flush(bucket)

    return points
