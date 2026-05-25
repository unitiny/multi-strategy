import logging

logger = logging.getLogger("multi_strategy")


def calc_position(fixed_loss: float, atr_pct: float, close_price: float,
                  leverage: int) -> dict:
    notional = fixed_loss / (atr_pct / 100)
    quantity = notional / close_price
    margin = notional / leverage
    return {
        "notional": round(notional, 4),
        "quantity": round(quantity, 6),
        "margin": round(margin, 4),
    }


def calc_stop_take(entry_price: float, atr_pct: float,
                   reward_risk: float) -> dict:
    sl_price = entry_price * (1 - atr_pct / 100)
    tp_price = entry_price * (1 + reward_risk * atr_pct / 100)
    return {
        "stop_loss": round(sl_price, 6),
        "take_profit": round(tp_price, 6),
    }
