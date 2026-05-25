import logging
import sys


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

    base_dir = __file__.rsplit("utils", 1)[0].rstrip("/\\")
    fh = logging.FileHandler(f"{base_dir}/app.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
