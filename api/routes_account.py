from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from api.auth import verify_api_key

router = APIRouter(prefix="/account", tags=["account"], dependencies=[Depends(verify_api_key)])

_client = None


def set_client(client):
    global _client
    _client = client


class BalanceInfo(BaseModel):
    futures_balance: float
    spot_balance: float
    total: float


class PositionInfo(BaseModel):
    symbol: str
    quantity: float
    entry_price: float
    unrealized_pnl: float
    leverage: str


class AccountResponse(BaseModel):
    balance: Optional[BalanceInfo] = None
    positions: list[PositionInfo] = []
    error: Optional[str] = None


@router.get("", response_model=AccountResponse)
async def get_account():
    if not _client:
        return AccountResponse(error="Binance client not initialized")

    try:
        balances = await _client.futures_account_balance()
        futures_usdt = 0.0
        for b in balances:
            if b.get("asset") == "USDT":
                futures_usdt = float(b.get("balance", 0))
                break

        try:
            spot_bal = await _client.get_asset_balance(asset="USDT")
            spot_usdt = float(spot_bal.get("free", 0)) + float(spot_bal.get("locked", 0)) if spot_bal else 0.0
        except Exception:
            spot_usdt = 0.0

        balance = BalanceInfo(
            futures_balance=futures_usdt,
            spot_balance=spot_usdt,
            total=futures_usdt + spot_usdt,
        )
    except Exception as e:
        return AccountResponse(error=f"Balance query failed: {e}")

    try:
        raw_positions = await _client.futures_position_information()
        positions = []
        for p in raw_positions:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                positions.append(PositionInfo(
                    symbol=p["symbol"],
                    quantity=amt,
                    entry_price=float(p["entryPrice"]),
                    unrealized_pnl=float(p["unRealizedProfit"]),
                    leverage=p.get("leverage", "N/A"),
                ))
        return AccountResponse(balance=balance, positions=positions)
    except Exception as e:
        return AccountResponse(balance=balance, error=f"Position query failed: {e}")
