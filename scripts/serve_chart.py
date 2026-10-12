"""Local chart server: 30m candles + regime draw/save + SSE live tip.

  python scripts/serve_chart.py
  open http://127.0.0.1:8765/

On each start: historical backfill → Live (replay+realtime) → serve.
Live flush → SSE /api/events → browser fetches /api/bars tip.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from charles_trade.chart.bars_30m import bars_30m, default_range
from charles_trade.chart.render import render_30m_html
from charles_trade.chart.update_hub import UpdateHub
from charles_trade.data.backfill import BackfillRequest, backfill_trades
from charles_trade.data.live import LiveRequest, live_flush_settings_from_env, run_live_trades
from charles_trade.data.store import TradeStore
from charles_trade.entities.data.instrument import Instrument
from charles_trade.entities.data.regime import Regime
from charles_trade.entities.data.utils import (
    DEFAULT_CONTINUOUS_SYMBOL,
    DEFAULT_DATASET,
    DEFAULT_STYPE_IN,
)
from charles_trade.regime.classify import classify_regime

DB = ROOT / "database" / "charles_trade.db"
OUT = ROOT / "charts" / "6e_30m.html"
HOST = "127.0.0.1"
PORT = 8765
INSTRUMENT = "6E"

UPDATE_HUB = UpdateHub()
_LIVE_STOP = threading.Event()
_LIVE_SESSION: dict = {}
_LIVE_THREAD: threading.Thread | None = None
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def _build_one_regime(
    store: TradeStore, instrument_pk: str, start_ts: int, end_ts: int
) -> Regime:
    from datetime import datetime, timezone

    from charles_trade.entities.data.regime import RegimeLabel, RegimeVpLevel, RegimeVwapPoint
    from charles_trade.indicators.volume_profile import volume_profile
    from charles_trade.indicators.vwap import vwap

    if end_ts <= start_ts:
        raise ValueError("end_ts must be > start_ts")
    inst = Instrument(instrument_pk=instrument_pk)
    open_px, close_px = store.first_last_price(instrument_pk, start_ts, end_ts)
    if open_px is None or close_px is None:
        label = RegimeLabel.RANGE
    else:
        label = classify_regime(
            open_px,
            close_px,
            tick_size=inst.tick_size,
            trend_ticks=inst.regime_trend_ticks,
        )
    start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc)
    points = vwap(store, instrument_pk=instrument_pk, start=start, end=end)
    levels = volume_profile(store, instrument_pk=instrument_pk, start=start, end=end)
    last = points[-1] if points else None
    return Regime(
        instrument_pk=instrument_pk,
        start_ts=start_ts,
        end_ts=end_ts,
        label=label,
        vwap_raw=last.vwap_raw if last else None,
        first_higher_raw=last.first_higher_raw if last else None,
        first_lower_raw=last.first_lower_raw if last else None,
        volume=last.volume if last else 0,
        trade_count=last.trade_count if last else 0,
        vp_levels=tuple(
            RegimeVpLevel(
                price_raw=lv.price_raw,
                buy_vol=lv.buy_vol,
                sell_vol=lv.sell_vol,
                unknown_vol=lv.unknown_vol,
                trade_count=lv.trade_count,
            )
            for lv in levels
        ),
        vwap_points=tuple(
            RegimeVwapPoint(
                time=p.time,
                vwap_raw=p.vwap_raw,
                first_higher_raw=p.first_higher_raw,
                first_lower_raw=p.first_lower_raw,
                volume=p.volume,
                trade_count=p.trade_count,
            )
            for p in points
        ),
    )


def rebuild_html(api_base: str) -> Path:
    inst = Instrument(instrument_pk=INSTRUMENT)
    with TradeStore(DB) as store:
        if store.bars_30m_count(INSTRUMENT) == 0:
            raise RuntimeError(
                f"no bars_30m for {INSTRUMENT}; run: python scripts/rebuild_bars_30m.py"
            )
        start, end = default_range(store, INSTRUMENT)
        bars = bars_30m(store, instrument_pk=INSTRUMENT, start=start, end=end)
        regimes = store.list_regimes(INSTRUMENT)
    title = f"{INSTRUMENT} 30m  |  New York  |  {len(bars)} bars"
    return render_30m_html(
        bars,
        title=title,
        path=OUT,
        instrument_pk=INSTRUMENT,
        regimes=regimes,
        api_base=api_base,
        tick_size=inst.tick_size,
    )


class Handler(BaseHTTPRequestHandler):
    # Quiet client disconnects (refresh / close tab / SSE teardown on Windows).
    def handle(self) -> None:
        try:
            super().handle()
        except _CLIENT_GONE:
            pass

    def log_message(self, fmt: str, *args) -> None:
        print(f"[chart] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except _CLIENT_GONE:
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _sse_bars_updated(self) -> None:
        """Server-Sent Events: push when Live flush publishes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_seq = UPDATE_HUB.seq
        try:
            # hello so client knows the stream is up
            hello = json.dumps({"type": "hello", "seq": last_seq})
            self.wfile.write(f"event: hello\ndata: {hello}\n\n".encode("utf-8"))
            self.wfile.flush()

            while not _LIVE_STOP.is_set():
                seq, payload = UPDATE_HUB.wait_after(last_seq, timeout=20.0)
                if seq > last_seq and payload is not None:
                    last_seq = seq
                    body = json.dumps({"type": "bars_updated", **payload})
                    self.wfile.write(
                        f"event: bars_updated\ndata: {body}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                else:
                    # keepalive comment (EventSource ignores)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except _CLIENT_GONE:
            return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/chart"):
            api_base = f"http://{HOST}:{PORT}"
            path = rebuild_html(api_base)
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/events":
            self._sse_bars_updated()
            return
        if parsed.path == "/api/bars":
            qs = parse_qs(parsed.query)
            instrument_pk = qs.get("instrument_pk", [INSTRUMENT])[0]
            try:
                tip = int(qs.get("tip", ["5"])[0])
            except ValueError:
                tip = 5
            with TradeStore(DB) as store:
                rows = store.list_bars_30m_tip(instrument_pk, limit=tip)
            bars = [
                {
                    "time": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": vol,
                }
                for ts, o, h, l, c, vol in rows
            ]
            self._json(200, {"instrument_pk": instrument_pk, "bars": bars})
            return
        if parsed.path == "/api/regimes":
            qs = parse_qs(parsed.query)
            instrument_pk = qs.get("instrument_pk", [INSTRUMENT])[0]
            with TradeStore(DB) as store:
                regimes = [r.to_dict() for r in store.list_regimes(instrument_pk)]
            self._json(200, regimes)
            return
        if parsed.path == "/api/regime_vp":
            qs = parse_qs(parsed.query)
            try:
                regime_id = int(qs.get("regime_id", [""])[0])
            except ValueError:
                self._json(400, {"error": "regime_id required"})
                return
            with TradeStore(DB) as store:
                levels = store.list_regime_vp(regime_id)
            self._json(200, levels)
            return
        if parsed.path == "/api/regime_vwap":
            qs = parse_qs(parsed.query)
            try:
                regime_id = int(qs.get("regime_id", [""])[0])
            except ValueError:
                self._json(400, {"error": "regime_id required"})
                return
            with TradeStore(DB) as store:
                points = store.list_regime_vwap(regime_id)
            self._json(200, points)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/regimes":
            self._send(404, b"not found", "text/plain")
            return
        qs = parse_qs(parsed.query)
        instrument_pk = qs.get("instrument_pk", [INSTRUMENT])[0]
        length = int(self.headers.get("Content-Length", "0"))
        raw = json.loads(self.rfile.read(length).decode("utf-8"))
        try:
            start_ts = int(raw["start_ts"])
            end_ts = int(raw["end_ts"])
        except (KeyError, TypeError, ValueError):
            self._json(400, {"error": "body must be {start_ts, end_ts}"})
            return
        try:
            with TradeStore(DB) as store:
                built = _build_one_regime(store, instrument_pk, start_ts, end_ts)
                saved = store.insert_regime(built)
            self._json(200, saved.to_dict())
        except ValueError as exc:
            self._json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        regime_id: int | None = None
        if parsed.path.startswith("/api/regimes/"):
            tail = parsed.path[len("/api/regimes/") :].strip("/")
            try:
                regime_id = int(tail)
            except ValueError:
                self._json(400, {"error": "invalid regime id"})
                return
        elif parsed.path == "/api/regimes":
            try:
                regime_id = int(qs.get("id", [""])[0])
            except ValueError:
                self._json(400, {"error": "id required"})
                return
        else:
            self._send(404, b"not found", "text/plain")
            return
        with TradeStore(DB) as store:
            ok = store.delete_regime(regime_id)
        if not ok:
            self._json(404, {"error": "regime not found"})
            return
        self._json(200, {"ok": True, "id": regime_id})


class ChartServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        err = sys.exc_info()[1]
        if isinstance(err, _CLIENT_GONE):
            return
        super().handle_error(request, client_address)


def run_startup_backfill() -> None:
    """Gap-fill historical trades before serving the chart."""
    print("Startup backfill…", flush=True)
    result = backfill_trades(
        BackfillRequest(
            instrument_pk=INSTRUMENT,
            symbol=DEFAULT_CONTINUOUS_SYMBOL,
            dataset=DEFAULT_DATASET,
            stype_in=DEFAULT_STYPE_IN,
            start=None,  # end - 365d
            end=None,  # now UTC, clipped to available_end
            db_path=DB,
            force_refetch_days=False,
        )
    )
    print(
        f"Startup backfill done: fetched_days={result.fetched_days} "
        f"inserted={result.trades_inserted} "
        f"skipped_covered={result.skipped_covered_days} "
        f"available_end={result.available_end.isoformat()}",
        flush=True,
    )


def start_live_ingest() -> None:
    """Background Live session with auto-reconnect until stop_live_ingest()."""
    global _LIVE_THREAD
    _LIVE_STOP.clear()
    batch_size, flush_interval = live_flush_settings_from_env()

    def _on_flush(info: dict) -> None:
        UPDATE_HUB.publish(info)

    def _run() -> None:
        backoff = 3.0
        while not _LIVE_STOP.is_set():
            try:
                run_live_trades(
                    LiveRequest(
                        instrument_pk=INSTRUMENT,
                        symbol=DEFAULT_CONTINUOUS_SYMBOL,
                        dataset=DEFAULT_DATASET,
                        stype_in=DEFAULT_STYPE_IN,
                        db_path=DB,
                        batch_size=batch_size,
                        flush_interval_sec=flush_interval,
                        on_flush=_on_flush,
                        session=_LIVE_SESSION,
                    ),
                    stop_event=_LIVE_STOP,
                )
            except Exception as exc:
                print(f"[live] session crashed: {exc}", flush=True)
            if _LIVE_STOP.is_set():
                break
            print(
                f"[live] disconnected; reconnecting in {backoff:.0f}s…",
                flush=True,
            )
            if _LIVE_STOP.wait(timeout=backoff):
                break

    _LIVE_THREAD = threading.Thread(target=_run, name="live-ingest", daemon=True)
    _LIVE_THREAD.start()


def stop_live_ingest(*, join_timeout: float = 8.0) -> None:
    """Signal Live to exit and flush remaining buffer (Ctrl+C path)."""
    _LIVE_STOP.set()
    client = _LIVE_SESSION.get("client")
    if client is not None:
        try:
            client.stop()
        except Exception as exc:
            print(f"[live] client.stop: {exc}", flush=True)
    t = _LIVE_THREAD
    if t is not None and t.is_alive():
        t.join(timeout=join_timeout)
        if t.is_alive():
            print("[live] join timed out; buffer flush may be incomplete", flush=True)
        else:
            print("[live] stopped cleanly (final flush done)", flush=True)


def main() -> None:
    run_startup_backfill()
    start_live_ingest()
    api_base = f"http://{HOST}:{PORT}"
    rebuild_html(api_base)
    server = ChartServer((HOST, PORT), Handler)

    def _shutdown(signum, frame) -> None:
        print("\nShutting down…", flush=True)
        stop_live_ingest()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    print(f"Chart: {api_base}/", flush=True)
    print("SSE: Live flush → /api/events → /api/bars tip", flush=True)
    print("Draw regime: toolbar button → two candle clicks → Save", flush=True)
    try:
        server.serve_forever()
    finally:
        stop_live_ingest()
        server.server_close()


if __name__ == "__main__":
    main()
