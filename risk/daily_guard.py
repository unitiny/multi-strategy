import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("multi_strategy")

import os

_PERSIST = Path(os.environ.get("PERSIST_DIR", Path(__file__).resolve().parent.parent))
STATE_PATH = _PERSIST / "state.json"


class DailyGuard:
    def __init__(self, config: dict, state_path: str = None):
        self.config = config
        self.state_path = Path(state_path) if state_path else STATE_PATH
        self.consecutive_stops = 0
        self.last_stop_date = None
        self.trading_paused = False
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                with open(self.state_path, "r") as f:
                    data = json.load(f)
                self.consecutive_stops = data.get("consecutive_stops", 0)
                self.last_stop_date = data.get("last_stop_date")
                self.trading_paused = data.get("trading_paused", False)
            except Exception as e:
                logger.error(f"Load state: {e}")

    def _save(self):
        data = self._full_state()
        try:
            with open(self.state_path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Save state: {e}")

    def _full_state(self) -> dict:
        return {
            "consecutive_stops": self.consecutive_stops,
            "last_stop_date": self.last_stop_date,
            "trading_paused": self.trading_paused,
        }

    def can_trade(self) -> bool:
        self._check_reset()
        if self.trading_paused:
            return False
        max_stops = self.config["risk"]["max_daily_stops"]
        return self.consecutive_stops < max_stops

    def record_stop_loss(self):
        self._check_reset()
        self.consecutive_stops += 1
        self.last_stop_date = datetime.utcnow().strftime("%Y-%m-%d")
        max_stops = self.config["risk"]["max_daily_stops"]
        if self.consecutive_stops >= max_stops:
            self.trading_paused = True
            logger.warning(
                f"Daily stop limit reached ({self.consecutive_stops}), trading paused"
            )
        self._save()

    def record_win(self):
        self.consecutive_stops = 0
        self.trading_paused = False
        self._save()

    def _check_reset(self):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self.last_stop_date and self.last_stop_date != today:
            self.consecutive_stops = 0
            self.trading_paused = False
            self._save()
            logger.info("Daily stop count reset (new day)")

    def get_status(self) -> dict:
        self._check_reset()
        return {
            "consecutive_stops": self.consecutive_stops,
            "max_daily_stops": self.config["risk"]["max_daily_stops"],
            "trading_paused": self.trading_paused,
            "last_stop_date": self.last_stop_date,
        }
