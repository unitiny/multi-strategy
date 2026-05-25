import aiosqlite
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"


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
