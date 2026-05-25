# 多标策略风控实时监控系统

接入币安合约（模拟盘/实盘）的实时监控及自动下单程序，实现多标的 EMA 回调策略的信号检测、自动开平仓、风控管理和钉钉消息通知。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：

| 包 | 用途 |
|---|------|
| python-binance | 币安 REST/WebSocket API |
| aiohttp | 异步 HTTP + WebSocket 客户端 |
| websockets | WebSocket 协议支持 |
| pyyaml | YAML 配置解析 |
| python-dotenv | .env 环境变量加载 |
| numpy | 技术指标计算 |
| aiosqlite | SQLite 异步操作（随 Python 附带） |

### 2. 配置

#### `.env` — 敏感凭证

```env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx
```

#### `config.yaml` — 策略与风控参数

关键字段说明：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `watchlist` | `watchlist`=观察列表模式，`scan`=全市场扫描模式 |
| `binance.testnet` | `true` | `true`=模拟盘，`false`=实盘 |
| `binance.proxy` | — | HTTP/HTTPS 代理地址 |
| `strategy.fixed_loss` | `0.21` | 单笔固定亏损 USDT |
| `strategy.leverage` | `3` | 杠杆倍数 |
| `strategy.reward_risk` | `3` | 默认盈亏比 |

watchlist 下可按标的覆盖任意策略参数：

```yaml
watchlist:
  - symbol: DOGEUSDT
    reward_risk: 1.5       # 覆盖全局 reward_risk
    atr_pct_threshold: 0.8 # 覆盖全局 ATR% 阈值
```

### 3. 运行

```bash
# 前台运行（Ctrl+C 优雅停机）
python main.py

# 后台运行（Linux）
nohup python main.py > /dev/null 2>&1 &

# 后台运行（Windows PowerShell）
Start-Process python -ArgumentList "main.py" -WindowStyle Hidden
```

### 4. 测试

```bash
python test_system.py
```

测试覆盖：配置加载、SQLite、K线缓存与指标计算、仓位计算、日间风控、信号检测、钉钉签名、币安连接、行情预热、策略评估、代理检测。

---

## 运维命令

### systemd 托管（Linux VPS）

创建 `/etc/systemd/system/multi-strategy.service`：

```ini
[Unit]
Description=Multi-Strategy Trading System
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/multi-strategy
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable multi-strategy
sudo systemctl start multi-strategy
sudo systemctl status multi-strategy    # 查看状态
sudo systemctl stop multi-strategy      # 优雅停机
journalctl -u multi-strategy -f         # 查看日志
```

### Windows 任务计划

```powershell
# 创建任务（开机自启 + 崩溃重启）
schtasks /create /tn "MultiStrategy" /tr "python E:\path\to\multi-strategy\main.py" /sc onstart /ru your_user /rl highest

# 手动启停
schtasks /run /tn "MultiStrategy"
schtasks /end /tn "MultiStrategy"
```

---

## 项目结构

```
multi-strategy/
├── config.yaml              # 策略/风控/通知配置
├── .env                     # API Key、钉钉 Secret
├── state.json               # 运行状态（持仓、止损计数）
├── trades.db                # SQLite 交易记录
├── app.log                  # 运行日志
├── requirements.txt         # Python 依赖
├── main.py                  # 入口，asyncio 事件循环
├── test_system.py           # 自动化测试
├── core/
│   ├── engine.py            # 主引擎：协调行情/信号/下单/通知
│   ├── strategy.py          # 信号计算（ATR/RSI/EMA/回调/成交量）
│   ├── executor.py          # 下单执行（LIMIT优先+超时转MARKET）
│   └── oco_watcher.py       # 止损止盈联动撤销（WebSocket+轮询）
├── risk/
│   ├── position_sizing.py   # 固定风险仓位计算
│   ├── daily_guard.py       # 连续亏损暂停 + state.json 持久化
│   └── balance_monitor.py   # 资产偏离检测
├── data/
│   ├── market_feed.py       # 行情获取（REST预热+WebSocket增量）
│   ├── kline_cache.py       # K线缓存与指标计算
│   └── database.py          # SQLite 交易记录 CRUD
├── notify/
│   └── dingtalk.py          # 钉钉推送（Sign 签名验证）
├── monitor/
│   ├── vpn_check.py         # 代理连通性检测
│   ├── funding_rate.py      # 资金费率统计日报
│   └── heartbeat.py         # 心跳播报
└── utils/
    ├── config_loader.py     # YAML + .env 加载 + 按标的参数覆盖
    └── logger.py            # 日志配置（控制台 + 文件）
```

---

## 核心逻辑

### 进场条件（5 条全部满足）

1. ATR% > 设定阈值
2. 回调幅度落在 [pullback_min_pct, pullback_max_pct]
3. RSI 在 [rsi_min, rsi_max] 区间
4. 成交量 < 20 周期均量（缩量回调）
5. 当前无持仓（全局单品种）

### 仓位计算

```
名义价值 = fixed_loss / (ATR% / 100)
开仓数量 = 名义价值 / 当前价格
保证金   = 名义价值 / leverage
```

### 止损止盈

```
止损价 = 开仓价 × (1 - 1 × ATR%)
止盈价 = 开仓价 × (1 + reward_risk × ATR%)
```

### 风控

- 连续 2 笔止损 → 当日暂停开仓，次日 00:00 自动恢复
- 总资产偏离目标 ±20% → 钉钉告警
- 代理断线 → 暂停开仓 + 告警，恢复后自动恢复

---

## 切换实盘

修改 `config.yaml`：

```yaml
binance:
  testnet: false    # 改为 false
```

替换 `.env` 中的 API Key 为实盘 Key。

---

## 常见问题

**Q: WebSocket 连不上？**
检查代理配置，确保 `binance.proxy` 地址正确且代理服务正在运行。

**Q: 程序崩溃后重启？**
systemd / 任务计划会自动重启。重启后从 `state.json` 恢复状态，从交易所同步持仓和条件单。

**Q: 如何查看日志？**
- 控制台直接输出
- 文件日志：`app.log`
- Linux：`journalctl -u multi-strategy -f`
