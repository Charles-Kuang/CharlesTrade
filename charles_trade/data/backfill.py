"""Orchestrate historical backfill with day-level gap fill + dedupe insert."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from charles_trade.data.clients.historical import HistoricalTradesClient
from charles_trade.data.store import TradeStore
from charles_trade.data.utils.gaps import (
    day_window,
    ensure_utc,
    iter_utc_days,
    missing_days,
)
from charles_trade.entities.data.ingest_state import IngestStatus
from charles_trade.entities.data.utils import (
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_DATASET,
    DEFAULT_STYPE_IN,
    utc_now,
)

# Overlap when resuming from covered_until (dedup absorbs duplicates).
_RESUME_OVERLAP = timedelta(minutes=1)


@dataclass(slots=True)
class BackfillRequest:
    instrument_pk: str = "6E"
    symbol: str = DEFAULT_CONTINUOUS_SYMBOL
    dataset: str = DEFAULT_DATASET
    stype_in: str = DEFAULT_STYPE_IN
    start: Optional[datetime] = None  # default: end - 365 days
    end: Optional[datetime] = None  # default: now UTC
    db_path: Path | str = Path("database/charles_trade.db")
    force_refetch_days: bool = False  # if True, ignore coverage_days


@dataclass(slots=True)
class BackfillResult:
    requested_days: int
    missing_days: int
    fetched_days: int
    trades_seen: int
    trades_inserted: int
    skipped_covered_days: int
    available_end: datetime


def _default_range(
    start: Optional[datetime],
    end: Optional[datetime],
) -> tuple[datetime, datetime]:
    end_dt = ensure_utc(end) if end else utc_now()
    start_dt = ensure_utc(start) if start else end_dt - timedelta(days=365)
    return start_dt, end_dt


def _resume_from(watermark: Optional[datetime]) -> Optional[datetime]:
    if watermark is None:
        return None
    return ensure_utc(watermark) - _RESUME_OVERLAP


def _apply_resume(
    win_start: datetime,
    win_end: datetime,
    resume: Optional[datetime],
) -> datetime:
    """If resume falls inside (win_start, win_end), continue from resume; else unchanged.

    Unmarked gap days before the watermark still full-fetch (never skip).
    """
    if resume is None:
        return win_start
    if win_start < resume < win_end:
        return resume
    return win_start


def backfill_trades(req: BackfillRequest) -> BackfillResult:
    start, end = _default_range(req.start, req.end)
    client = HistoricalTradesClient()
    available_end = client.available_end(req.dataset)

    if start >= available_end:
        raise ValueError(
            f"start {start.isoformat()} is after Historical available_end "
            f"{available_end.isoformat()} (use Live for newer data)"
        )

    if end > available_end:
        print(
            f"Clip end {end.isoformat()} → available_end {available_end.isoformat()}",
            flush=True,
        )
        end = available_end

    with TradeStore(req.db_path) as store:
        state = store.get_ingest_state(req.instrument_pk)
        # Decision cursor: max(history, live); Historical still only fills to end.
        covered_floor = (
            None
            if req.force_refetch_days
            else (
                ensure_utc(state.covered_until) if state.covered_until else None
            )
        )
        watermark = covered_floor
        resume = _resume_from(watermark)

        state.status = IngestStatus.BACKFILLING
        state.updated_at = utc_now()
        store.save_ingest_state(state)

        covered = (
            set()
            if req.force_refetch_days
            else store.covered_days(req.instrument_pk, req.dataset)
        )
        all_days = list(iter_utc_days(start, end))
        to_fetch = missing_days(start, end, covered)
        skipped = len(all_days) - len(to_fetch)

        trades_seen = 0
        trades_inserted = 0
        fetched_days = 0

        his_s = (
            state.history_covered_until.isoformat()
            if state.history_covered_until
            else "None"
        )
        live_s = (
            state.live_watermark_ts.isoformat()
            if state.live_watermark_ts
            else "None"
        )
        print(
            f"Backfill {req.instrument_pk} ({req.symbol}) "
            f"[{start.isoformat()} → {end.isoformat()}) "
            f"days={len(all_days)} missing={len(to_fetch)} skip={skipped} "
            f"his={his_s} live={live_s}"
            + (
                f" covered_until={watermark.isoformat()} "
                f"resume_from={resume.isoformat()}"
                if watermark is not None and resume is not None
                else ""
            ),
            flush=True,
        )

        # Live already at/past Historical end → no tip Historical work.
        if (
            not req.force_refetch_days
            and covered_floor is not None
            and covered_floor >= end
        ):
            print(
                f"Skip Historical tip: covered_until {covered_floor.isoformat()} "
                f">= available_end {end.isoformat()}",
                flush=True,
            )


        from charles_trade.chart.bars_30m import rebuild_bars_30m

        def _fetch_window(
            *,
            label: str,
            win_start: datetime,
            win_end: datetime,
            mark_day: date | None,
        ) -> None:
            nonlocal trades_seen, trades_inserted, fetched_days, watermark, resume

            print(
                f"{label} {win_start.isoformat()} → {win_end.isoformat()}",
                flush=True,
            )
            trades = client.fetch_trades(
                dataset=req.dataset,
                symbol=req.symbol,
                stype_in=req.stype_in,
                start=win_start,
                end=win_end,
                instrument_pk=req.instrument_pk,
            )
            attempted, inserted = store.insert_trades(trades)

            covered_note = ""
            if mark_day is not None:
                _ds, day_end = day_window(mark_day)
                if day_end <= available_end:
                    store.mark_day_covered(
                        req.instrument_pk,
                        req.dataset,
                        mark_day,
                        trade_count=attempted,
                    )
                    covered_note = "covered"
                else:
                    covered_note = "partial(not marked)"

            trades_seen += attempted
            trades_inserted += inserted
            fetched_days += 1
            n_bars = rebuild_bars_30m(
                store, req.instrument_pk, start=win_start, end=win_end
            )
            print(
                f"    trades={attempted} inserted={inserted} "
                f"dup_skipped={attempted - inserted} "
                f"{covered_note + ' ' if covered_note else ''}"
                f"bars_30m_upsert={n_bars}",
                flush=True,
            )

            # Advance history watermark; never drop below prior covered_until (live).
            state_now = store.get_ingest_state(req.instrument_pk)
            state_now.history_covered_until = win_end
            state_now.status = IngestStatus.BACKFILLING
            state_now.updated_at = utc_now()
            store.save_ingest_state(state_now)
            watermark = (
                max(win_end, covered_floor)
                if covered_floor is not None
                else win_end
            )
            resume = _resume_from(watermark)

        for i, day in enumerate(to_fetch, start=1):
            day_start, day_end = day_window(day)
            win_start = max(day_start, start)
            win_end = min(day_end, end)
            if win_end <= win_start:
                continue

            # covered_until already past this window (e.g. Live ahead of tip day)
            if resume is not None and resume >= win_end:
                print(
                    f"[{i}/{len(to_fetch)}] skip {day.isoformat()} "
                    f"(covered through {resume.isoformat()})",
                    flush=True,
                )
                continue

            adjusted = _apply_resume(win_start, win_end, resume)
            if adjusted != win_start:
                print(
                    f"[{i}/{len(to_fetch)}] resume {day.isoformat()} "
                    f"from watermark-overlap {adjusted.isoformat()}",
                    flush=True,
                )
            win_start = adjusted
            if win_end <= win_start:
                continue

            _fetch_window(
                label=f"[{i}/{len(to_fetch)}] fetch {day.isoformat()}",
                win_start=win_start,
                win_end=win_end,
                mark_day=day,
            )

        # Marked tip day but watermark still behind end: continue from until-1min.
        if (
            not req.force_refetch_days
            and watermark is not None
            and watermark < end
        ):
            tip_day = ensure_utc(watermark).date()
            if tip_day.isoformat() in covered or tip_day.isoformat() in store.covered_days(
                req.instrument_pk, req.dataset
            ):
                cont_start = max(_resume_from(watermark) or watermark, start)
                if cont_start < end:
                    _fetch_window(
                        label=f"[continue] marked tip {tip_day.isoformat()}",
                        win_start=cont_start,
                        win_end=end,
                        mark_day=None,
                    )
                    # Re-evaluate mark for tip calendar day now that end may close it.
                    _ds, tip_day_end = day_window(tip_day)
                    if tip_day_end <= available_end and end >= tip_day_end:
                        store.mark_day_covered(
                            req.instrument_pk,
                            req.dataset,
                            tip_day,
                            trade_count=0,
                        )

        state = store.get_ingest_state(req.instrument_pk)
        state.history_covered_until = end
        state.status = IngestStatus.IDLE
        state.updated_at = utc_now()
        store.save_ingest_state(state)

        print(
            f"Done. db={req.db_path} total_rows={store.trade_count(req.instrument_pk)} "
            f"seen={trades_seen} inserted={trades_inserted}",
            flush=True,
        )

        return BackfillResult(
            requested_days=len(all_days),
            missing_days=len(to_fetch),
            fetched_days=fetched_days,
            trades_seen=trades_seen,
            trades_inserted=trades_inserted,
            skipped_covered_days=skipped,
            available_end=available_end,
        )
