# Multi-Strategy Trading API 文档

Base URL: `http://<host>:9450`

所有接口需要在请求头中携带 API Key：

```
X-Api-Key: <your_api_key>
```

API Key 配置在 `.env` 的 `API_KEY` 字段。

---

## 1. 账户信息

### GET /account

返回合约/现货余额及当前持仓。

**Response:**

```json
{
  "balance": {
    "futures_balance": 1000.50,
    "spot_balance": 200.00,
    "total": 1200.50
  },
  "positions": [
    {
      "symbol": "BTCUSDT",
      "quantity": 0.1,
      "entry_price": 68000.0,
      "unrealized_pnl": 15.5,
      "leverage": "10"
    }
  ],
  "error": null
}
```

- `positions` 只返回有仓位的交易对（`positionAmt != 0`）
- `balance` 或 `positions` 查询失败时，对应字段为 null/空数组，`error` 字段包含错误信息

---

## 2. 交易记录

### GET /trades

查询历史交易记录。

**Query Parameters:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_date | string | 否 | 开始日期，格式 YYYY-MM-DD |
| end_date | string | 否 | 结束日期，格式 YYYY-MM-DD |
| status | string | 否 | 状态过滤：`open` 或 `closed` |
| symbol | string | 否 | 交易对，如 BTCUSDT |
| limit | int | 否 | 返回条数上限，默认 100，最大 1000 |

**Response:**

```json
[
  {
    "id": 1,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "quantity": 0.01,
    "entry_price": 68000.0,
    "exit_price": 68500.0,
    "stop_loss_price": 67500.0,
    "take_profit_price": 69000.0,
    "pnl": 5.0,
    "status": "closed",
    "entry_time": "2026-05-25T12:00:00",
    "exit_time": "2026-05-25T14:00:00",
    "strategy_params": "{\"atr_pct\": 3.5}"
  }
]
```

**常用查询示例：**

- 当前持仓：`GET /trades?status=open`
- 某交易对历史：`GET /trades?symbol=BTCUSDT&status=closed`
- 最近 N 笔：`GET /trades?limit=10`

---

## 3. 信号记录

### GET /signals

查询扫描产生的交易信号。

**Query Parameters:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_date | string | 否 | 开始日期，格式 YYYY-MM-DD |
| end_date | string | 否 | 结束日期，格式 YYYY-MM-DD |
| symbol | string | 否 | 交易对，如 BTCUSDT |
| limit | int | 否 | 返回条数上限，默认 100，最大 1000 |

**Response:**

```json
[
  {
    "id": 1,
    "symbol": "PHBUSDT",
    "direction": "LONG",
    "atr_pct": 3.37,
    "pullback_pct": 1.03,
    "rsi": 43.8,
    "volume": 123456.78,
    "volume_ma": 100000.0,
    "ema": 0.85,
    "close": 1.234,
    "params": "{\"sl_atr_mult\": 1.5}",
    "triggered_at": "2026-05-25T12:10:28"
  }
]
```

**常用查询示例：**

- 今日信号：`GET /signals?start_date=2026-05-25`
- 某交易对信号：`GET /signals?symbol=PHBUSDT`

---

## 4. 日志查询

### GET /logs

查询系统运行日志。

**Query Parameters:**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| date | string | 否 | 指定日期，格式 YYYY-MM-DD（精确查询） |
| start_date | string | 否 | 开始日期（范围查询，与 end_date 配合） |
| end_date | string | 否 | 结束日期 |
| level | string | 否 | 日志级别：DEBUG / INFO / WARNING / ERROR |
| keyword | string | 否 | 消息关键词（不区分大小写） |
| limit | int | 否 | 返回条数上限，默认 200，最大 2000 |

**Response:**

```json
[
  {
    "timestamp": "2026-05-25 20:10:28",
    "level": "INFO",
    "module": "multi_strategy",
    "message": "Scan signal: PHBUSDT ATR%=3.37 pullback=1.03% RSI=43.8"
  }
]
```

**优先级：** `date` 参数优先于 `start_date`/`end_date`。都不传时默认查当天。

**常用查询示例：**

- 今日错误：`GET /logs?level=ERROR`
- 搜索关键词：`GET /logs?keyword=PHBUSDT`
- 指定日期：`GET /logs?date=2026-05-24&level=WARNING`

---

## 错误响应

**401 Unauthorized** — API Key 缺失或错误：

```json
{"detail": "Invalid API key"}
```

**500 Internal Server Error** — 服务端未配置 API_KEY：

```json
{"detail": "API_KEY not configured"}
```

---

## curl 示例

```bash
# 查询账户
curl -H "X-Api-Key: msk-2026-multi-strategy" http://localhost:9450/account

# 查询当前持仓
curl -H "X-Api-Key: msk-2026-multi-strategy" "http://localhost:9450/trades?status=open"

# 查询今日信号
curl -H "X-Api-Key: msk-2026-multi-strategy" "http://localhost:9450/signals?start_date=2026-05-25"

# 查询错误日志
curl -H "X-Api-Key: msk-2026-multi-strategy" "http://localhost:9450/logs?level=ERROR&limit=50"
```
