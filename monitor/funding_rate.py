import logging
from datetime import datetime

from binance.async_client import AsyncClient as BinanceAsyncClient
from market_data.database import Database

logger = logging.getLogger("multi_strategy")


class FundingRateMonitor:
    def __init__(self, client: BinanceAsyncClient, db: Database, config: dict):
        self.client = client
        self.db = db
        self.config = config

    async def collect_funding(self, symbol: str):
        try:
            rates = await self.client.futures_funding_rate(
                symbol=symbol, limit=1
            )
            if rates:
                r = rates[0]
                funding_rate = float(r["fundingRate"])
                funding_time = datetime.fromtimestamp(
                    r["fundingTime"] / 1000
                ).isoformat()
                amount = funding_rate
                await self.db.insert_funding_rate(
                    symbol=symbol,
                    funding_rate=funding_rate,
                    funding_time=funding_time,
                    amount=amount,
                )
        except Exception as e:
            logger.error(f"Funding rate collect {symbol}: {e}")

    async def generate_report(self) -> str:
        daily = await self.db.get_daily_funding()
        total_funding = await self.db.get_total_funding()
        daily_pnl = await self.db.get_daily_pnl()
        net = daily_pnl + total_funding

        lines = [
            f"**日期**: {datetime.utcnow().strftime('%Y-%m-%d')}",
            "",
            "### 当日资金费率",
        ]
        if daily:
            for item in daily:
                lines.append(f"- {item['symbol']}: {item['total_amount']:.6f}")
        else:
            lines.append("- 无数据")

        lines.extend([
            "",
            f"### 累计资金费率: {total_funding:.6f} USDT",
            f"### 当日交易盈亏: {daily_pnl:.4f} USDT",
            f"### 净盈亏: {net:.4f} USDT",
        ])
        return "\n".join(lines)
