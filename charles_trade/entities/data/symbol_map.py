"""symbol_map table entity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True, slots=True)
class SymbolMap:
    """Maps continuous / raw symbol to venue instrument_id for a validity window."""

    instrument_pk: str
    raw_symbol: str  # e.g. "6EH6"
    instrument_id: int
    valid_from: datetime
    valid_to: Optional[datetime] = None
    publisher_id: Optional[int] = None
