"""Map Databento wire records → domain entities."""

from __future__ import annotations

from databento_dbn import TradeMsg

from charles_trade.entities.data.trade import DataSource, Side, Trade


def _side_from_dbn(side: object) -> Side:
    value = getattr(side, "value", side)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    value = str(value)
    if value in ("B", "BID"):
        return Side.BUY
    if value in ("A", "ASK"):
        return Side.SELL
    return Side.NONE


def trade_msg_to_trade(
    msg: TradeMsg,
    *,
    instrument_pk: str,
    dataset: str,
    source: DataSource,
) -> Trade:
    return Trade(
        instrument_pk=instrument_pk,
        dataset=dataset,
        source=source,
        publisher_id=int(msg.publisher_id),
        instrument_id=int(msg.instrument_id),
        ts_event=int(msg.ts_event),
        price=int(msg.price),
        size=int(msg.size),
        side=_side_from_dbn(msg.side),
        sequence=int(msg.sequence),
        ts_recv=int(msg.ts_recv) if getattr(msg, "ts_recv", None) is not None else None,
        flags=int(getattr(msg, "flags", 0) or 0),
    )
