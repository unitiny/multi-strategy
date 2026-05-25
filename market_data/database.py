import aiosqlite
import json
from pathlib import Path
from datetime import datetime

import os

_PERSIST = Path(os.environ.get("PERSIST_DIR", Path(__file__).resolve().parent.parent))
DB_PATH = _PERSIST / "trades.db"


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._conn: aiosqlite.Connection | None = None

    async def init(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()

    async def _create_tables(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss_price REAL,
                take_profit_price REAL,
                pnl REAL,
                status TEXT DEFAULT 'open',
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                strategy_params TEXT
            );

            CREATE TABLE IF NOT EXISTS funding_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                funding_rate REAL NOT NULL,
                funding_time TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                atr_pct REAL,
                pullback_pct REAL,
                rsi REAL,
                volume REAL,
                volume_ma REAL,
                ema REAL,
                close REAL,
                params TEXT,
                triggered_at TEXT NOT NULL
            );
        """)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def insert_trade(self, symbol: str, side: str, quantity: float,
                           entry_price: float, stop_loss_price: float,
                           take_profit_price: float, strategy_params: dict = None):
        await self._conn.execute(
            """INSERT INTO trades (symbol, side, quantity, entry_price,
               stop_loss_price, take_profit_price, entry_time, strategy_params, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (symbol, side, quantity, entry_price, stop_loss_price,
             take_profit_price, datetime.utcnow().isoformat(),
             json.dumps(strategy_params) if strategy_params else None)
        )
        await self._conn.commit()

    async def close_trade(self, symbol: str, exit_price: float, pnl: float):
        await self._conn.execute(
            """UPDATE trades SET exit_price=?, pnl=?, exit_time=?, status='closed'
               WHERE symbol=? AND status='open'""",
            (exit_price, pnl, datetime.utcnow().isoformat(), symbol)
        )
        await self._conn.commit()

    async def get_open_trade(self):
        async with self._conn.execute(
            "SELECT * FROM trades WHERE status='open' LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_open_trade_by_symbol(self, symbol: str):
        async with self._conn.execute(
            "SELECT * FROM trades WHERE symbol=? AND status='open' LIMIT 1",
            (symbol,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_open_trades(self):
        async with self._conn.execute(
            "SELECT * FROM trades WHERE status='open'"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def insert_funding_rate(self, symbol: str, funding_rate: float,
                                  funding_time: str, amount: float):
        await self._conn.execute(
            """INSERT INTO funding_rates (symbol, funding_rate, funding_time, amount)
               VALUES (?, ?, ?, ?)""",
            (symbol, funding_rate, funding_time, amount)
        )
        await self._conn.commit()

    async def get_daily_funding(self, date_str: str = None):
        if date_str is None:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
        async with self._conn.execute(
            """SELECT symbol, SUM(amount) as total_amount
               FROM funding_rates
               WHERE DATE(funding_time) = ?
               GROUP BY symbol""",
            (date_str,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_total_funding(self):
        async with self._conn.execute(
            "SELECT SUM(amount) as total FROM funding_rates"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row)["total"] or 0.0

    async def get_daily_pnl(self, date_str: str = None):
        if date_str is None:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
        async with self._conn.execute(
            """SELECT SUM(pnl) as total FROM trades
               WHERE status='closed' AND DATE(exit_time) = ?""",
            (date_str,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row)["total"] or 0.0

    async def get_all_closed_trades(self):
        async with self._conn.execute(
            "SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def insert_signal(self, symbol: str, direction: str, atr_pct: float,
                            pullback_pct: float, rsi: float, volume: float,
                            volume_ma: float, ema: float, close: float,
                            params: dict = None):
        await self._conn.execute(
            """INSERT INTO signals (symbol, direction, atr_pct, pullback_pct, rsi,
               volume, volume_ma, ema, close, params, triggered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, direction, atr_pct, pullback_pct, rsi,
             volume, volume_ma, ema, close,
             json.dumps(params) if params else None,
             datetime.utcnow().isoformat())
        )
        await self._conn.commit()

    async def query_signals(self, start_date: str = None, end_date: str = None,
                            symbol: str = None, limit: int = 100):
        conditions = []
        params = []
        if start_date:
            conditions.append("DATE(triggered_at) >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("DATE(triggered_at) <= ?")
            params.append(end_date)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        query = f"SELECT * FROM signals WHERE {where} ORDER BY triggered_at DESC LIMIT ?"
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def query_trades(self, start_date: str = None, end_date: str = None,
                           status: str = None, symbol: str = None, limit: int = 100):
        conditions = []
        params = []
        if start_date:
            conditions.append("DATE(entry_time) >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("DATE(entry_time) <= ?")
            params.append(end_date)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        query = f"SELECT * FROM trades WHERE {where} ORDER BY entry_time DESC LIMIT ?"
        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
