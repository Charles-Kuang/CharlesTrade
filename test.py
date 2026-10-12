from charles_trade.data.clients import HistoricalTradesClient
from charles_trade.data.backfill import _default_range

from dotenv import load_dotenv

load_dotenv(".env")


client = HistoricalTradesClient()
print(_default_range(start=None, end=None))
print(client.available_end(dataset="GLBX.MDP3", schema="trades"))