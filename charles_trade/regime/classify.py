"""Classify a regime from open/close move in ticks."""

from __future__ import annotations

from charles_trade.entities.data.regime import RegimeLabel, color_for
from charles_trade.entities.data.utils import (
    DEFAULT_REGIME_TREND_TICKS,
    DEFAULT_TICK_SIZE,
)

__all__ = ["classify_regime", "color_for"]


def classify_regime(
    open_price: float,
    close_price: float,
    *,
    tick_size: float = DEFAULT_TICK_SIZE,
    trend_ticks: int = DEFAULT_REGIME_TREND_TICKS,
) -> RegimeLabel:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    move_ticks = abs(close_price - open_price) / tick_size
    if move_ticks > trend_ticks:
        return RegimeLabel.UP if close_price > open_price else RegimeLabel.DOWN
    return RegimeLabel.RANGE
