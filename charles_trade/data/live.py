"""Live ingest: Databento replay + real-time on one session.

Reads ingest_state.covered_until (= max(history, live)), subscribes with
start=watermark-overlap, inserts trades, upserts bars_30m, advances
live_watermark_ts, and marks coverage_days for fully completed UTC days.

Flush policy (B+D): batch full OR flush_interval elapsed OR REPLAY_COMPLETED.
Batch size / interval from env LIVE_FLUSH_BATCH_SIZE / LIVE_FLUSH_INTERVAL_SEC.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable, Optional

import databento as db
from databento_dbn import ErrorMsg, SystemMsg, TradeMsg

from charles_trade.data.store import TradeStore
from charles_trade.data.utils.gaps import day_window, ensure_utc, iter_utc_days
from charles_trade.data.utils.mapper import trade_msg_to_trade
from charles_trade.entities.data.ingest_state import IngestStatus
from charles_trade.entities.data.trade import DataSource, Trade
from charles_trade.entities.data.utils import (
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_DATASET,
    DEFAULT_STYPE_IN,
    utc_now,
)

_RESUME_OVERLAP = timedelta(minutes=1)
_LIVE_REPLAY_MAX_AGE = timedelta(hours=23)
_DEFAULT_BATCH = 200
_DEFAULT_FLUSH_INTERVAL_SEC = 1.0
_BAR_SECONDS = 30 * 60  # keep local; avoid import cycle with chart.bars_30m


def live_flush_settings_from_env() -> tuple[int, float]:
    """(batch_size, flush_interval_sec) from .env / process env."""
    batch_raw = os.getenv("LIVE_FLUSH_BATCH_SIZE", str(_DEFAULT_BATCH))
    interval_raw = os.getenv(
        "LIVE_FLUSH_INTERVAL_SEC", str(_DEFAULT_FLUSH_INTERVAL_SEC)
    )
    try:
        batch = max(1, int(batch_raw))
    except ValueError:
        batch = _DEFAULT_BATCH
    try:
        interval = max(0.05, float(interval_raw))
    except ValueError:
        interval = _DEFAULT_FLUSH_INTERVAL_SEC
    return batch, interval


def _mark_days_completed_by_live(
    store: TradeStore,
    *,
    instrument_pk: str,
    dataset: str,
    prev_watermark: Optional[datetime],
    new_watermark: datetime,
) -> list[date]:
    """Mark UTC days whose end is now <= live watermark (fully past).

    Only newly completed days since prev_watermark. Skips days already in
    coverage_days so backfill trade_count is preserved.
    """
    new_watermark = ensure_utc(new_watermark)
    if prev_watermark is not None:
        prev_watermark = ensure_utc(prev_watermark)
        scan_start = prev_watermark
    else:
        scan_start = new_watermark - timedelta(days=1)

    already = store.covered_days(instrument_pk, dataset)
    marked: list[date] = []
    # [scan_start, new_watermark + epsilon) so the day containing new_wm is visited
    for day in iter_utc_days(scan_start, new_watermark + timedelta(seconds=1)):
        _ds, day_end = day_window(day)
        if day_end > new_watermark:
            continue
        if prev_watermark is not None and day_end <= prev_watermark:
            continue
        key = day.isoformat()
        if key in already:
            continue
        store.mark_day_covered(instrument_pk, dataset, day, trade_count=0)
        marked.append(day)
        already.add(key)
    return marked


@dataclass(slots=True)
class LiveRequest:
    instrument_pk: str = "6E"
    symbol: str = DEFAULT_CONTINUOUS_SYMBOL
    dataset: str = DEFAULT_DATASET
    stype_in: str = DEFAULT_STYPE_IN
    db_path: Path | str = Path("database/charles_trade.db")
    start: Optional[datetime] = None  # override; default from ingest_state
    overlap: timedelta = _RESUME_OVERLAP
    batch_size: int = _DEFAULT_BATCH
    flush_interval_sec: float = _DEFAULT_FLUSH_INTERVAL_SEC
    on_flush: Optional[Callable[[dict[str, Any]], None]] = None
    # serve_chart may pass {"client": None}; we set client so Ctrl+C can stop() it
    session: dict[str, Any] = field(default_factory=dict)


def resolve_live_start(
    store: TradeStore,
    req: LiveRequest,
) -> datetime:
    """covered_until (or override), minus overlap, clamped to ~24h."""
    if req.start is not None:
        base = ensure_utc(req.start)
    else:
        state = store.get_ingest_state(req.instrument_pk)
        raw = state.covered_until
        if raw is None:
            base = utc_now() - timedelta(hours=1)
            print(
                f"[live] no ingest watermark; default start={base.isoformat()}",
                flush=True,
            )
        else:
            base = ensure_utc(raw)
            print(
                f"[live] covered_until={base.isoformat()} "
                f"(his={state.history_covered_until} live={state.live_watermark_ts})",
                flush=True,
            )

    start = base - req.overlap
    earliest = utc_now() - _LIVE_REPLAY_MAX_AGE
    if start < earliest:
        print(
            f"[live] clamp start {start.isoformat()} → {earliest.isoformat()} "
            f"(Live replay window)",
            flush=True,
        )
        start = earliest
    return start


def _is_replay_completed(rec: SystemMsg) -> bool:
    code = getattr(rec, "code", None)
    try:
        from databento_dbn import SystemCode

        if code == SystemCode.REPLAY_COMPLETED:
            return True
    except Exception:
        pass
    if code is not None and "REPLAY_COMPLETED" in str(code):
        return True
    msg = str(getattr(rec, "msg", "") or "").lower()
    return "replay" in msg and "completed" in msg


def run_live_trades(
    req: LiveRequest,
    *,
    stop_event: Optional[Event] = None,
) -> None:
    """Block: subscribe Live (replay then real-time) until stop_event or disconnect.

    Flush when: batch full, flush_interval elapsed (timer), or REPLAY_COMPLETED.
    """
    stop_event = stop_event or Event()
    db_path = Path(req.db_path)
    batch_size = max(1, int(req.batch_size))
    flush_interval = max(0.05, float(req.flush_interval_sec))

    with TradeStore(db_path) as store:
        live_start = resolve_live_start(store, req)
        state = store.get_ingest_state(req.instrument_pk)
        state.status = IngestStatus.LIVE
        state.updated_at = utc_now()
        store.save_ingest_state(state)

    print(
        f"[live] subscribe {req.instrument_pk} ({req.symbol}) "
        f"start={live_start.isoformat()} "
        f"batch={batch_size} flush_interval={flush_interval}s",
        flush=True,
    )

    client = db.Live()
    req.session["client"] = client
    client.subscribe(
        dataset=req.dataset,
        schema="trades",
        symbols=[req.symbol],
        stype_in=req.stype_in,
        start=live_start,
    )

    buffer: list[Trade] = []
    seen = 0
    inserted_total = 0
    last_ts_event: int | None = None
    last_flush_mono = time.monotonic()
    buf_lock = threading.Lock()
    # Watchdog only; do NOT set stop_event here — that is owned by serve_chart (Ctrl+C).
    session_done = Event()

    def flush(*, reason: str = "") -> None:
        nonlocal buffer, inserted_total, last_ts_event, last_flush_mono
        with buf_lock:
            if not buffer:
                return
            batch = buffer
            buffer = []
        # Lazy: chart.bars_30m → store → data package; avoid cycle at import time
        from charles_trade.chart.bars_30m import _bar_open_unix, rebuild_bars_30m

        min_ns = min(t.ts_event for t in batch)
        max_ns = max(t.ts_event for t in batch)
        # Align to full NY 30m bars so rebuild never upserts clipped edge bars.
        win_start = datetime.fromtimestamp(_bar_open_unix(min_ns), tz=timezone.utc)
        win_end = datetime.fromtimestamp(
            _bar_open_unix(max_ns) + _BAR_SECONDS, tz=timezone.utc
        )
        with TradeStore(db_path) as store:
            _attempted, inserted = store.insert_trades(batch)
            inserted_total += inserted
            n_bars = rebuild_bars_30m(
                store, req.instrument_pk, start=win_start, end=win_end
            )
            state = store.get_ingest_state(req.instrument_pk)
            prev_wm = state.live_watermark_ts or state.history_covered_until
            new_wm = datetime.fromtimestamp(
                max_ns / 1_000_000_000, tz=timezone.utc
            )
            marked_days = _mark_days_completed_by_live(
                store,
                instrument_pk=req.instrument_pk,
                dataset=req.dataset,
                prev_watermark=prev_wm,
                new_watermark=new_wm,
            )
            state.live_watermark_ts = new_wm
            state.last_sequence = batch[-1].sequence
            state.status = IngestStatus.LIVE
            state.updated_at = utc_now()
            store.save_ingest_state(state)
        last_ts_event = max_ns
        last_flush_mono = time.monotonic()
        extra = (
            f" coverage_days+={','.join(d.isoformat() for d in marked_days)}"
            if marked_days
            else ""
        )
        why = f" reason={reason}" if reason else ""
        print(
            f"[live] flush inserted={inserted} bars_30m_upsert={n_bars} "
            f"watermark={state.live_watermark_ts.isoformat()} "
            f"seen={seen}{extra}{why}",
            flush=True,
        )
        if req.on_flush is not None:
            try:
                req.on_flush(
                    {
                        "instrument_pk": req.instrument_pk,
                        "watermark": state.live_watermark_ts.isoformat(),
                        "inserted": inserted,
                        "bars_upserted": n_bars,
                        "reason": reason,
                    }
                )
            except Exception as exc:
                print(f"[live] on_flush error: {exc}", flush=True)

    def maybe_flush_by_size() -> None:
        with buf_lock:
            n = len(buffer)
        if n >= batch_size:
            flush(reason="batch")

    def maybe_flush_by_time() -> None:
        with buf_lock:
            pending = len(buffer) > 0
        if pending and (time.monotonic() - last_flush_mono) >= flush_interval:
            flush(reason="interval")

    def _interval_watchdog() -> None:
        tick = min(0.25, max(0.05, flush_interval / 2.0))
        while not session_done.is_set() and not stop_event.is_set():
            if session_done.wait(timeout=tick):
                break
            if stop_event.is_set():
                break
            maybe_flush_by_time()

    watchdog = threading.Thread(
        target=_interval_watchdog, name="live-flush-watchdog", daemon=True
    )
    watchdog.start()

    loop_exit = "unknown"
    try:
        for rec in client:
            if stop_event.is_set():
                loop_exit = "stop_event"
                break
            if isinstance(rec, ErrorMsg):
                print(f"[live] ErrorMsg: {rec}", flush=True)
                continue
            if isinstance(rec, SystemMsg):
                print(f"[live] SystemMsg: {rec}", flush=True)
                if _is_replay_completed(rec):
                    flush(reason="replay_completed")
                maybe_flush_by_time()
                continue
            if not isinstance(rec, TradeMsg):
                maybe_flush_by_time()
                continue

            trade = trade_msg_to_trade(
                rec,
                instrument_pk=req.instrument_pk,
                dataset=req.dataset,
                source=DataSource.LIVE,
            )
            with buf_lock:
                buffer.append(trade)
            seen += 1
            maybe_flush_by_size()
            maybe_flush_by_time()
        else:
            # for-loop exhausted without break → gateway closed the stream
            loop_exit = "stream_ended"
    except KeyboardInterrupt:
        loop_exit = "keyboard"
        print("[live] interrupted", flush=True)
    except Exception as exc:
        loop_exit = f"error:{type(exc).__name__}"
        print(f"[live] session error: {exc}", flush=True)
        raise
    finally:
        session_done.set()
        flush(reason="shutdown")
        try:
            client.stop()
        except Exception:
            pass
        req.session.pop("client", None)
        with TradeStore(db_path) as store:
            state = store.get_ingest_state(req.instrument_pk)
            state.status = IngestStatus.IDLE
            state.updated_at = utc_now()
            store.save_ingest_state(state)
        print(
            f"[live] stopped exit={loop_exit} seen={seen} "
            f"inserted_total={inserted_total} last_ts_event={last_ts_event}",
            flush=True,
        )
