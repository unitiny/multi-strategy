import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from binance.async_client import AsyncClient as BinanceAsyncClient

from core.strategy import Strategy
from core.executor import Executor
from core.oco_watcher import OcoWatcher
from data.kline_cache import KlineCache
from data.market_feed import MarketFeed, _parse_kline
from data.database import Database
from risk.daily_guard import DailyGuard
from risk.balance_monitor import BalanceMonitor
from monitor.vpn_check import VpnCheck
from monitor.funding_rate import FundingRateMonitor
from monitor.heartbeat import Heartbeat
from notify.dingtalk import DingTalkNotifier

logger = logging.getLogger("multi_strategy")


class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.state_path = Path(__file__).resolve().parent.parent / "state.json"
        self.notifier: Optional[DingTalkNotifier] = None
        self.client: Optional[BinanceAsyncClient] = None
        self.cache = KlineCache()
        self.db = Database()
        self.feed: Optional[MarketFeed] = None
        self.strategy: Optional[Strategy] = None
        self.executor: Optional[Executor] = None
        self.oco: Optional[OcoWatcher] = None
        self.guard: Optional[DailyGuard] = None
        self.balance_monitor: Optional[BalanceMonitor] = None
        self.vpn_check: Optional[VpnCheck] = None
        self.funding_monitor: Optional[FundingRateMonitor] = None
        self.heartbeat: Optional[Heartbeat] = None
        self._current_position: Optional[dict] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        logger.info("Engine starting...")
        await self.db.init()

        self.notifier = DingTalkNotifier(
            self.config["dingtalk"]["webhook"],
            self.config["dingtalk"]["secret"],
        )

        proxy_url = (self.config["binance"].get("proxy") or {}).get("https")

        self.client = await BinanceAsyncClient.create(
            api_key=self.config["binance"]["api_key"],
            api_secret=self.config["binance"]["api_secret"],
            testnet=self.config["binance"]["testnet"],
            https_proxy=proxy_url,
        )

        symbols = [w["symbol"] for w in self.config.get("watchlist", [])]

        self.feed = MarketFeed(
            self.client, self.cache, symbols,
            interval=self.config["strategy"]["kline_interval"],
            proxy_url=proxy_url,
        )
        self.feed.set_on_kline_close(self._on_kline_close)

        self.strategy = Strategy(self.config, self.cache)
        self.executor = Executor(self.client, self.config)
        self.guard = DailyGuard(self.config)
        self.balance_monitor = BalanceMonitor(self.client, self.config)

        self.vpn_check = VpnCheck(
            self.config,
            on_down=self._on_proxy_down,
            on_up=self._on_proxy_up,
        )

        self.funding_monitor = FundingRateMonitor(
            self.client, self.db, self.config
        )
        self.heartbeat = Heartbeat(
            self.client, self.db, self.config,
            self.guard, self.vpn_check, self.balance_monitor,
        )

        self.oco = OcoWatcher(
            self.client, on_fill=self._on_position_closed,
            poll_interval=30, proxy_url=proxy_url,
        )

        self._load_state()
        if self._current_position:
            self.oco.set_position(self._current_position)
            logger.info(f"Restored position: {self._current_position['symbol']}")

        await self.vpn_check.start()
        await self.oco.start()
        await self.feed.start()

        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        self._running = True
        logger.info("Engine started successfully")

    async def stop(self):
        logger.info("Engine stopping...")
        self._running = False

        if self._current_position:
            symbol = self._current_position["symbol"]
            await self.executor.cancel_open_limit(symbol)

        self._save_state()

        if self.feed:
            await self.feed.stop()
        if self.oco:
            await self.oco.stop()
        if self.vpn_check:
            await self.vpn_check.stop()
        if self._scheduler_task:
            self._scheduler_task.cancel()
        if self.client:
            await self.client.close_connection()
        await self.db.close()
        logger.info("Engine stopped")

    def _load_state(self):
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    state = json.load(f)
                if state.get("current_position"):
                    self._current_position = state["current_position"]
            except Exception as e:
                logger.error(f"Load state: {e}")

    def _save_state(self):
        state = {
            "current_position": self._current_position,
            "proxy_ok": self.vpn_check.proxy_ok if self.vpn_check else True,
            "last_heartbeat": datetime.utcnow().isoformat(),
        }
        if self.guard:
            state.update(self.guard.get_status())
        try:
            with open(self.state_path, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Save state: {e}")

    async def _on_kline_close(self, symbol: str, kline: dict):
        logger.info(f"Kline closed: {symbol}")
        if self._current_position:
            return
        if not self.vpn_check.proxy_ok:
            return
        if not self.guard.can_trade():
            logger.info("Trading paused (daily limit)")
            return

        signal = self.strategy.evaluate(symbol)
        if not signal:
            return

        logger.info(
            f"Signal detected: {symbol} ATR%={signal.atr_pct:.2f} "
            f"pullback={signal.pullback_pct:.2f}% RSI={signal.rsi:.1f}"
        )

        result = await self.executor.execute_open(signal, self.db)
        if result:
            self._current_position = result
            self.oco.set_position(result)
            self._save_state()
            await self.notifier.notify_open(
                symbol=result["symbol"],
                direction="LONG",
                quantity=result["quantity"],
                entry_price=result["entry_price"],
                sl=result["sl_price"],
                tp=result["tp_price"],
            )

    async def _on_position_closed(self, symbol: str, exit_price: float,
                                  is_sl: bool, order_id: int):
        if not self._current_position:
            return
        pos = self._current_position
        entry = pos["entry_price"]
        qty = pos["quantity"]
        pnl = (exit_price - entry) * qty if exit_price else 0

        await self.db.close_trade(symbol, exit_price, pnl)

        if is_sl:
            self.guard.record_stop_loss()
            await self.notifier.notify_sl(
                symbol, pnl, self.guard.consecutive_stops
            )
            if not self.guard.can_trade():
                await self.notifier.notify_pause()
        else:
            self.guard.record_win()
            params = self.config["strategy"]
            await self.notifier.notify_tp(symbol, pnl, params["reward_risk"])

        self._current_position = None
        self.oco.clear_position()
        self._save_state()
        logger.info(f"Position closed: {symbol} PnL={pnl:.4f} SL={is_sl}")

    async def _on_proxy_down(self):
        await self.notifier.notify_proxy_down()

    async def _on_proxy_up(self):
        await self.notifier.notify_proxy_up()

    async def _scheduler_loop(self):
        last_report_date = None
        last_heartbeat_times = set()
        while True:
            try:
                now = datetime.utcnow()
                time_str = now.strftime("%H:%M")

                report_time = self.config["schedule"]["funding_report"]
                if time_str == report_time and last_report_date != now.strftime("%Y-%m-%d"):
                    last_report_date = now.strftime("%Y-%m-%d")
                    symbols = [w["symbol"] for w in self.config.get("watchlist", [])]
                    for s in symbols:
                        await self.funding_monitor.collect_funding(s)
                    report = await self.funding_monitor.generate_report()
                    await self.notifier.notify_funding_report(report)

                for ht in self.config["schedule"]["heartbeat"]:
                    key = f"{now.strftime('%Y-%m-%d')}_{ht}"
                    if time_str == ht and key not in last_heartbeat_times:
                        last_heartbeat_times.add(key)
                        report = await self.heartbeat.generate_report()
                        await self.notifier.notify_heartbeat(report)

                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                if self.notifier:
                    await self.notifier.notify_error(f"Scheduler: {e}")
                await asyncio.sleep(30)
