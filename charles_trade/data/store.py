"""SQLite persistence for trades + bars_30m + coverage + ingest_state."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from charles_trade.entities.data.ingest_state import IngestState, IngestStatus
from charles_trade.entities.data.regime import Regime, RegimeLabel
from charles_trade.entities.data.trade import Trade

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_pk TEXT NOT NULL,
    dataset TEXT NOT NULL,
    source TEXT NOT NULL,
    publisher_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    ts_event INTEGER NOT NULL,
    ts_recv INTEGER,
    price INTEGER NOT NULL,
    size INTEGER NOT NULL,
    side TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    flags INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    UNIQUE (
        dataset,
        publisher_id,
        instrument_id,
        ts_event,
        sequence,
        price,
        size,
        side
    )
);

CREATE INDEX IF NOT EXISTS idx_trades_inst_ts
    ON trades (instrument_pk, ts_event);

CREATE TABLE IF NOT EXISTS coverage_days (
    instrument_pk TEXT NOT NULL,
    dataset TEXT NOT NULL,
    day_utc TEXT NOT NULL,
    trade_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_pk, dataset, day_utc)
);

CREATE TABLE IF NOT EXISTS ingest_state (
    instrument_pk TEXT NOT NULL,
    schema TEXT NOT NULL DEFAULT 'trades',
    history_covered_until TEXT,
    live_watermark_ts TEXT,
    last_sequence INTEGER,
    status TEXT NOT NULL DEFAULT 'idle',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (instrument_pk, schema)
);

CREATE TABLE IF NOT EXISTS regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_pk TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    label INTEGER NOT NULL,
    vwap_raw INTEGER,
    first_higher_raw INTEGER,
    first_lower_raw INTEGER,
    volume INTEGER NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_regimes_inst
    ON regimes (instrument_pk, start_ts);

CREATE TABLE IF NOT EXISTS regime_vp (
    regime_id INTEGER NOT NULL,
    price_raw INTEGER NOT NULL,
    buy_vol INTEGER NOT NULL,
    sell_vol INTEGER NOT NULL,
    unknown_vol INTEGER NOT NULL,
    trade_count INTEGER NOT NULL,
    PRIMARY KEY (regime_id, price_raw),
    FOREIGN KEY (regime_id) REFERENCES regimes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_regime_vp_regime
    ON regime_vp (regime_id);

CREATE TABLE IF NOT EXISTS regime_vwap (
    regime_id INTEGER NOT NULL,
    ts_open INTEGER NOT NULL,
    vwap_raw INTEGER NOT NULL,
    first_higher_raw INTEGER NOT NULL,
    first_lower_raw INTEGER NOT NULL,
    volume INTEGER NOT NULL,
    trade_count INTEGER NOT NULL,
    PRIMARY KEY (regime_id, ts_open),
    FOREIGN KEY (regime_id) REFERENCES regimes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_regime_vwap_regime
    ON regime_vwap (regime_id);

CREATE TABLE IF NOT EXISTS bars_30m (
    instrument_pk TEXT NOT NULL,
    ts_open INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (instrument_pk, ts_open)
);

CREATE INDEX IF NOT EXISTS idx_bars_30m_inst_ts
    ON bars_30m (instrument_pk, ts_open);
"""


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


class TradeStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(SCHEMA_SQL)
        self._migrate_regimes()
        self._migrate_regime_label_int()
        self._migrate_drop_trades_raw_symbol()
        self._conn.commit()

    def _migrate_drop_trades_raw_symbol(self) -> None:
        """Remove unused trades.raw_symbol (hist NULL vs live 6E.n.0 was inconsistent)."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(trades)")}
        if "raw_symbol" not in cols:
            return
        print(
            "[store] migrating: DROP COLUMN trades.raw_symbol (may take a bit)…",
            flush=True,
        )
        self._conn.execute("ALTER TABLE trades DROP COLUMN raw_symbol")

    def _migrate_regime_label_int(self) -> None:
        """label: text up/down/range → int codes; drop display-only color column."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(regimes)")}
        if not cols:
            return
        # Convert legacy string labels (and quoted digits) to int codes.
        self._conn.execute(
            """
            UPDATE regimes SET label = CASE CAST(label AS TEXT)
                WHEN 'up' THEN 1
                WHEN 'down' THEN 2
                WHEN 'range' THEN 3
                WHEN '1' THEN 1
                WHEN '2' THEN 2
                WHEN '3' THEN 3
                ELSE label
            END
            """
        )
        if "color" in cols:
            print("[store] migrating: DROP COLUMN regimes.color", flush=True)
            self._conn.execute("ALTER TABLE regimes DROP COLUMN color")

    def _migrate_regimes(self) -> None:
        """Add VWAP columns / regime_vp on existing DBs created before those existed."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(regimes)")}
        alters = [
            ("vwap_raw", "INTEGER"),
            ("first_higher_raw", "INTEGER"),
            ("first_lower_raw", "INTEGER"),
            ("volume", "INTEGER NOT NULL DEFAULT 0"),
            ("trade_count", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, decl in alters:
            if name not in cols:
                self._conn.execute(f"ALTER TABLE regimes ADD COLUMN {name} {decl}")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS regime_vp (
                regime_id INTEGER NOT NULL,
                price_raw INTEGER NOT NULL,
                buy_vol INTEGER NOT NULL,
                sell_vol INTEGER NOT NULL,
                unknown_vol INTEGER NOT NULL,
                trade_count INTEGER NOT NULL,
                PRIMARY KEY (regime_id, price_raw),
                FOREIGN KEY (regime_id) REFERENCES regimes(id) ON DELETE CASCADE
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_vp_regime
                ON regime_vp (regime_id)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS regime_vwap (
                regime_id INTEGER NOT NULL,
                ts_open INTEGER NOT NULL,
                vwap_raw INTEGER NOT NULL,
                first_higher_raw INTEGER NOT NULL,
                first_lower_raw INTEGER NOT NULL,
                volume INTEGER NOT NULL,
                trade_count INTEGER NOT NULL,
                PRIMARY KEY (regime_id, ts_open),
                FOREIGN KEY (regime_id) REFERENCES regimes(id) ON DELETE CASCADE
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_vwap_regime
                ON regime_vwap (regime_id)
            """
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TradeStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def covered_days(self, instrument_pk: str, dataset: str) -> set[str]:
        rows = self._conn.execute(
            """
            SELECT day_utc FROM coverage_days
            WHERE instrument_pk = ? AND dataset = ?
            """,
            (instrument_pk, dataset),
        ).fetchall()
        return {r[0] for r in rows}

    def mark_day_covered(
        self,
        instrument_pk: str,
        dataset: str,
        day: date,
        trade_count: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO coverage_days (instrument_pk, dataset, day_utc, trade_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(instrument_pk, dataset, day_utc) DO UPDATE SET
                trade_count = excluded.trade_count
            """,
            (instrument_pk, dataset, day.isoformat(), trade_count),
        )
        self._conn.commit()

    def insert_trades(self, trades: Iterable[Trade]) -> tuple[int, int]:
        """Insert trades; skip duplicates. Returns (attempted, inserted)."""
        rows = []
        for t in trades:
            rows.append(
                (
                    t.instrument_pk,
                    t.dataset,
                    t.source.value,
                    t.publisher_id,
                    t.instrument_id,
                    t.ts_event,
                    t.ts_recv,
                    t.price,
                    t.size,
                    t.side.value,
                    t.sequence,
                    t.flags,
                    _dt_to_iso(t.ingested_at),
                )
            )

        attempted = len(rows)
        if attempted == 0:
            return 0, 0

        before = self._conn.total_changes
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO trades (
                instrument_pk, dataset, source,
                publisher_id, instrument_id, ts_event, ts_recv,
                price, size, side, sequence, flags, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        inserted = self._conn.total_changes - before
        return attempted, inserted

    def get_ingest_state(self, instrument_pk: str, schema: str = "trades") -> IngestState:
        row = self._conn.execute(
            """
            SELECT history_covered_until, live_watermark_ts, last_sequence, status, updated_at
            FROM ingest_state
            WHERE instrument_pk = ? AND schema = ?
            """,
            (instrument_pk, schema),
        ).fetchone()
        if row is None:
            return IngestState(instrument_pk=instrument_pk, schema=schema)
        return IngestState(
            instrument_pk=instrument_pk,
            schema=schema,
            history_covered_until=_iso_to_dt(row[0]),
            live_watermark_ts=_iso_to_dt(row[1]),
            last_sequence=row[2],
            status=IngestStatus(row[3]),
            updated_at=_iso_to_dt(row[4]) or datetime.now(timezone.utc),
        )

    def save_ingest_state(self, state: IngestState) -> None:
        self._conn.execute(
            """
            INSERT INTO ingest_state (
                instrument_pk, schema, history_covered_until, live_watermark_ts,
                last_sequence, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_pk, schema) DO UPDATE SET
                history_covered_until = excluded.history_covered_until,
                live_watermark_ts = excluded.live_watermark_ts,
                last_sequence = excluded.last_sequence,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                state.instrument_pk,
                state.schema,
                _dt_to_iso(state.history_covered_until),
                _dt_to_iso(state.live_watermark_ts),
                state.last_sequence,
                state.status.value,
                _dt_to_iso(state.updated_at),
            ),
        )
        self._conn.commit()

    def trade_count(self, instrument_pk: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE instrument_pk = ?",
            (instrument_pk,),
        ).fetchone()
        return int(row[0]) if row else 0

    def first_last_price(
        self,
        instrument_pk: str,
        start_ts: int,
        end_ts: int,
    ) -> tuple[float | None, float | None]:
        """First and last trade display prices in half-open [start_ts, end_ts) unix seconds."""
        start_ns = start_ts * 1_000_000_000
        end_ns = end_ts * 1_000_000_000
        first = self._conn.execute(
            """
            SELECT price FROM trades
            WHERE instrument_pk = ? AND ts_event >= ? AND ts_event < ?
            ORDER BY ts_event, sequence LIMIT 1
            """,
            (instrument_pk, start_ns, end_ns),
        ).fetchone()
        last = self._conn.execute(
            """
            SELECT price FROM trades
            WHERE instrument_pk = ? AND ts_event >= ? AND ts_event < ?
            ORDER BY ts_event DESC, sequence DESC LIMIT 1
            """,
            (instrument_pk, start_ns, end_ns),
        ).fetchone()
        if first is None or last is None:
            return None, None
        from charles_trade.entities.data.utils import price_raw_to_float

        return price_raw_to_float(int(first[0])), price_raw_to_float(int(last[0]))

    def list_regimes(self, instrument_pk: str) -> list[Regime]:
        rows = self._conn.execute(
            """
            SELECT id, instrument_pk, start_ts, end_ts, label,
                   vwap_raw, first_higher_raw, first_lower_raw, volume, trade_count
            FROM regimes
            WHERE instrument_pk = ?
            ORDER BY start_ts
            """,
            (instrument_pk,),
        ).fetchall()
        return [
            Regime(
                id=int(r[0]),
                instrument_pk=r[1],
                start_ts=int(r[2]),
                end_ts=int(r[3]),
                label=RegimeLabel(int(r[4])),
                vwap_raw=int(r[5]) if r[5] is not None else None,
                first_higher_raw=int(r[6]) if r[6] is not None else None,
                first_lower_raw=int(r[7]) if r[7] is not None else None,
                volume=int(r[8] or 0),
                trade_count=int(r[9] or 0),
            )
            for r in rows
        ]

    def list_regime_vp(self, regime_id: int) -> list[dict]:
        """Volume profile levels for one regime (display prices)."""
        from charles_trade.entities.data.utils import price_raw_to_float

        rows = self._conn.execute(
            """
            SELECT price_raw, buy_vol, sell_vol, unknown_vol, trade_count
            FROM regime_vp
            WHERE regime_id = ?
            ORDER BY price_raw
            """,
            (regime_id,),
        ).fetchall()
        out = []
        for price_raw, buy_vol, sell_vol, unknown_vol, trade_count in rows:
            buy = int(buy_vol)
            sell = int(sell_vol)
            unk = int(unknown_vol)
            out.append(
                {
                    "price": price_raw_to_float(int(price_raw)),
                    "price_raw": int(price_raw),
                    "buy_vol": buy,
                    "sell_vol": sell,
                    "unknown_vol": unk,
                    "total_vol": buy + sell + unk,
                    "trade_count": int(trade_count),
                }
            )
        return out

    def list_regime_vwap(self, regime_id: int) -> list[dict]:
        """Running VWAP series for one regime (display prices)."""
        from charles_trade.entities.data.utils import price_raw_to_float

        rows = self._conn.execute(
            """
            SELECT ts_open, vwap_raw, first_higher_raw, first_lower_raw, volume, trade_count
            FROM regime_vwap
            WHERE regime_id = ?
            ORDER BY ts_open
            """,
            (regime_id,),
        ).fetchall()
        return [
            {
                "time": int(ts),
                "vwap": price_raw_to_float(int(vwap_raw)),
                "first_higher": price_raw_to_float(int(hi)),
                "first_lower": price_raw_to_float(int(lo)),
                "volume": int(vol),
                "trade_count": int(tc),
            }
            for ts, vwap_raw, hi, lo, vol, tc in rows
        ]

    def insert_regime(self, regime: Regime) -> Regime:
        """Insert one regime + vp/vwap. Returns stored row with id."""
        if regime.end_ts <= regime.start_ts:
            raise ValueError("end_ts must be > start_ts")
        cur = self._conn.execute(
            """
            INSERT INTO regimes (
                instrument_pk, start_ts, end_ts, label,
                vwap_raw, first_higher_raw, first_lower_raw, volume, trade_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                regime.instrument_pk,
                regime.start_ts,
                regime.end_ts,
                int(regime.label),
                regime.vwap_raw,
                regime.first_higher_raw,
                regime.first_lower_raw,
                regime.volume,
                regime.trade_count,
            ),
        )
        regime_id = int(cur.lastrowid)
        if regime.vp_levels:
            self._conn.executemany(
                """
                INSERT INTO regime_vp (
                    regime_id, price_raw, buy_vol, sell_vol, unknown_vol, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        regime_id,
                        lvl.price_raw,
                        lvl.buy_vol,
                        lvl.sell_vol,
                        lvl.unknown_vol,
                        lvl.trade_count,
                    )
                    for lvl in regime.vp_levels
                ],
            )
        if regime.vwap_points:
            self._conn.executemany(
                """
                INSERT INTO regime_vwap (
                    regime_id, ts_open, vwap_raw, first_higher_raw, first_lower_raw,
                    volume, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        regime_id,
                        p.time,
                        p.vwap_raw,
                        p.first_higher_raw,
                        p.first_lower_raw,
                        p.volume,
                        p.trade_count,
                    )
                    for p in regime.vwap_points
                ],
            )
        self._conn.commit()
        rows = self.list_regimes(regime.instrument_pk)
        for r in rows:
            if r.id == regime_id:
                return r
        raise RuntimeError(f"inserted regime {regime_id} not found")

    def delete_regime(self, regime_id: int) -> bool:
        """Delete one regime and cascaded vp/vwap. Returns True if a row was removed."""
        self._conn.execute("DELETE FROM regime_vp WHERE regime_id = ?", (regime_id,))
        self._conn.execute("DELETE FROM regime_vwap WHERE regime_id = ?", (regime_id,))
        cur = self._conn.execute("DELETE FROM regimes WHERE id = ?", (regime_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def replace_regimes(self, instrument_pk: str, regimes: Iterable[Regime]) -> list[Regime]:
        """Full replace for one instrument (regimes + vp + vwap). Returns stored rows."""
        self._conn.execute(
            """
            DELETE FROM regime_vp
            WHERE regime_id IN (
                SELECT id FROM regimes WHERE instrument_pk = ?
            )
            """,
            (instrument_pk,),
        )
        self._conn.execute(
            """
            DELETE FROM regime_vwap
            WHERE regime_id IN (
                SELECT id FROM regimes WHERE instrument_pk = ?
            )
            """,
            (instrument_pk,),
        )
        self._conn.execute(
            "DELETE FROM regimes WHERE instrument_pk = ?",
            (instrument_pk,),
        )
        self._conn.commit()
        for r in regimes:
            if r.instrument_pk != instrument_pk or r.end_ts <= r.start_ts:
                continue
            self.insert_regime(r)
        return self.list_regimes(instrument_pk)

    def upsert_bars_30m(
        self,
        instrument_pk: str,
        bars: Iterable[tuple[int, float, float, float, float, int]],
    ) -> int:
        """Upsert 30m bars. Each row: (ts_open, open, high, low, close, volume)."""
        rows = [
            (instrument_pk, ts, o, h, l, c, vol)
            for ts, o, h, l, c, vol in bars
        ]
        if not rows:
            return 0
        self._conn.executemany(
            """
            INSERT INTO bars_30m (instrument_pk, ts_open, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_pk, ts_open) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def replace_bars_30m(
        self,
        instrument_pk: str,
        bars: Iterable[tuple[int, float, float, float, float, int]],
    ) -> int:
        """Full replace of 30m bars for one instrument."""
        self._conn.execute(
            "DELETE FROM bars_30m WHERE instrument_pk = ?",
            (instrument_pk,),
        )
        return self.upsert_bars_30m(instrument_pk, bars)

    def list_bars_30m(
        self,
        instrument_pk: str,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[tuple[int, float, float, float, float, int]]:
        """Return (ts_open, open, high, low, close, volume) in [start_ts, end_ts)."""
        sql = """
            SELECT ts_open, open, high, low, close, volume
            FROM bars_30m
            WHERE instrument_pk = ?
        """
        params: list[object] = [instrument_pk]
        if start_ts is not None:
            sql += " AND ts_open >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts_open < ?"
            params.append(end_ts)
        sql += " ORDER BY ts_open"
        return [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]))
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def list_bars_30m_tip(
        self,
        instrument_pk: str,
        *,
        limit: int = 5,
    ) -> list[tuple[int, float, float, float, float, int]]:
        """Latest N bars ascending by ts_open."""
        limit = max(1, min(int(limit), 500))
        rows = self._conn.execute(
            """
            SELECT ts_open, open, high, low, close, volume
            FROM bars_30m
            WHERE instrument_pk = ?
            ORDER BY ts_open DESC
            LIMIT ?
            """,
            (instrument_pk, limit),
        ).fetchall()
        out = [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5]))
            for r in rows
        ]
        out.reverse()
        return out

    def bars_30m_count(self, instrument_pk: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM bars_30m WHERE instrument_pk = ?",
            (instrument_pk,),
        ).fetchone()
        return int(row[0]) if row else 0

    def bars_30m_range(self, instrument_pk: str) -> tuple[int, int] | None:
        """(min_ts_open, max_ts_open) or None if empty."""
        row = self._conn.execute(
            """
            SELECT MIN(ts_open), MAX(ts_open) FROM bars_30m
            WHERE instrument_pk = ?
            """,
            (instrument_pk,),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return int(row[0]), int(row[1])
