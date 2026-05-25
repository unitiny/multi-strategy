import logging
from datetime import datetime
from typing import Optional

from binance.async_client import AsyncClient as BinanceAsyncClient
from market_data.database import Database

logger = logging.getLogger("multi_strategy")


class Heartbeat:
    def __init__(self, client: BinanceAsyncClient, db: Database,
                 config: dict, daily_guard, vpn_check, balance_monitor):
        self.client = client
        self.db = db
        self.config = config
        self.daily_guard = daily_guard
        self.vpn_check = vpn_check
        self.balance_monitor = balance_monitor

    async def generate_report(self) -> str:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        guard = self.daily_guard.get_status()
        proxy_ok = self.vpn_check.proxy_ok
        daily_pnl = await self.db.get_daily_pnl()

        lines = [
            f"**时间**: {now}",
            "",
            "### 持仓状态",
        ]

        try:
            positions = await self.client.futures_position_information()
            has_pos = False
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    has_pos = True
                    lines.append(
                        f"- **{p['symbol']}**: {amt} @ {p['entryPrice']} "
                        f"| PnL: {p['unRealizedProfit']}"
                    )
            if not has_pos:
                lines.append("- 无持仓")
        except Exception as e:
            lines.append(f"- 查询失败: {e}")

        lines.extend([
            "",
            "### 账户余额",
        ])
        try:
            balances = await self.client.futures_account_balance()
            for b in balances:
                if b["asset"] == "USDT":
                    lines.append(f"- 合约: {float(b['balance']):.4f} USDT")
        except Exception as e:
            lines.append(f"- 查询失败: {e}")

        lines.extend([
            "",
            f"### 当日盈亏: {daily_pnl:.4f} USDT",
            f"### 连续止损: {guard['consecutive_stops']}/{guard['max_daily_stops']}",
            f"### 交易暂停: {'是' if guard['trading_paused'] else '否'}",
            f"### 代理状态: {'正常' if proxy_ok else '断线'}",
        ])
        return "\n".join(lines)
