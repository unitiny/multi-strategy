"""Regression tests for live trading safety rails."""
import asyncio
import os
import tempfile

from core.executor import Executor
from core.position_sync import PositionSync
from data.database import Database


class DummySignal:
    symbol = "TESTUSDT"
    atr_pct = 1.0
    close = 100.0
    params = {"reward_risk": 3}


class FakeClient:
    def __init__(self):
        self.created_orders = []
        self.cancelled_orders = []
        self.positions = []
        self.exchange_info = {
            "symbols": [{
                "symbol": "TESTUSDT",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "LOT_SIZE", "minQuantity": "0.001", "maxQuantity": "5"},
                    {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "maxQty": "5"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }]
        }
        self.order_status = {}
        self.fail_sl = False

    async def futures_exchange_info(self):
        return self.exchange_info

    async def futures_change_leverage(self, symbol, leverage):
        return {"symbol": symbol, "leverage": leverage}

    async def futures_position_information(self, symbol=None):
        if symbol:
            return [p for p in self.positions if p["symbol"] == symbol]
        return self.positions

    async def futures_create_order(self, **kwargs):
        if kwargs["type"] == "STOP_MARKET" and self.fail_sl:
            raise RuntimeError("SL rejected")
        order_id = len(self.created_orders) + 1
        order = {"orderId": order_id, "status": "FILLED", "avgPrice": "100", **kwargs}
        self.created_orders.append(order)
        self.order_status[order_id] = order
        if kwargs["type"] in ("LIMIT", "MARKET") and kwargs["side"] == "BUY":
            self.positions = [{
                "symbol": kwargs["symbol"],
                "positionAmt": "0.25",
                "entryPrice": "101.5",
            }]
        return order

    async def futures_get_order(self, symbol, orderId):
        return self.order_status[orderId]

    async def futures_cancel_order(self, symbol, orderId):
        self.cancelled_orders.append((symbol, orderId))
        return {"orderId": orderId, "status": "CANCELED"}

    async def futures_get_open_orders(self, symbol=None):
        return [
            {"symbol": "TESTUSDT", "orderId": 91, "type": "STOP_MARKET", "reduceOnly": True},
            {"symbol": "OTHERUSDT", "orderId": 92, "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
        ]


async def with_db(fn):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    await db.init()
    try:
        return await fn(db)
    finally:
        await db.close()
        os.unlink(db_path)


def config():
    return {
        "strategy": {
            "fixed_loss": 0.21,
            "leverage": 3,
            "limit_order_timeout_sec": 0,
        },
        "risk": {
            "max_daily_stops": 2,
            "total_asset_target": 22.5,
            "rebalance_threshold_pct": 20,
        },
    }


async def test_rejects_duplicate_open_from_db():
    async def run(db):
        await db.insert_trade("TESTUSDT", "BUY", 1, 100, 99, 103)
        client = FakeClient()
        executor = Executor(client, config())
        result = await executor.execute_open(DummySignal(), db)
        assert result is None
        assert client.created_orders == []
    await with_db(run)


async def test_rejects_duplicate_open_from_exchange():
    async def run(db):
        client = FakeClient()
        client.positions = [{"symbol": "TESTUSDT", "positionAmt": "1.5", "entryPrice": "100"}]
        executor = Executor(client, config())
        result = await executor.execute_open(DummySignal(), db)
        assert result is None
        assert client.created_orders == []
    await with_db(run)


async def test_open_uses_exchange_quantity_and_entry_after_fill():
    async def run(db):
        client = FakeClient()
        executor = Executor(client, config())
        result = await executor.execute_open(DummySignal(), db)
        assert result["quantity"] == 0.25
        assert result["entry_price"] == 101.5
        trade = await db.get_open_trade()
        assert trade["quantity"] == 0.25
        assert trade["entry_price"] == 101.5
    await with_db(run)


async def test_failed_stop_loss_triggers_reduce_only_emergency_close_without_db_open():
    async def run(db):
        client = FakeClient()
        client.fail_sl = True
        executor = Executor(client, config())
        result = await executor.execute_open(DummySignal(), db)
        assert result is None
        assert any(o["side"] == "SELL" and o["type"] == "MARKET" and o.get("reduceOnly")
                   for o in client.created_orders)
        assert await db.get_open_trade() is None
    await with_db(run)


async def test_reduce_only_market_close_splits_by_exchange_max_quantity():
    client = FakeClient()
    executor = Executor(client, config())
    precision = await executor._get_precision("TESTUSDT")
    result = await executor.close_position_market("TESTUSDT", 12.0, precision)
    chunks = [o["quantity"] for o in client.created_orders if o["type"] == "MARKET"]
    assert chunks == [5.0, 5.0, 2.0]
    assert result["filled"] == 12.0


async def test_cleanup_orphaned_protective_orders_skips_active_symbol():
    client = FakeClient()
    executor = Executor(client, config())
    cancelled = await executor.cleanup_orphaned_protective_orders({"TESTUSDT"})
    assert cancelled == 1
    assert client.cancelled_orders == [("OTHERUSDT", 92)]


async def test_startup_sync_recovers_exchange_position_and_attaches_protection():
    async def run(db):
        client = FakeClient()
        client.positions = [{"symbol": "TESTUSDT", "positionAmt": "0.25", "entryPrice": "101.5"}]
        executor = Executor(client, config())
        sync = PositionSync(client, executor, db, config())
        recovered = await sync.sync(["TESTUSDT"])
        assert recovered["symbol"] == "TESTUSDT"
        assert recovered["quantity"] == 0.25
        assert any(o["type"] == "STOP_MARKET" for o in client.created_orders)
        assert await db.get_open_trade() is not None
    await with_db(run)


async def main():
    tests = [
        test_rejects_duplicate_open_from_db,
        test_rejects_duplicate_open_from_exchange,
        test_open_uses_exchange_quantity_and_entry_after_fill,
        test_failed_stop_loss_triggers_reduce_only_emergency_close_without_db_open,
        test_reduce_only_market_close_splits_by_exchange_max_quantity,
        test_cleanup_orphaned_protective_orders_skips_active_symbol,
        test_startup_sync_recovers_exchange_position_and_attaches_protection,
    ]
    for test in tests:
        await test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} trading safety tests passed")


if __name__ == "__main__":
    asyncio.run(main())
