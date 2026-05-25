import asyncio
import logging
from typing import Optional

from binance.async_client import AsyncClient as BinanceAsyncClient
from binance.enums import *

from risk.position_sizing import calc_position, calc_stop_take

logger = logging.getLogger("multi_strategy")


class Executor:
    def __init__(self, client: BinanceAsyncClient, config: dict):
        self.client = client
        self.config = config
        self._precision_cache: dict = {}

    async def _get_precision(self, symbol: str) -> dict:
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        info = await self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                prec = {
                    "price_precision": s["pricePrecision"],
                    "qty_precision": s["quantityPrecision"],
                    "min_qty": 0,
                }
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        prec["min_qty"] = float(f["minQuantity"])
                        break
                self._precision_cache[symbol] = prec
                return prec
        return {"price_precision": 2, "qty_precision": 3, "min_qty": 0.001}

    def _round_price(self, price: float, precision: int) -> float:
        return round(price, precision)

    def _round_qty(self, qty: float, precision: int) -> float:
        return round(qty, precision)

    async def set_leverage(self, symbol: str, leverage: int):
        try:
            await self.client.futures_change_leverage(
                symbol=symbol, leverage=leverage
            )
            logger.info(f"Leverage set: {symbol} x{leverage}")
        except Exception as e:
            logger.warning(f"Set leverage {symbol}: {e}")

    async def execute_open(self, signal, db) -> Optional[dict]:
        strategy = self.config["strategy"]
        params = signal.params
        symbol = signal.symbol

        pos = calc_position(
            strategy["fixed_loss"], signal.atr_pct,
            signal.close, strategy["leverage"]
        )
        precision = await self._get_precision(symbol)
        quantity = self._round_qty(pos["quantity"], precision["qty_precision"])
        if quantity <= 0:
            logger.warning(f"Quantity too small for {symbol}: {quantity}")
            return None

        await self.set_leverage(symbol, strategy["leverage"])

        logger.info(f"Opening LIMIT {symbol} qty={quantity}")

        try:
            limit_order = await self.client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="LIMIT",
                quantity=quantity,
                price=self._round_price(signal.close, precision["price_precision"]),
                timeInForce="GTC",
            )
        except Exception as e:
            logger.error(f"Limit order failed: {e}, falling back to MARKET")
            return await self._market_open(symbol, quantity, signal, precision, db)

        order_id = limit_order["orderId"]
        filled = False
        for _ in range(strategy["limit_order_timeout_sec"]):
            await asyncio.sleep(1)
            try:
                status = await self.client.futures_get_order(
                    symbol=symbol, orderId=order_id
                )
                if status["status"] == "FILLED":
                    filled = True
                    entry_price = float(status["avgPrice"])
                    break
            except Exception:
                pass

        if not filled:
            try:
                await self.client.futures_cancel_order(
                    symbol=symbol, orderId=order_id
                )
                logger.info(f"LIMIT timeout, cancelled {symbol}")
            except Exception:
                pass
            return await self._market_open(symbol, quantity, signal, precision, db)

        entry_price = float(
            (await self.client.futures_get_order(symbol=symbol, orderId=order_id))
            .get("avgPrice", signal.close)
        )
        return await self._finalize_open(
            symbol, quantity, entry_price, signal, precision, db
        )

    async def _market_open(self, symbol, quantity, signal, precision, db):
        logger.info(f"Opening MARKET {symbol} qty={quantity}")
        try:
            order = await self.client.futures_create_order(
                symbol=symbol, side="BUY", type="MARKET", quantity=quantity
            )
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            return None

        await asyncio.sleep(0.5)
        status = await self.client.futures_get_order(
            symbol=symbol, orderId=order["orderId"]
        )
        entry_price = float(status.get("avgPrice", signal.close))
        return await self._finalize_open(
            symbol, quantity, entry_price, signal, precision, db
        )

    async def _finalize_open(self, symbol, quantity, entry_price,
                             signal, precision, db):
        st = calc_stop_take(entry_price, signal.atr_pct, signal.params["reward_risk"])
        sl_price = self._round_price(st["stop_loss"], precision["price_precision"])
        tp_price = self._round_price(st["take_profit"], precision["price_precision"])

        sl_order = await self._place_sl(symbol, quantity, sl_price)
        tp_order = await self._place_tp(symbol, quantity, tp_price)

        if not sl_order or not tp_order:
            logger.error(f"Failed to set SL/TP for {symbol}, closing position")
            await self.client.futures_create_order(
                symbol=symbol, side="SELL", type="MARKET", quantity=quantity
            )
            return None

        await db.insert_trade(
            symbol=symbol, side="BUY", quantity=quantity,
            entry_price=entry_price, stop_loss_price=sl_price,
            take_profit_price=tp_price, strategy_params=signal.params
        )

        logger.info(
            f"Opened {symbol}: qty={quantity} entry={entry_price} "
            f"SL={sl_price} TP={tp_price}"
        )
        return {
            "symbol": symbol,
            "quantity": quantity,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": sl_order["orderId"],
            "tp_order_id": tp_order["orderId"],
        }

    async def _place_sl(self, symbol: str, quantity: float, stop_price: float):
        try:
            return await self.client.futures_create_order(
                symbol=symbol, side="SELL", type="STOP_MARKET",
                stopPrice=stop_price, closePosition="true"
            )
        except Exception as e:
            logger.error(f"SL order failed {symbol}: {e}")
            return None

    async def _place_tp(self, symbol: str, quantity: float, stop_price: float):
        try:
            return await self.client.futures_create_order(
                symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                stopPrice=stop_price, closePosition="true"
            )
        except Exception as e:
            logger.error(f"TP order failed {symbol}: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: int):
        try:
            await self.client.futures_cancel_order(
                symbol=symbol, orderId=order_id
            )
            logger.info(f"Cancelled order {order_id} on {symbol}")
        except Exception as e:
            logger.warning(f"Cancel order {order_id}: {e}")

    async def cancel_open_limit(self, symbol: str):
        try:
            open_orders = await self.client.futures_get_open_orders(symbol=symbol)
            for o in open_orders:
                if o["type"] == "LIMIT":
                    await self.client.futures_cancel_order(
                        symbol=symbol, orderId=o["orderId"]
                    )
                    logger.info(f"Graceful shutdown: cancelled LIMIT {o['orderId']}")
        except Exception as e:
            logger.warning(f"Cancel open limits: {e}")
