"""Hand-drawn market regimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from charles_trade.entities.data.utils import price_raw_to_float


class RegimeLabel(IntEnum):
    """Extensible regime kind codes (DB stores the int).

    1–3: price-move classification. Higher codes reserved for future draw tools.
    """

    UP = 1
    DOWN = 2
    RANGE = 3
    # e.g. DRAW_ZONE = 4, DRAW_NOTE = 5, …


REGIME_NAMES: dict[int, str] = {
    RegimeLabel.UP: "up",
    RegimeLabel.DOWN: "down",
    RegimeLabel.RANGE: "range",
}

# Display-only; not persisted
REGIME_COLORS: dict[int, str] = {
    RegimeLabel.UP: "rgba(38, 166, 154, 0.22)",
    RegimeLabel.DOWN: "rgba(239, 83, 80, 0.22)",
    RegimeLabel.RANGE: "rgba(255, 160, 0, 0.22)",
}


def color_for(label: RegimeLabel | int) -> str:
    return REGIME_COLORS.get(int(label), REGIME_COLORS[RegimeLabel.RANGE])


def name_for(label: RegimeLabel | int) -> str:
    return REGIME_NAMES.get(int(label), f"label_{int(label)}")


@dataclass(frozen=True, slots=True)
class RegimeVpLevel:
    """One persisted volume-profile price level for a regime."""

    price_raw: int
    buy_vol: int
    sell_vol: int
    unknown_vol: int
    trade_count: int


@dataclass(frozen=True, slots=True)
class RegimeVwapPoint:
    """One persisted running-VWAP sample (30m bar) for a regime."""

    time: int
    vwap_raw: int
    first_higher_raw: int
    first_lower_raw: int
    volume: int
    trade_count: int


@dataclass(frozen=True, slots=True)
class Regime:
    """Time range on one instrument. start/end are unix seconds (half-open [start, end))."""

    instrument_pk: str
    start_ts: int
    end_ts: int
    label: RegimeLabel
    id: int | None = None
    vwap_raw: int | None = None  # last running VWAP in range (summary)
    first_higher_raw: int | None = None
    first_lower_raw: int | None = None
    volume: int = 0
    trade_count: int = 0
    # Populated on save path only; not returned from list_regimes.
    vp_levels: tuple[RegimeVpLevel, ...] = field(default_factory=tuple)
    vwap_points: tuple[RegimeVwapPoint, ...] = field(default_factory=tuple)

    @property
    def color(self) -> str:
        return color_for(self.label)

    @property
    def label_name(self) -> str:
        return name_for(self.label)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "instrument_pk": self.instrument_pk,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "label": int(self.label),
            "label_name": self.label_name,
            "color": self.color,
            "vwap": price_raw_to_float(self.vwap_raw) if self.vwap_raw is not None else None,
            "first_higher": (
                price_raw_to_float(self.first_higher_raw)
                if self.first_higher_raw is not None
                else None
            ),
            "first_lower": (
                price_raw_to_float(self.first_lower_raw)
                if self.first_lower_raw is not None
                else None
            ),
            "volume": self.volume,
            "trade_count": self.trade_count,
        }
