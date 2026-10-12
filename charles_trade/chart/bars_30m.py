"""30-minute bars aligned to America/New_York — aggregate from trades, read from cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from charles_trade.data.store import TradeStore
from charles_trade.entities.data.utils import PRICE_SCALE

NY = ZoneInfo("America/New_York")
BAR_MINUTES = 30
BAR_SECONDS = BAR_MINUTES * 60


@dataclass(frozen=True, slots=True)
class Bar30m:
    time: int  # unix seconds of the New York bar open
    open: float
    high: float
    low: float
    close: float
    volume: int

    def as_row(self) -> tuple[int, float, float, float, float, int]:
        return (self.time, self.open, self.high, self.low, self.close, self.volume)


def _bar_open_unix(ts_event_ns: int) -> int:
    dt = datetime.fromtimestamp(ts_event_ns / 1_000_000_000, tz=timezone.utc).astimezone(NY)
    minute = (dt.minute // BAR_MINUTES) * BAR_MINUTES
    opened = dt.replace(minute=minute, second=0, microsecond=0)
    return int(opened.timestamp())


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def aggregate_bars_30m_from_trades(
    store: TradeStore,
    *,
    instrument_pk: str,
    start: datetime,
    end: datetime,
) -> list[Bar30m]:
    """Build OHLC bars from raw trades for [start, end). Bucket edges :00 / :30 NY."""
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)
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

    bars: list[Bar30m] = []
    bucket: int | None = None
    open_px = high_px = low_px = close_px = 0.0
    volume = 0

    def flush() -> None:
        if bucket is None:
            return
        bars.append(
            Bar30m(
                time=bucket,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=volume,
            )
        )

    for ts_event, price_raw, size in rows:
        opened = _bar_open_unix(int(ts_event))
        price = int(price_raw) / PRICE_SCALE
        qty = int(size)
        if bucket != opened:
            flush()
            bucket = opened
            open_px = high_px = low_px = close_px = price
            volume = qty
            continue
        if price > high_px:
            high_px = price
        if price < low_px:
            low_px = price
        close_px = price
        volume += qty
    flush()
    return bars


def rebuild_bars_30m(
    store: TradeStore,
    instrument_pk: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> int:
    """Aggregate trades → upsert bars_30m.

    With no range: full replace for the instrument (all trades).
    With range: expand to bar edges and upsert touched bars.
    """
    if start is None and end is None:
        row = store._conn.execute(
            "SELECT MIN(ts_event), MAX(ts_event) FROM trades WHERE instrument_pk = ?",
            (instrument_pk,),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            store.replace_bars_30m(instrument_pk, [])
            return 0
        start = datetime.fromtimestamp(int(row[0]) / 1_000_000_000, tz=timezone.utc)
        # include last trade's bar
        end = datetime.fromtimestamp(int(row[1]) / 1_000_000_000, tz=timezone.utc) + timedelta(
            seconds=1
        )
        bars = aggregate_bars_30m_from_trades(
            store, instrument_pk=instrument_pk, start=start, end=end
        )
        return store.replace_bars_30m(instrument_pk, (b.as_row() for b in bars))

    start = _ensure_utc(start) if start else datetime(1970, 1, 1, tzinfo=timezone.utc)
    end = _ensure_utc(end) if end else datetime.now(timezone.utc)
    # Expand trade query so bars that intersect [start, end) have full coverage.
    start_exp = start - timedelta(seconds=BAR_SECONDS)
    end_exp = end + timedelta(seconds=BAR_SECONDS)
    bars = aggregate_bars_30m_from_trades(
        store, instrument_pk=instrument_pk, start=start_exp, end=end_exp
    )
    # CRITICAL: do NOT upsert expanded edge bars — they are often incomplete
    # (trade query clipped mid-bar) and would overwrite good cache rows.
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    to_upsert = [
        b.as_row()
        for b in bars
        if b.time < end_ts and (b.time + BAR_SECONDS) > start_ts
    ]
    return store.upsert_bars_30m(instrument_pk, to_upsert)


def bars_30m(
    store: TradeStore,
    *,
    instrument_pk: str,
    start: datetime,
    end: datetime,
) -> list[Bar30m]:
    """Load cached 30m bars for [start, end)."""
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    rows = store.list_bars_30m(instrument_pk, start_ts=start_ts, end_ts=end_ts)
    return [
        Bar30m(time=ts, open=o, high=h, low=l, close=c, volume=vol)
        for ts, o, h, l, c, vol in rows
    ]


def default_range(store: TradeStore, instrument_pk: str) -> tuple[datetime, datetime]:
    """[max_ts - 365 days, max_ts] preferring bars_30m, else trades."""
    br = store.bars_30m_range(instrument_pk)
    if br is not None:
        _min_ts, max_ts = br
        end = datetime.fromtimestamp(max_ts + BAR_SECONDS, tz=timezone.utc)
        start = end - timedelta(days=365)
        return start, end

    row = store._conn.execute(
        "SELECT MAX(ts_event) FROM trades WHERE instrument_pk = ?",
        (instrument_pk,),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"no trades/bars for {instrument_pk}")
    end = datetime.fromtimestamp(int(row[0]) / 1_000_000_000, tz=timezone.utc)
    start = end - timedelta(days=365)
    return start, end
