import hashlib
import hmac
import base64
import urllib.parse
import time
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("multi_strategy")


class DingTalkNotifier:
    def __init__(self, webhook: str, secret: str):
        self.webhook = webhook
        self.secret = secret

    def _sign(self) -> str:
        ts = str(int(time.time() * 1000))
        string_to_sign = f"{ts}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook}&timestamp={ts}&sign={sign}"

    async def send(self, title: str, text: str, level: str = "INFO"):
        if not self.webhook:
            logger.warning("DingTalk webhook not configured, skip notification")
            return
        emoji = {"INFO": "", "WARNING": "⚠️", "ERROR": "🚨"}.get(level, "")
        content = f"{emoji} **{title}**\n\n{text}"
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }
        try:
            url = self._sign()
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    result = await resp.json()
                    if result.get("errcode") != 0:
                        logger.error(f"DingTalk send failed: {result}")
        except Exception as e:
            logger.error(f"DingTalk error: {e}")

    async def notify_open(self, symbol: str, direction: str, quantity: float,
                          entry_price: float, sl: float, tp: float):
        await self.send(
            "开仓成交",
            f"- 标的: **{symbol}**\n- 方向: {direction}\n- 数量: {quantity}\n"
            f"- 入场价: {entry_price}\n- 止损价: {sl}\n- 止盈价: {tp}",
            "INFO",
        )

    async def notify_tp(self, symbol: str, pnl: float, reward_risk: float):
        await self.send(
            "止盈成交",
            f"- 标的: **{symbol}**\n- 盈亏: **+{pnl:.4f} USDT**\n- 盈亏比: {reward_risk}",
            "INFO",
        )

    async def notify_sl(self, symbol: str, pnl: float, consecutive_stops: int):
        await self.send(
            "止损成交",
            f"- 标的: **{symbol}**\n- 亏损: **{pnl:.4f} USDT**\n- 当日连续止损: {consecutive_stops}",
            "WARNING",
        )

    async def notify_pause(self):
        await self.send(
            "连续止损暂停",
            "当日已暂停开仓，请关注市场情况",
            "WARNING",
        )

    async def notify_balance_deviation(self, total: float, deviation: float):
        await self.send(
            "资产偏离提醒",
            f"- 当前总资产: **{total} USDT**\n- 偏离: **{deviation}%**\n- 请手动操作划转",
            "WARNING",
        )

    async def notify_proxy_down(self):
        await self.send("代理断线", "代理不通，暂停开仓", "ERROR")

    async def notify_proxy_up(self):
        await self.send("代理恢复", "代理恢复，恢复开仓", "INFO")

    async def notify_funding_report(self, text: str):
        await self.send("资金费率日报", text, "INFO")

    async def notify_heartbeat(self, text: str):
        await self.send("心跳播报", text, "INFO")

    async def notify_error(self, error_text: str):
        await self.send("程序异常", error_text, "ERROR")
