"""Shared constants and helpers for market-data entities."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Databento price scale: price_raw / PRICE_SCALE == display price
PRICE_SCALE = 1_000_000_000  # e.g. 108500000000 → 1.085

# Exchange / display timezone (handles EST/EDT automatically)
NY_TZ = ZoneInfo("America/New_York")

# 6E defaults (verify via definition schema at runtime later)
DEFAULT_DATASET = "GLBX.MDP3"
DEFAULT_SCHEMA = "trades"
DEFAULT_CONTINUOUS_SYMBOL = "6E.n.0"
DEFAULT_STYPE_IN = "continuous"
DEFAULT_TICK_SIZE = 0.00005
DEFAULT_MULTIPLIER = 125_000.0  # EUR
DEFAULT_REGIME_TREND_TICKS = 10  # open/close move > this → up/down, else range


def utc_now() -> datetime:
    """Current time in UTC (storage / Databento alignment)."""
    return datetime.now(timezone.utc)


def ny_now() -> datetime:
    """Current time in America/New_York (EST or EDT)."""
    return datetime.now(NY_TZ)


def to_ny(dt: datetime) -> datetime:
    """Convert any aware/naive datetime to New York time.

    Naive values are treated as UTC (Databento convention).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY_TZ)


def price_raw_to_float(price_raw: int) -> float:
    return price_raw / PRICE_SCALE


def float_to_price_raw(price: float) -> int:
    return int(round(price * PRICE_SCALE))
