import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes_logs import router as logs_router
from api.routes_account import router as account_router, set_client
from api.routes_trades import router as trades_router, set_db as set_trades_db
from api.routes_signals import router as signals_router, set_db as set_signals_db

logger = logging.getLogger("multi_strategy")

_client = None
_db = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _client:
        set_client(_client)
    if _db:
        set_trades_db(_db)
        set_signals_db(_db)
    logger.info(f"API server started on port {_port}")
    yield
    logger.info("API server stopped")


def create_app(client=None, db=None, port: int = 8000) -> FastAPI:
    global _client, _db, _port
    _client = client
    _db = db
    _port = port

    app = FastAPI(
        title="Multi-Strategy Trading API",
        description="外部 AI 查询接口",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(logs_router)
    app.include_router(account_router)
    app.include_router(trades_router)
    app.include_router(signals_router)
    return app
