"""instruments table entity."""

from __future__ import annotations

from dataclasses import dataclass

from charles_trade.entities.data.utils import (
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_DATASET,
    DEFAULT_MULTIPLIER,
    DEFAULT_REGIME_TREND_TICKS,
    DEFAULT_SCHEMA,
    DEFAULT_STYPE_IN,
    DEFAULT_TICK_SIZE,
)


@dataclass(frozen=True, slots=True)
class Instrument:
    """Logical product. One row for 6E; concrete months live in SymbolMap."""

    instrument_pk: str  # e.g. "6E"
    dataset: str = DEFAULT_DATASET
    continuous_symbol: str = DEFAULT_CONTINUOUS_SYMBOL
    stype_in: str = DEFAULT_STYPE_IN
    schema: str = DEFAULT_SCHEMA
    tick_size: float = DEFAULT_TICK_SIZE
    multiplier: float = DEFAULT_MULTIPLIER
    regime_trend_ticks: int = DEFAULT_REGIME_TREND_TICKS
    currency: str = "USD"
    unit_of_measure: str = "EUR"
