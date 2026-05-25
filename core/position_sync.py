import logging
from typing import Optional

from risk.position_sizing import calc_stop_take

logger = logging.getLogger("multi_strategy")


class PositionSync:
    """Reconcile local trade state with Binance futures positions."""

    def __init__(self, client, executor, db, config: dict):
        self.client = client
        self.executor = executor
        self.db = db
        self.config = config

    async def sync(self, symbols: list[str]) -> Optional[dict]:
        open_trade = await self.db.get_open_trade()
        if open_trade:
            exchange_pos = await self.executor._fetch_exchange_position(open_trade["symbol"])
            if not exchange_pos:
                logger.warning(
                    f"Startup sync: DB open but exchange flat, closing local trade "
                    f"{open_trade['symbol']}"
                )
                await self.db.close_trade(
                    open_trade["symbol"], open_trade["entry_price"], 0.0
                )
                return None
            return self._position_from_trade(open_trade)

        for symbol in symbols:
            exchange_pos = await self.executor._fetch_exchange_position(symbol)
            if not exchange_pos:
                continue
            recovered = await self._recover_external_position(symbol, exchange_pos)
            if recovered:
                return recovered
        return None

    def _position_from_trade(self, trade: dict) -> dict:
        params = {}
        return {
            "symbol": trade["symbol"],
            "quantity": trade["quantity"],
            "entry_price": trade["entry_price"],
            "sl_price": trade["stop_loss_price"],
            "tp_price": trade["take_profit_price"],
            "sl_order_id": None,
            "tp_order_id": None,
            "recovered": True,
            "strategy_params": params,
        }

    async def _recover_external_position(self, symbol: str, exchange_pos: dict) -> Optional[dict]:
        precision = await self.executor._get_precision(symbol)
        quantity = self.executor._round_qty(
            self.executor._position_qty(exchange_pos),
            precision["qty_precision"],
        )
        entry_price = self.executor._position_entry(exchange_pos, 0)
        if quantity <= 0 or entry_price <= 0:
            return None

        strategy = self.config["strategy"]
        atr_pct = strategy.get("recovered_position_atr_pct", strategy.get("atr_pct_threshold", 1.0))
        reward_risk = strategy.get("reward_risk", 3)
        st = calc_stop_take(entry_price, atr_pct, reward_risk)
        sl_price = self.executor._round_price(st["stop_loss"], precision["price_precision"])
        tp_price = self.executor._round_price(st["take_profit"], precision["price_precision"])

        sl_order = await self.executor._place_sl(symbol, quantity, sl_price)
        tp_order = await self.executor._place_tp(symbol, quantity, tp_price) if sl_order else None
        if not sl_order:
            logger.critical(
                f"Startup sync recovered unprotected exchange position but SL attach failed: {symbol}"
            )
            return None

        params = {
            "reward_risk": reward_risk,
            "recovered_external_position": True,
            "manual_action_required": not bool(tp_order),
        }
        await self.db.insert_trade(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            strategy_params=params,
        )
        logger.critical(
            f"Startup sync recovered exchange position: {symbol} "
            f"qty={quantity} entry={entry_price} SL={sl_price} TP={tp_price}"
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
            "recovered": True,
            "strategy_params": params,
        }
