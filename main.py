import asyncio
import signal
import sys
import logging
from pathlib import Path

from core.engine import Engine
from utils.config_loader import load_config
from utils.logger import setup_logger


async def main():
    config_path = str(Path(__file__).parent / "config.yaml")
    config = load_config(config_path)
    logger = setup_logger(config.get("log_level", "INFO"))

    logger.info("=" * 50)
    logger.info("Multi-Strategy Risk Control System Starting")
    logger.info(f"Mode: {config.get('mode', 'watchlist')}")
    logger.info(f"Testnet: {config['binance']['testnet']}")
    logger.info("=" * 50)

    engine = Engine(config)
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    else:
        try:
            loop.add_signal_handler(signal.SIGINT, _signal_handler)
        except NotImplementedError:
            signal.signal(signal.SIGINT, lambda *_: _signal_handler())

    try:
        await engine.start()
        logger.info("System running. Press Ctrl+C to stop.")
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        if engine.notifier:
            await engine.notifier.notify_error(f"Fatal: {e}")
    finally:
        await engine.stop()
        logger.info("System shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
