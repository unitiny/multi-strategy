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
                    "max_qty": 0,
                    "market_max_qty": 0,
                    "min_notional": 0,
                }
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        prec["min_qty"] = float(f["minQuantity"])
                        prec["max_qty"] = float(f.get("maxQuantity", 0) or 0)
                    elif f["filterType"] == "MARKET_LOT_SIZE":
                        prec["market_max_qty"] = float(f.get("maxQty", 0) or 0)
                    elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                        prec["min_notional"] = float(
                            f.get("notional", f.get("minNotional", 0)) or 0
                        )
                self._precision_cache[symbol] = prec
                return prec
        return {
            "price_precision": 2, "qty_precision": 3, "min_qty": 0.001,
            "max_qty": 0, "market_max_qty": 0, "min_notional": 0,
        }

    def _round_price(self, price: float, precision: int) -> float:
        return round(price, precision)

    def _round_qty(self, qty: float, precision: int) -> float:
        return round(qty, precision)

    def _position_qty(self, position: dict) -> float:
        if not position:
            return 0.0
        for key in ("positionAmt", "position_amt", "contracts"):
            try:
                return abs(float(position.get(key, 0) or 0))
            except (TypeError, ValueError):
                continue
        return 0.0

    def _position_entry(self, position: dict, fallback: float) -> float:
        if not position:
            return fallback
        for key in ("entryPrice", "entry_price", "avgPrice"):
            try:
                value = float(position.get(key, 0) or 0)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return fallback

    async def _fetch_exchange_position(self, symbol: str) -> Optional[dict]:
        try:
            positions = await self.client.futures_position_information(symbol=symbol)
        except TypeError:
            positions = await self.client.futures_position_information()
        for pos in positions or []:
            if pos.get("symbol") == symbol and self._position_qty(pos) > 0:
                return pos
        return None

    async def _can_open(self, symbol: str, db) -> bool:
        open_trade = await db.get_open_trade()
        if open_trade:
            logger.warning(
                f"Open rejected: DB already has open trade "
                f"{open_trade.get('symbol', symbol)}"
            )
            return False
        exchange_pos = await self._fetch_exchange_position(symbol)
        if exchange_pos:
            logger.warning(
                f"Open rejected: exchange already has position {symbol} "
                f"qty={self._position_qty(exchange_pos)}"
            )
            return False
        return True

    def _is_untradable_remainder(self, qty: float, price: float, precision: dict) -> bool:
        if qty <= 0:
            return True
        if precision.get("min_qty") and qty < precision["min_qty"]:
            return True
        if precision.get("min_notional") and price > 0:
            return qty * price < precision["min_notional"]
        return False

    def _split_quantity(self, quantity: float, max_qty: float) -> list[float]:
        if quantity <= 0:
            return []
        if not max_qty or max_qty <= 0 or quantity <= max_qty:
            return [quantity]
        chunks = []
        remaining = quantity
        while remaining > 0:
            chunk = min(remaining, max_qty)
            chunks.append(chunk)
            remaining -= chunk
        return chunks

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

        if not await self._can_open(symbol, db):
            return None

        pos = calc_position(
            strategy["fixed_loss"], signal.atr_pct,
            signal.close, strategy["leverage"]
        )
        precision = await self._get_precision(symbol)
        quantity = self._round_qty(pos["quantity"], precision["qty_precision"])
        if self._is_untradable_remainder(quantity, signal.close, precision):
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
        exchange_pos = await self._fetch_exchange_position(symbol)
        if exchange_pos:
            exchange_qty = self._position_qty(exchange_pos)
            exchange_entry = self._position_entry(exchange_pos, entry_price)
            if exchange_qty > 0:
                if abs(exchange_qty - quantity) > max(quantity * 1e-6, 1e-9):
                    logger.critical(
                        "Open fill differs from exchange position: "
                        f"{symbol} order_filled={quantity:.8f} "
                        f"exchange_qty={exchange_qty:.8f}"
                    )
                quantity = self._round_qty(exchange_qty, precision["qty_precision"])
                entry_price = exchange_entry

        st = calc_stop_take(entry_price, signal.atr_pct, signal.params["reward_risk"])
        sl_price = self._round_price(st["stop_loss"], precision["price_precision"])
        tp_price = self._round_price(st["take_profit"], precision["price_precision"])

        sl_order = await self._place_sl(symbol, quantity, sl_price)
        tp_order = await self._place_tp(symbol, quantity, tp_price)

        if not sl_order:
            logger.error(f"Failed to set SL for {symbol}, emergency closing position")
            await self.close_position_market(symbol, quantity, precision)
            return None
        if not tp_order:
            logger.warning(f"Failed to set TP for {symbol}; SL-only protection remains")

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
            "tp_order_id": tp_order["orderId"] if tp_order else None,
            "sl_order_ids": sl_order.get("orderIds", [sl_order["orderId"]]),
            "tp_order_ids": tp_order.get("orderIds", [tp_order["orderId"]]) if tp_order else [],
        }

    async def _place_sl(self, symbol: str, quantity: float, stop_price: float):
        return await self._place_protective_split(
            symbol=symbol,
            quantity=quantity,
            order_type="STOP_MARKET",
            stop_price=stop_price,
            label="SL",
        )

    async def _place_tp(self, symbol: str, quantity: float, stop_price: float):
        return await self._place_protective_split(
            symbol=symbol,
            quantity=quantity,
            order_type="TAKE_PROFIT_MARKET",
            stop_price=stop_price,
            label="TP",
        )

    async def _place_protective_split(self, symbol: str, quantity: float,
                                      order_type: str, stop_price: float,
                                      label: str):
        try:
            precision = await self._get_precision(symbol)
            max_qty = precision.get("market_max_qty") or precision.get("max_qty") or 0
            chunks = [
                self._round_qty(q, precision["qty_precision"])
                for q in self._split_quantity(quantity, max_qty)
                if not self._is_untradable_remainder(q, 0, {**precision, "min_notional": 0})
            ]
            orders = []
            for chunk in chunks:
                orders.append(await self.client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type=order_type,
                    quantity=chunk,
                    stopPrice=stop_price,
                    reduceOnly=True,
                ))
            if not orders:
                return None
            return {
                "orderId": orders[0]["orderId"],
                "orderIds": [o["orderId"] for o in orders],
                "orders": orders,
            }
        except Exception as e:
            logger.error(f"{label} order failed {symbol}: {e}")
            return None

    async def close_position_market(self, symbol: str, quantity: float,
                                    precision: dict = None) -> Optional[dict]:
        precision = precision or await self._get_precision(symbol)
        max_qty = precision.get("market_max_qty") or precision.get("max_qty") or 0
        chunks = [
            self._round_qty(q, precision["qty_precision"])
            for q in self._split_quantity(quantity, max_qty)
            if not self._is_untradable_remainder(q, 0, {**precision, "min_notional": 0})
        ]
        if not chunks:
            return None

        orders = []
        for chunk in chunks:
            order = await self.client.futures_create_order(
                symbol=symbol, side="SELL", type="MARKET",
                quantity=chunk, reduceOnly=True,
            )
            orders.append(order)
        return {
            "orderId": orders[-1].get("orderId"),
            "status": "FILLED",
            "filled": sum(chunks),
            "orders": orders,
        }

    async def cleanup_orphaned_protective_orders(self, active_symbols: set[str]) -> int:
        open_orders = await self.client.futures_get_open_orders()
        active = {s.upper() for s in active_symbols if s}
        cancelled = 0
        for order in open_orders or []:
            symbol = order.get("symbol")
            if not symbol or symbol.upper() in active:
                continue
            order_type = str(order.get("type", "")).upper()
            reduce_only = str(order.get("reduceOnly", order.get("closePosition", ""))).lower() == "true"
            protective = any(t in order_type for t in ("STOP", "TAKE_PROFIT", "TRAILING"))
            if not (reduce_only or protective):
                continue
            await self.client.futures_cancel_order(
                symbol=symbol, orderId=order["orderId"]
            )
            cancelled += 1
        return cancelled

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
