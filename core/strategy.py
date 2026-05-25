import logging
from typing import Optional

from data.kline_cache import KlineCache
from utils.config_loader import get_symbol_params

logger = logging.getLogger("multi_strategy")


class Signal:
    def __init__(self, symbol: str, direction: str, atr_pct: float,
                 pullback_pct: float, rsi: float, volume: float,
                 volume_ma: float, ema: float, close: float,
                 params: dict):
        self.symbol = symbol
        self.direction = direction
        self.atr_pct = atr_pct
        self.pullback_pct = pullback_pct
        self.rsi = rsi
        self.volume = volume
        self.volume_ma = volume_ma
        self.ema = ema
        self.close = close
        self.params = params


class Strategy:
    def __init__(self, config: dict, cache: KlineCache):
        self.config = config
        self.cache = cache

    def evaluate(self, symbol: str) -> Optional[Signal]:
        params = get_symbol_params(self.config, symbol)
        last = self.cache.get_last_closed(symbol)
        if not last:
            return None

        close = last["close"]
        high = last["high"]
        low = last["low"]
        volume = last["volume"]

        atr = self.cache.calc_atr(symbol, params["atr_period"])
        if atr is None or close == 0:
            return None
        atr_pct = (atr / close) * 100

        if atr_pct <= params["atr_pct_threshold"]:
            return None

        ema = self.cache.calc_ema(symbol, params["ema_period"])
        if ema is None:
            return None

        pullback_pct = (ema - close) / ema * 100 if ema != 0 else 0
        if not (params["pullback_min_pct"] <= pullback_pct <= params["pullback_max_pct"]):
            return None

        rsi = self.cache.calc_rsi(symbol, params["rsi_period"])
        if rsi is None:
            return None
        if not (params["rsi_min"] <= rsi <= params["rsi_max"]):
            return None

        volume_ma = self.cache.calc_volume_ma(symbol, params["volume_ma_period"])
        if volume_ma is None:
            return None
        if volume >= volume_ma:
            return None

        return Signal(
            symbol=symbol,
            direction="LONG",
            atr_pct=atr_pct,
            pullback_pct=pullback_pct,
            rsi=rsi,
            volume=volume,
            volume_ma=volume_ma,
            ema=ema,
            close=close,
            params=params,
        )

    def scan_evaluate(self, symbol: str, klines_20: list) -> Optional[Signal]:
        self.cache.update(symbol, klines_20)
        return self.evaluate(symbol)
