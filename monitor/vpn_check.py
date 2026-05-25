import asyncio
import logging
import aiohttp

logger = logging.getLogger("multi_strategy")


class VpnCheck:
    def __init__(self, config: dict, on_down=None, on_up=None):
        self.config = config
        self.interval = config["schedule"]["vpn_check_interval_sec"]
        self.on_down = on_down
        self.on_up = on_up
        self._proxy_ok = True
        self._task = None

    @property
    def proxy_ok(self) -> bool:
        return self._proxy_ok

    async def check_once(self) -> bool:
        proxy = self.config["binance"]["proxy"]
        testnet = self.config["binance"]["testnet"]
        base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        url = f"{base}/fapi/v1/time"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    proxy=proxy.get("https") if proxy else None,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        if not self._proxy_ok:
                            self._proxy_ok = True
                            logger.info("Proxy restored")
                            if self.on_up:
                                await self.on_up()
                        return True
        except Exception as e:
            logger.warning(f"Proxy check failed: {e}")
        if self._proxy_ok:
            self._proxy_ok = False
            logger.warning("Proxy down")
            if self.on_down:
                await self.on_down()
        return False

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            try:
                await self.check_once()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"VPN check loop error: {e}")
                await asyncio.sleep(self.interval)
