import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from binance.async_client import AsyncClient as BinanceAsyncClient

from core.strategy import Strategy
from core.executor import Executor
from core.oco_watcher import OcoWatcher
from core.position_sync import PositionSync
from market_data.kline_cache import KlineCache
from market_data.market_feed import MarketFeed, _parse_kline
from market_data.database import Database
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
        self.state_path = Path(os.environ.get("PERSIST_DIR", Path(__file__).resolve().parent.parent)) / "state.json"
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
        self._scan_task: Optional[asyncio.Task] = None
        self._scan_symbols: list[str] = []
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

        mode = self.config.get("mode", "watchlist")

        if mode == "scan":
            # feed is still needed for fetch_exchange_info()
            self.feed = MarketFeed(
                self.client, self.cache, [],
                interval=self.config["strategy"]["kline_interval"],
                proxy_url=proxy_url,
            )
        else:
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
        symbols = [w["symbol"] for w in self.config.get("watchlist", [])]
        if not self._current_position:
            sync = PositionSync(self.client, self.executor, self.db, self.config)
            self._current_position = await sync.sync(symbols)

        active_symbols = {self._current_position["symbol"]} if self._current_position else set()
        try:
            cancelled = await self.executor.cleanup_orphaned_protective_orders(active_symbols)
            if cancelled:
                logger.warning(f"Cleaned orphaned protective orders: {cancelled}")
        except Exception as e:
            logger.warning(f"Cleanup orphaned protective orders failed: {e}")

        if self._current_position:
            self.oco.set_position(self._current_position)
            logger.info(f"Restored position: {self._current_position['symbol']}")

        await self.vpn_check.start()
        await self.oco.start()
        if mode == "scan":
            self._scan_task = asyncio.create_task(self._scan_loop())
        else:
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
        if self._scan_task:
            self._scan_task.cancel()
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

    async def _log_account_status(self):
        try:
            bal = await self.balance_monitor.check()
            if bal:
                logger.info(
                    f"Account | Futures: {bal['futures_balance']} USDT "
                    f"| Spot: {bal['spot_balance']} USDT "
                    f"| Total: {bal['total']} USDT"
                )
        except Exception as e:
            logger.warning(f"Account balance query failed: {e}")

        try:
            positions = await self.client.futures_position_information()
            open_positions = [
                p for p in positions if float(p.get("positionAmt", 0)) != 0
            ]
            if open_positions:
                for p in open_positions:
                    logger.info(
                        f"Position | {p['symbol']}: qty={p['positionAmt']} "
                        f"entry={p['entryPrice']} uPnL={p['unRealizedProfit']}"
                    )
            else:
                logger.info("Position | No open positions")
        except Exception as e:
            logger.warning(f"Position query failed: {e}")

    async def _scan_loop(self):
        interval = self.config["strategy"]["kline_interval"]
        # Map interval string to seconds for scan pacing
        interval_sec = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                        "4h": 14400, "1d": 86400}.get(interval, 3600)
        # Scan more frequently than the kline interval to catch signals sooner
        scan_interval = max(60, interval_sec // 4)

        while True:
            try:
                await self._log_account_status()
                if not self._current_position and self.vpn_check.proxy_ok and self.guard.can_trade():
                    await self._scan_all_symbols()
                await asyncio.sleep(scan_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scan loop error: {e}")
                if self.notifier:
                    await self.notifier.notify_error(f"Scan: {e}")
                await asyncio.sleep(60)

    async def _scan_all_symbols(self):
        try:
            exchange_symbols = await self.feed.fetch_exchange_info()
        except Exception as e:
            logger.error(f"Fetch exchange info failed: {e}")
            return

        all_symbols = list(exchange_symbols.keys())
        logger.info(f"Scanning {len(all_symbols)} symbols...")

        for symbol in all_symbols:
            try:
                raw = await self.client.futures_klines(
                    symbol=symbol,
                    interval=self.config["strategy"]["kline_interval"],
                    limit=30,
                )
                klines = [_parse_kline(k) for k in raw]
                if len(klines) < 20:
                    continue

                signal = self.strategy.scan_evaluate(symbol, klines)
                if signal:
                    logger.info(
                        f"Scan signal: {symbol} ATR%={signal.atr_pct:.2f} "
                        f"pullback={signal.pullback_pct:.2f}% RSI={signal.rsi:.1f}"
                    )
                    await self._persist_signal(signal)
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
                        break
            except Exception as e:
                logger.debug(f"Scan {symbol} failed: {e}")

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
        await self._persist_signal(signal)

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

    async def _persist_signal(self, signal):
        try:
            await self.db.insert_signal(
                symbol=signal.symbol, direction=signal.direction,
                atr_pct=signal.atr_pct, pullback_pct=signal.pullback_pct,
                rsi=signal.rsi, volume=signal.volume,
                volume_ma=signal.volume_ma, ema=signal.ema,
                close=signal.close, params=signal.params,
            )
        except Exception as e:
            logger.warning(f"Persist signal failed: {e}")

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
                    funding_symbols = self._scan_symbols or [w["symbol"] for w in self.config.get("watchlist", [])]
                    for s in funding_symbols:
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
