import os
import re
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from typing import Optional
from pydantic import BaseModel

from api.auth import verify_api_key

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(verify_api_key)])

_PERSIST = Path(os.environ.get("PERSIST_DIR", Path(__file__).resolve().parent.parent))
LOG_DIR = _PERSIST / "logs"
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s*\| ([\w.]+) \| (.+)$"
)


class LogEntry(BaseModel):
    timestamp: str
    level: str
    module: str
    message: str


@router.get("", response_model=list[LogEntry])
async def query_logs(
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD"),
    start_date: Optional[str] = Query(None, description="开始日期"),
    end_date: Optional[str] = Query(None, description="结束日期"),
    level: Optional[str] = Query(None, description="日志级别 DEBUG/INFO/WARNING/ERROR"),
    keyword: Optional[str] = Query(None, description="消息关键词"),
    limit: int = Query(200, description="返回条数上限", ge=1, le=2000),
):
    dates = _resolve_dates(date, start_date, end_date)
    results = []
    for d in dates:
        log_file = LOG_DIR / d / "app.log"
        if not log_file.exists():
            continue
        entries = _parse_log_file(log_file, level, keyword, limit - len(results))
        results.extend(entries)
        if len(results) >= limit:
            break
    return results[:limit]


def _resolve_dates(date, start_date, end_date):
    if date:
        return [date]
    dates = []
    if start_date and end_date:
        current = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current = current.replace(day=current.day + 1) if current.day < 28 else _next_month(current)
    elif start_date:
        dates.append(start_date)
    elif end_date:
        dates.append(end_date)
    else:
        dates.append(datetime.utcnow().strftime("%Y-%m-%d"))
    return sorted(dates, reverse=True)


def _next_month(dt):
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1)
    return dt.replace(month=dt.month + 1, day=1)


def _parse_log_file(path, level_filter, keyword, max_entries):
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LOG_PATTERN.match(line.strip())
            if not m:
                continue
            ts, lvl, mod, msg = m.groups()
            if level_filter and lvl != level_filter:
                continue
            if keyword and keyword.lower() not in msg.lower():
                continue
            entries.append(LogEntry(timestamp=ts, level=lvl, module=mod, message=msg))
            if len(entries) >= max_entries:
                break
    return entries
