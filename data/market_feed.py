import asyncio
import logging
import time
from typing import Callable, Optional

from binance.async_client import AsyncClient as BinanceAsyncClient
from binance.enums import *

from .kline_cache import KlineCache

logger = logging.getLogger("multi_strategy")


def _parse_kline(raw) -> dict:
    if isinstance(raw, dict):
        return {
            "open_time": int(raw.get("open_time", raw.get("t", 0))),
            "open": float(raw.get("open", raw.get("o", 0))),
            "high": float(raw.get("high", raw.get("h", 0))),
            "low": float(raw.get("low", raw.get("l", 0))),
            "close": float(raw.get("close", raw.get("c", 0))),
            "volume": float(raw.get("volume", raw.get("v", 0))),
            "close_time": int(raw.get("close_time", raw.get("T", 0))),
        }
    return {
        "open_time": int(raw[0]),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "close_time": int(raw[6]),
    }


class MarketFeed:
    def __init__(self, client: BinanceAsyncClient, cache: KlineCache,
                 symbols: list[str], interval: str = "1h",
                 kline_limit: int = 100, proxy_url: str = None):
        self.client = client
        self.cache = cache
        self.symbols = symbols
        self.interval = interval
        self.kline_limit = kline_limit
        self.proxy_url = proxy_url
        self._ws_tasks: list[asyncio.Task] = []
        self._running = False
        self._on_kline_close: Optional[Callable] = None
        self._last_kline_times: dict[str, int] = {}
        self._reconnect_delay = 1.0

    def set_on_kline_close(self, callback: Callable):
        self._on_kline_close = callback

    async def warmup(self):
        for symbol in self.symbols:
            try:
                raw_klines = await self.client.futures_klines(
                    symbol=symbol, interval=self.interval, limit=self.kline_limit
                )
                klines = [_parse_kline(k) for k in raw_klines]
                self.cache.update(symbol, klines)
                if klines:
                    self._last_kline_times[symbol] = klines[-1]["open_time"]
                logger.info(f"Warmup {symbol}: {len(klines)} klines loaded")
            except Exception as e:
                logger.error(f"Warmup failed for {symbol}: {e}")

    async def _backfill(self, symbol: str, since: int):
        try:
            raw = await self.client.futures_klines(
                symbol=symbol, interval=self.interval,
                startTime=since + 1, limit=200
            )
            if raw:
                klines = [_parse_kline(k) for k in raw]
                self.cache.update(symbol, klines)
                if klines:
                    self._last_kline_times[symbol] = klines[-1]["open_time"]
                logger.info(f"Backfill {symbol}: {len(klines)} klines")
        except Exception as e:
            logger.error(f"Backfill failed for {symbol}: {e}")

    async def start(self):
        self._running = True
        await self.warmup()
        for symbol in self.symbols:
            task = asyncio.create_task(self._ws_loop(symbol))
            self._ws_tasks.append(task)

    async def stop(self):
        self._running = False
        for task in self._ws_tasks:
            task.cancel()
        self._ws_tasks.clear()

    async def _ws_loop(self, symbol: str):
        while self._running:
            try:
                await self._ws_connect(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS {symbol} error: {e}")
                if self._running:
                    self._reconnect_delay = min(self._reconnect_delay * 2, 60)
                    logger.info(f"WS {symbol} reconnect in {self._reconnect_delay}s")
                    await asyncio.sleep(self._reconnect_delay)

    async def _ws_connect(self, symbol: str):
        stream = symbol.lower() + f"@kline_{self.interval}"
        url = f"wss://fstream.binance.com/ws/{stream}"

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url, proxy=self.proxy_url
            ) as ws:
                self._reconnect_delay = 1.0
                logger.info(f"WS connected: {symbol}")
                last_time = self._last_kline_times.get(symbol, 0)
                if last_time:
                    await self._backfill(symbol, last_time)

                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_message(symbol, msg.json())
                    elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.ERROR):
                        break

    async def _on_message(self, symbol: str, data: dict):
        try:
            k = data["k"]
            is_closed = k["x"]
            kline = {
                "open_time": int(k["t"]),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "close_time": int(k["T"]),
            }
            existing = self.cache.get_klines(symbol)
            if existing and existing[-1]["open_time"] == kline["open_time"]:
                existing[-1] = kline
            else:
                self.cache.update(symbol, [kline])

            if is_closed and self._on_kline_close:
                prev = self._last_kline_times.get(symbol, 0)
                if kline["open_time"] > prev:
                    self._last_kline_times[symbol] = kline["open_time"]
                    await self._on_kline_close(symbol, kline)
        except Exception as e:
            logger.error(f"WS msg parse error {symbol}: {e}")

    async def fetch_exchange_info(self) -> dict:
        info = await self.client.futures_exchange_info()
        symbols = {}
        for s in info["symbols"]:
            if (s["quoteAsset"] == "USDT"
                    and s["contractType"] == "PERPETUAL"
                    and s["status"] == "TRADING"):
                symbols[s["symbol"]] = {
                    "price_precision": s["pricePrecision"],
                    "qty_precision": s["quantityPrecision"],
                }
        return symbols
