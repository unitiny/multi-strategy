import numpy as np
from collections import defaultdict
from typing import Optional


class KlineCache:
    def __init__(self, max_klines: int = 200):
        self._max = max_klines
        self._data: dict[str, list[dict]] = defaultdict(list)

    def update(self, symbol: str, klines: list[dict]):
        existing = self._data[symbol]
        existing_keys = {k["open_time"] for k in existing}
        for k in klines:
            if k["open_time"] not in existing_keys:
                existing.append(k)
        existing.sort(key=lambda x: x["open_time"])
        self._data[symbol] = existing[-self._max:]

    def get_klines(self, symbol: str, count: int = None) -> list[dict]:
        data = self._data.get(symbol, [])
        if count:
            return data[-count:]
        return list(data)

    def get_last_closed(self, symbol: str) -> Optional[dict]:
        data = self._data.get(symbol, [])
        return data[-2] if len(data) >= 2 else None

    def calc_atr(self, symbol: str, period: int = 14) -> Optional[float]:
        klines = self.get_klines(symbol, period + 1)
        if len(klines) < period + 1:
            return None
        trs = []
        for i in range(1, len(klines)):
            h = klines[i]["high"]
            l = klines[i]["low"]
            prev_close = klines[i - 1]["close"]
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
        if len(trs) < period:
            return None
        return float(np.mean(trs[-period:]))

    def calc_rsi(self, symbol: str, period: int = 14) -> Optional[float]:
        klines = self.get_klines(symbol, period + 1)
        if len(klines) < period + 1:
            return None
        closes = [k["close"] for k in klines]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))
        if avg_loss == 0:
            return 100.0
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        return 100.0 - (100.0 / (1.0 + rs))

    def calc_ema(self, symbol: str, period: int = 5) -> Optional[float]:
        klines = self.get_klines(symbol, period * 3)
        if len(klines) < period:
            return None
        closes = np.array([k["close"] for k in klines], dtype=float)
        multiplier = 2.0 / (period + 1)
        ema = float(closes[:period].mean())
        for c in closes[period:]:
            ema = (c - ema) * multiplier + ema
        return ema

    def calc_volume_ma(self, symbol: str, period: int = 20) -> Optional[float]:
        klines = self.get_klines(symbol, period)
        if len(klines) < period:
            return None
        volumes = [k["volume"] for k in klines]
        return float(np.mean(volumes))
