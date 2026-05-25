from fastapi import APIRouter, Query, Depends
from typing import Optional

from api.auth import verify_api_key

router = APIRouter(prefix="/signals", tags=["signals"], dependencies=[Depends(verify_api_key)])

_db = None


def set_db(db):
    global _db
    _db = db


@router.get("")
async def query_signals(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    symbol: Optional[str] = Query(None, description="交易对"),
    limit: int = Query(100, description="返回条数上限", ge=1, le=1000),
):
    if not _db:
        return {"error": "Database not initialized"}
    return await _db.query_signals(
        start_date=start_date, end_date=end_date,
        symbol=symbol, limit=limit,
    )
