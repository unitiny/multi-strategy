import logging
import os
import sys
from datetime import datetime
from pathlib import Path


class DailyFileHandler(logging.FileHandler):
    """按天切换日志文件，存放到 logs/YYYY-MM-DD/ 目录下"""

    def __init__(self, log_dir: str, encoding: str = "utf-8"):
        self._log_dir = Path(log_dir)
        self._current_date: str = ""
        super().__init__(self._build_path(), encoding=encoding)

    def _build_path(self) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        day_dir = self._log_dir / today
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "app.log"
        self._current_date = today
        return str(path)

    def emit(self, record: logging.LogRecord) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self.close()
            self.baseFilename = self._build_path()
            self.stream = self._open()
        super().emit(record)


def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("multi_strategy")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    base_dir = os.environ.get("PERSIST_DIR", __file__.rsplit("utils", 1)[0].rstrip("/\\"))
    fh = DailyFileHandler(f"{base_dir}/logs", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
