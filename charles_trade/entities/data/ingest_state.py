"""ingest_state table entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from charles_trade.entities.data.utils import DEFAULT_SCHEMA, utc_now


class IngestStatus(str, Enum):
    IDLE = "idle"
    BACKFILLING = "backfilling"
    LIVE = "live"


@dataclass(slots=True)
class IngestState:
    """Watermarks for history → live bridge. One row per instrument + schema.

    history_covered_until / live_watermark_ts are written by each pipeline;
    decisions use covered_until = max(both).
    """

    instrument_pk: str
    schema: str = DEFAULT_SCHEMA
    history_covered_until: Optional[datetime] = None
    live_watermark_ts: Optional[datetime] = None
    last_sequence: Optional[int] = None
    status: IngestStatus = IngestStatus.IDLE
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def covered_until(self) -> Optional[datetime]:
        """Effective coverage cursor: max(history_covered_until, live_watermark_ts)."""
        h = self.history_covered_until
        live = self.live_watermark_ts
        if h is None:
            return live
        if live is None:
            return h
        return max(h, live)

    @property
    def resume_from(self) -> Optional[datetime]:
        """Resume point for backfill tip / Live subscribe (same as covered_until)."""
        return self.covered_until
