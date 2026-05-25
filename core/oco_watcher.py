import asyncio
import logging
from typing import Callable, Optional

from binance.async_client import AsyncClient as BinanceAsyncClient

logger = logging.getLogger("multi_strategy")


class OcoWatcher:
    def __init__(self, client: BinanceAsyncClient, on_fill: Callable,
                 poll_interval: int = 30, proxy_url: str = None):
        self.client = client
        self.on_fill = on_fill
        self.poll_interval = poll_interval
        self.proxy_url = proxy_url
        self._position: Optional[dict] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._listen_key: Optional[str] = None

    def set_position(self, position: dict):
        self._position = position

    def clear_position(self):
        self._position = None

    async def start(self):
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()

    async def _ws_loop(self):
        delay = 1.0
        while self._running:
            try:
                self._listen_key = await self.client.futures_stream_get_listen_key()
                url = f"wss://fstream.binance.com/ws/{self._listen_key}"
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        url, proxy=self.proxy_url
                    ) as ws:
                        delay = 1.0
                        logger.info("OCO WS connected (USER_DATA)")
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_ws(msg.json())
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OCO WS error: {e}")
                delay = min(delay * 2, 60)
                await asyncio.sleep(delay)

    async def _handle_ws(self, data: dict):
        if data.get("e") != "ORDER_TRADE_UPDATE":
            return
        o = data.get("o", {})
        symbol = o.get("s")
        order_id = o.get("i")
        status = o.get("X")
        order_type = o.get("o")
        side = o.get("S")

        if status != "FILLED":
            return
        if not self._position or self._position["symbol"] != symbol:
            return

        logger.info(f"OCO WS: {symbol} order {order_id} type={order_type} FILLED")

        exit_price = float(o.get("ap", 0))
        is_sl = order_type in ("STOP_MARKET",)
        is_tp = order_type in ("TAKE_PROFIT_MARKET",)

        if is_sl or is_tp:
            cancel_id = self._position.get(
                "sl_order_id" if is_tp else "tp_order_id"
            )
            if cancel_id:
                await self._cancel_order(symbol, cancel_id)
            await self.on_fill(
                symbol=symbol,
                exit_price=exit_price,
                is_sl=is_sl,
                order_id=order_id,
            )
            self._position = None

    async def _poll_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                if not self._position:
                    continue
                await self._poll_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OCO poll error: {e}")

    async def _poll_check(self):
        pos = self._position
        if not pos:
            return
        symbol = pos["symbol"]
        sl_id = pos.get("sl_order_id")
        tp_id = pos.get("tp_order_id")

        sl_filled = await self._check_filled(symbol, sl_id)
        tp_filled = await self._check_filled(symbol, tp_id)

        if sl_filled and not tp_filled:
            logger.info(f"OCO poll: SL filled, cancelling TP for {symbol}")
            exit_price = await self._get_fill_price(symbol, sl_id)
            await self._cancel_order(symbol, tp_id)
            await self.on_fill(symbol=symbol, exit_price=exit_price,
                               is_sl=True, order_id=sl_id)
            self._position = None
        elif tp_filled and not sl_filled:
            logger.info(f"OCO poll: TP filled, cancelling SL for {symbol}")
            exit_price = await self._get_fill_price(symbol, tp_id)
            await self._cancel_order(symbol, sl_id)
            await self.on_fill(symbol=symbol, exit_price=exit_price,
                               is_sl=False, order_id=tp_id)
            self._position = None

    async def _check_filled(self, symbol: str, order_id: int) -> bool:
        if not order_id:
            return False
        try:
            order = await self.client.futures_get_order(
                symbol=symbol, orderId=order_id
            )
            return order["status"] == "FILLED"
        except Exception:
            return False

    async def _get_fill_price(self, symbol: str, order_id: int) -> float:
        try:
            order = await self.client.futures_get_order(
                symbol=symbol, orderId=order_id
            )
            return float(order.get("avgPrice", 0))
        except Exception:
            return 0.0

    async def _cancel_order(self, symbol: str, order_id: int):
        try:
            await self.client.futures_cancel_order(
                symbol=symbol, orderId=order_id
            )
            logger.info(f"OCO cancelled {order_id} on {symbol}")
        except Exception as e:
            logger.warning(f"OCO cancel {order_id}: {e}")
