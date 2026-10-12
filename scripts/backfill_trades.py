"""Backfill Databento historical trades into local SQLite.

Examples:
  # default: 6E, last 365 days
  python scripts/backfill_trades.py

  # date-only: start=day 00:00 UTC, end=next day 00:00 UTC (includes end date)
  # e.g. --end 2026-09-16  →  2026-09-17T00:00:00Z
  python scripts/backfill_trades.py --start 2026-09-01 --end 2026-09-16

  # ISO datetime: used as-is (end still exclusive)
  python scripts/backfill_trades.py --start 2026-09-01T14:00:00 --end 2026-09-16T22:30:00
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from charles_trade.data.backfill import BackfillRequest, backfill_trades
from charles_trade.entities.data.utils import (
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_DATASET,
    DEFAULT_STYPE_IN,
)


def _is_date_only(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def _parse_start(value: str) -> datetime:
    """Date-only → that day 00:00 UTC; otherwise ISO datetime."""
    if _is_date_only(value):
        dt = datetime.strptime(value, "%Y-%m-%d")
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_end(value: str) -> datetime:
    """Date-only → next day 00:00 UTC (include that calendar day); otherwise ISO."""
    if _is_date_only(value):
        dt = datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill trades (gap-fill + dedupe)")
    p.add_argument("--instrument-pk", default="6E", help="Logical id, e.g. 6E")
    p.add_argument("--symbol", default=DEFAULT_CONTINUOUS_SYMBOL, help="Databento symbol")
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--stype-in", default=DEFAULT_STYPE_IN)
    p.add_argument(
        "--start",
        type=_parse_start,
        default=None,
        help="UTC start (default: end-365d). Date-only = that day 00:00",
    )
    p.add_argument(
        "--end",
        type=_parse_end,
        default=None,
        help="UTC end exclusive (default: now). Date-only = next day 00:00",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=ROOT / "database" / "charles_trade.db",
        help="SQLite path",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore coverage_days and refetch every day in range",
    )
    args = p.parse_args()

    result = backfill_trades(
        BackfillRequest(
            instrument_pk=args.instrument_pk,
            symbol=args.symbol,
            dataset=args.dataset,
            stype_in=args.stype_in,
            start=args.start,
            end=args.end,
            db_path=args.db,
            force_refetch_days=args.force,
        )
    )
    print(
        f"summary: requested_days={result.requested_days} "
        f"missing={result.missing_days} fetched={result.fetched_days} "
        f"seen={result.trades_seen} inserted={result.trades_inserted} "
        f"skipped_covered={result.skipped_covered_days}",
        flush=True,
    )


if __name__ == "__main__":
    main()
