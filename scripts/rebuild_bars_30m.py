"""Rebuild bars_30m cache from trades.

  python scripts/rebuild_bars_30m.py
  python scripts/rebuild_bars_30m.py --instrument 6E
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from charles_trade.chart.bars_30m import rebuild_bars_30m
from charles_trade.data.store import TradeStore

DB = ROOT / "database" / "charles_trade.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild 30m bar cache from trades")
    parser.add_argument("--instrument", default="6E")
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()

    with TradeStore(args.db) as store:
        n_trades = store.trade_count(args.instrument)
        print(f"Rebuilding bars_30m for {args.instrument} from {n_trades} trades…", flush=True)
        n = rebuild_bars_30m(store, args.instrument)
        br = store.bars_30m_range(args.instrument)
    print(f"Wrote {n} bars_30m rows", flush=True)
    if br:
        print(f"Range ts_open {br[0]} → {br[1]}", flush=True)


if __name__ == "__main__":
    main()
