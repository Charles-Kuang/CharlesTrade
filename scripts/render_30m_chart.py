"""Render one year of 6E 30-minute candles to a local HTML chart."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from charles_trade.chart.bars_30m import bars_30m, default_range
from charles_trade.chart.render import render_30m_html
from charles_trade.data.store import TradeStore
from charles_trade.entities.data.instrument import Instrument

DB = ROOT / "database" / "charles_trade.db"
OUT = ROOT / "charts" / "6e_30m.html"


def main() -> None:
    with TradeStore(DB) as store:
        if store.bars_30m_count("6E") == 0:
            raise SystemExit("no bars_30m for 6E; run: python scripts/rebuild_bars_30m.py")
        start, end = default_range(store, "6E")
        print(f"range {start.isoformat()} → {end.isoformat()}", flush=True)
        bars = bars_30m(store, instrument_pk="6E", start=start, end=end)
        regimes = store.list_regimes("6E")
    title = f"6E 30m  |  New York  |  {len(bars)} bars"
    path = render_30m_html(
        bars,
        title=title,
        path=OUT,
        instrument_pk="6E",
        regimes=regimes,
        api_base="",
        tick_size=Instrument(instrument_pk="6E").tick_size,
    )
    print(f"wrote {path}", flush=True)
    print("For draw/save: python scripts/serve_chart.py", flush=True)


if __name__ == "__main__":
    main()
