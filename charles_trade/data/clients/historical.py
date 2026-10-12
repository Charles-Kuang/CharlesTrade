"""Databento Historical client helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

import databento
from databento_dbn import TradeMsg

from charles_trade.data.utils.gaps import ensure_utc
from charles_trade.data.utils.mapper import trade_msg_to_trade
from charles_trade.entities.data.trade import DataSource, Trade


class HistoricalTradesClient:
    def __init__(self, client: databento.Historical | None = None) -> None:
        self._client = client or databento.Historical()

    def available_end(self, dataset: str, schema: str = "trades") -> datetime:
        """Latest timestamp Historical API can serve for this dataset/schema."""
        info = self._client.metadata.get_dataset_range(dataset=dataset)
        if isinstance(info, dict):
            schema_info = (info.get("schema") or {}).get(schema) or {}
            end = schema_info.get("end") or info.get("end")
        else:
            end = getattr(info, "end")
        if isinstance(end, datetime):
            return ensure_utc(end)
        return ensure_utc(datetime.fromisoformat(str(end).replace("Z", "+00:00")))

    def fetch_trades(
        self,
        *,
        dataset: str,
        symbol: str,
        stype_in: str,
        start: datetime,
        end: datetime,
        instrument_pk: str,
    ) -> list[Trade]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        if end <= start:
            return []

        store = self._client.timeseries.get_range(
            dataset=dataset,
            schema="trades",
            symbols=[symbol],
            stype_in=stype_in,
            start=start,
            end=end,
        )

        trades: list[Trade] = []
        for record in store:
            if not isinstance(record, TradeMsg):
                continue
            trades.append(
                trade_msg_to_trade(
                    record,
                    instrument_pk=instrument_pk,
                    dataset=dataset,
                    source=DataSource.HISTORICAL,
                )
            )
        return trades
