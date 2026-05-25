import logging
from binance.async_client import AsyncClient as BinanceAsyncClient

logger = logging.getLogger("multi_strategy")


class BalanceMonitor:
    def __init__(self, client: BinanceAsyncClient, config: dict):
        self.client = client
        self.config = config
        self.target = config["risk"]["total_asset_target"]
        self.threshold_pct = config["risk"]["rebalance_threshold_pct"]

    async def check(self) -> dict | None:
        try:
            futures = await self.client.futures_account_balance()
            futures_bal = 0.0
            for b in futures:
                if b["asset"] == "USDT":
                    futures_bal = float(b["balance"])
                    break

            spot = await self.client.get_asset_balance(asset="USDT")
            spot_bal = float(spot["free"]) + float(spot["locked"]) if spot else 0.0

            total = futures_bal + spot_bal
            deviation = ((total - self.target) / self.target) * 100 if self.target else 0
            exceeded = abs(deviation) > self.threshold_pct

            return {
                "futures_balance": round(futures_bal, 4),
                "spot_balance": round(spot_bal, 4),
                "total": round(total, 4),
                "target": self.target,
                "deviation_pct": round(deviation, 2),
                "exceeded": exceeded,
            }
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return None
