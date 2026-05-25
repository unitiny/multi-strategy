"""End-to-end test script for the multi-strategy system."""
import asyncio
import sys
import os
import json
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from utils.config_loader import load_config, get_symbol_params
from utils.logger import setup_logger


async def test_config_loading():
    print("\n=== Test 1: Config Loading ===")
    config = load_config()
    assert "binance" in config, "Missing binance config"
    assert "strategy" in config, "Missing strategy config"
    assert "watchlist" in config, "Missing watchlist config"
    assert config["binance"]["api_key"], "Missing API key"
    assert config["binance"]["api_secret"], "Missing API secret"
    assert config["mode"] in ("watchlist", "scan"), f"Invalid mode: {config['mode']}"
    print(f"  Mode: {config['mode']}")
    print(f"  Testnet: {config['binance']['testnet']}")
    print(f"  Watchlist: {[w['symbol'] for w in config['watchlist']]}")
    print(f"  Proxy: {config['binance'].get('proxy', {})}")

    # Test symbol param override
    doge_params = get_symbol_params(config, "DOGEUSDT")
    assert doge_params["reward_risk"] == 1.5, f"DOGE override failed: {doge_params['reward_risk']}"
    link_params = get_symbol_params(config, "LINKUSDT")
    assert link_params["reward_risk"] == 3, f"LINK should use default: {link_params['reward_risk']}"
    print(f"  DOGE reward_risk override: {doge_params['reward_risk']} (expected 1.5)")
    print(f"  LINK reward_risk default: {link_params['reward_risk']} (expected 3)")
    print("  PASS")


async def test_database():
    print("\n=== Test 2: SQLite Database ===")
    from market_data.database import Database
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    await db.init()
    await db.insert_trade("BTCUSDT", "BUY", 0.001, 50000.0, 49000.0, 52000.0)
    trade = await db.get_open_trade()
    assert trade is not None, "Trade not found"
    assert trade["symbol"] == "BTCUSDT"
    assert trade["status"] == "open"
    print(f"  Inserted trade: {trade['symbol']} @ {trade['entry_price']}")

    await db.close_trade("BTCUSDT", 51500.0, 1.5)
    trade = await db.get_open_trade()
    assert trade is None, "Trade should be closed"
    pnl = await db.get_daily_pnl()
    print(f"  Daily PnL: {pnl}")
    await db.close()
    os.unlink(db_path)
    print("  PASS")


async def test_kline_cache_and_indicators():
    print("\n=== Test 3: Kline Cache + Indicators ===")
    from market_data.kline_cache import KlineCache
    import numpy as np
    np.random.seed(42)

    cache = KlineCache()
    base_price = 100.0
    klines = []
    for i in range(100):
        close = base_price + np.random.randn() * 2
        high = close + abs(np.random.randn())
        low = close - abs(np.random.randn())
        volume = 1000 + np.random.rand() * 500
        klines.append({
            "open_time": 1000 + i * 3600000,
            "open": base_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": 1000 + (i + 1) * 3600000 - 1,
        })
        base_price = close

    cache.update("TESTUSDT", klines)
    assert len(cache.get_klines("TESTUSDT")) == 100

    atr = cache.calc_atr("TESTUSDT", 14)
    assert atr is not None and atr > 0, f"ATR invalid: {atr}"
    print(f"  ATR(14): {atr:.4f}")

    rsi = cache.calc_rsi("TESTUSDT", 14)
    assert rsi is not None and 0 <= rsi <= 100, f"RSI invalid: {rsi}"
    print(f"  RSI(14): {rsi:.2f}")

    ema = cache.calc_ema("TESTUSDT", 5)
    assert ema is not None, "EMA is None"
    print(f"  EMA(5): {ema:.4f}")

    vol_ma = cache.calc_volume_ma("TESTUSDT", 20)
    assert vol_ma is not None and vol_ma > 0, f"Vol MA invalid: {vol_ma}"
    print(f"  Volume MA(20): {vol_ma:.2f}")

    last = cache.get_last_closed("TESTUSDT")
    assert last is not None, "Last closed kline is None"
    print(f"  Last closed: close={last['close']:.4f}")
    print("  PASS")


async def test_position_sizing():
    print("\n=== Test 4: Position Sizing ===")
    from risk.position_sizing import calc_position, calc_stop_take

    pos = calc_position(0.21, 1.5, 100.0, 3)
    assert pos["notional"] > 0
    assert pos["quantity"] > 0
    assert pos["margin"] > 0
    print(f"  Notional: {pos['notional']}, Qty: {pos['quantity']}, Margin: {pos['margin']}")

    st = calc_stop_take(100.0, 1.5, 3)
    assert st["stop_loss"] < 100.0
    assert st["take_profit"] > 100.0
    print(f"  SL: {st['stop_loss']}, TP: {st['take_profit']}")
    print("  PASS")


async def test_daily_guard():
    print("\n=== Test 5: Daily Guard ===")
    import tempfile
    from risk.daily_guard import DailyGuard
    config = {"risk": {"max_daily_stops": 2, "total_asset_target": 22.5, "rebalance_threshold_pct": 20}}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({"consecutive_stops": 0, "last_stop_date": None, "trading_paused": False}, f)
        state_path = f.name

    guard = DailyGuard(config, state_path)
    assert guard.can_trade(), "Should be able to trade initially"

    guard.record_stop_loss()
    assert guard.can_trade(), "Should still trade after 1 stop"
    assert guard.consecutive_stops == 1

    guard.record_stop_loss()
    assert not guard.can_trade(), "Should be paused after 2 stops"
    assert guard.trading_paused
    print(f"  Paused after 2 stops: {guard.get_status()}")

    guard.record_win()
    assert guard.can_trade(), "Should resume after win"
    assert guard.consecutive_stops == 0
    print("  Resumed after win")
    os.unlink(state_path)
    print("  PASS")


async def test_strategy_signal():
    print("\n=== Test 6: Strategy Signal Detection ===")
    from market_data.kline_cache import KlineCache
    from core.strategy import Strategy
    import numpy as np
    np.random.seed(100)

    config = {
        "strategy": {
            "fixed_loss": 0.21, "leverage": 3, "kline_interval": "1h",
            "atr_period": 14, "rsi_period": 14, "ema_period": 5,
            "volume_ma_period": 20, "atr_pct_threshold": 0.5,
            "pullback_min_pct": 0.5, "pullback_max_pct": 2.0,
            "rsi_min": 30, "rsi_max": 70, "reward_risk": 3,
            "limit_order_timeout_sec": 30,
        },
        "watchlist": [{"symbol": "TESTUSDT"}],
    }

    cache = KlineCache()
    base = 100.0
    klines = []
    for i in range(100):
        close = base + np.random.randn() * 3
        klines.append({
            "open_time": 1000 + i * 3600000,
            "open": base, "high": close + 2, "low": close - 2,
            "close": close, "volume": 800 + np.random.rand() * 200,
            "close_time": 1000 + (i + 1) * 3600000 - 1,
        })
        base = close

    cache.update("TESTUSDT", klines)
    strategy = Strategy(config, cache)
    signal = strategy.evaluate("TESTUSDT")
    if signal:
        print(f"  Signal: {signal.symbol} dir={signal.direction} "
              f"ATR%={signal.atr_pct:.2f} pullback={signal.pullback_pct:.2f}% "
              f"RSI={signal.rsi:.1f}")
    else:
        print("  No signal (expected with random data)")
    print("  PASS")


async def test_dingtalk_sign():
    print("\n=== Test 7: DingTalk Sign ===")
    from notify.dingtalk import DingTalkNotifier
    notifier = DingTalkNotifier("https://example.com", "SECtest")
    url = notifier._sign()
    assert "timestamp=" in url
    assert "sign=" in url
    print(f"  Signed URL contains timestamp and sign")
    print("  PASS")


async def _make_client(config):
    from binance.async_client import AsyncClient
    proxy_url = (config["binance"].get("proxy") or {}).get("https")
    client = await AsyncClient.create(
        api_key=config["binance"]["api_key"],
        api_secret=config["binance"]["api_secret"],
        testnet=config["binance"]["testnet"],
        https_proxy=proxy_url,
    )
    return client


async def test_binance_connection():
    print("\n=== Test 8: Binance Testnet Connection ===")
    config = load_config()
    client = await _make_client(config)
    try:
        time_resp = await client.futures_time()
        print(f"  Futures time: {time_resp}")

        balance = await client.futures_account_balance()
        usdt_bal = [b for b in balance if b["asset"] == "USDT"]
        if usdt_bal:
            print(f"  USDT balance: {usdt_bal[0]['balance']}")

        klines = await client.futures_klines(symbol="BTCUSDT", interval="1h", limit=5)
        print(f"  Fetched {len(klines)} BTCUSDT 1h klines")
        assert len(klines) > 0, "No klines returned"
        print("  PASS")
    finally:
        await client.close_connection()


async def test_market_feed_warmup():
    print("\n=== Test 9: Market Feed Warmup ===")
    config = load_config()
    from market_data.kline_cache import KlineCache
    from market_data.market_feed import MarketFeed

    client = await _make_client(config)
    try:
        cache = KlineCache()
        symbols = [w["symbol"] for w in config["watchlist"]]
        feed = MarketFeed(client, cache, symbols, interval="1h", kline_limit=100)
        await feed.warmup()

        for sym in symbols:
            klines = cache.get_klines(sym)
            print(f"  {sym}: {len(klines)} klines")
            if klines:
                last = klines[-1]
                print(f"    Last: close={last['close']} vol={last['volume']}")

        # Test indicators on real data
        for sym in symbols:
            atr = cache.calc_atr(sym, 14)
            rsi = cache.calc_rsi(sym, 14)
            ema = cache.calc_ema(sym, 5)
            vol_ma = cache.calc_volume_ma(sym, 20)
            if atr:
                atr_pct = atr / klines[-1]["close"] * 100 if klines else 0
                print(f"  {sym} indicators: ATR%={atr_pct:.2f} RSI={rsi:.1f} EMA={ema:.2f} VolMA={vol_ma:.0f}")
        print("  PASS")
    finally:
        await client.close_connection()


async def test_full_strategy_evaluation():
    print("\n=== Test 10: Full Strategy Evaluation on Real Data ===")
    config = load_config()
    from market_data.kline_cache import KlineCache
    from market_data.market_feed import MarketFeed
    from core.strategy import Strategy

    client = await _make_client(config)
    try:
        cache = KlineCache()
        symbols = [w["symbol"] for w in config["watchlist"]]
        feed = MarketFeed(client, cache, symbols, interval="1h", kline_limit=100)
        await feed.warmup()

        strategy = Strategy(config, cache)
        for sym in symbols:
            signal = strategy.evaluate(sym)
            if signal:
                print(f"  SIGNAL: {sym} ATR%={signal.atr_pct:.2f} "
                      f"pullback={signal.pullback_pct:.2f}% RSI={signal.rsi:.1f}")
            else:
                last = cache.get_last_closed(sym)
                if last:
                    atr = cache.calc_atr(sym, 14)
                    rsi = cache.calc_rsi(sym, 14)
                    ema = cache.calc_ema(sym, 5)
                    pullback = (ema - last["close"]) / ema * 100 if ema else 0
                    vol_ma = cache.calc_volume_ma(sym, 20)
                    vol_ok = last["volume"] < vol_ma if vol_ma else False
                    atr_pct = atr / last["close"] * 100 if atr else 0
                    print(f"  No signal {sym}: ATR%={atr_pct:.2f} RSI={rsi:.1f} "
                          f"pullback={pullback:.2f}% vol_ok={vol_ok}")
        print("  PASS")
    finally:
        await client.close_connection()


async def test_vpn_check():
    print("\n=== Test 11: VPN/Proxy Check ===")
    config = load_config()
    from monitor.vpn_check import VpnCheck
    vpn = VpnCheck(config)
    ok = await vpn.check_once()
    print(f"  Proxy status: {'OK' if ok else 'DOWN'}")
    print("  PASS")


async def main():
    logger = setup_logger("INFO")
    tests = [
        ("Config Loading", test_config_loading),
        ("SQLite Database", test_database),
        ("Kline Cache + Indicators", test_kline_cache_and_indicators),
        ("Position Sizing", test_position_sizing),
        ("Daily Guard", test_daily_guard),
        ("Strategy Signal", test_strategy_signal),
        ("DingTalk Sign", test_dingtalk_sign),
        ("Binance Connection", test_binance_connection),
        ("Market Feed Warmup", test_market_feed_warmup),
        ("Full Strategy Evaluation", test_full_strategy_evaluation),
        ("VPN Check", test_vpn_check),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())
