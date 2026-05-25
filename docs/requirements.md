# 多标策略风控实时监控系统 — 需求文档 v1.0

> 最后更新：2026-05-25
> 状态：需求对齐完成，待开发

---

## 1. 项目概述

### 1.1 目标
开发一个接入币安合约（模拟盘/实盘）的实时监控及自动下单程序，实现多标的 EMA 回调策略的信号检测、自动开平仓、风控管理和消息通知。

### 1.2 核心原则
- **固定风险模型**：单笔最大亏损固定为 0.21 USDT，不复利
- **单品种持仓**：任意时刻全局最多持有 1 个标的的仓位
- **条件单执行**：止损止盈由交易所条件单执行，程序负责联动撤销

---

## 2. 技术选型

| 项目 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | asyncio 单进程 |
| 币安接口 | python-binance | 原生 API，条件单支持完善 |
| 架构 | 单进程 + asyncio | 轻量，systemd 托管 |
| 配置 | config.yaml + .env | YAML 存策略参数，.env 存敏感信息 |
| 状态存储 | state.json | 运行时状态（持仓、止损计数） |
| 交易记录 | SQLite | 交易历史、资金费率、统计查询 |
| 通知 | 钉钉 Sign 机器人 | Webhook + Secret 签名 |
| 部署 | 跨平台（Windows 开发，Linux VPS 生产） | systemd / 任务计划托管 |

---

## 3. 配置管理

### 3.1 config.yaml 结构

```yaml
mode: watchlist  # watchlist | scan

binance:
  testnet: true  # true=模拟盘, false=实盘
  proxy:
    http: "http://127.0.0.1:7897"
    https: "http://127.0.0.1:7897"

strategy:
  fixed_loss: 0.21          # USDT，单笔固定亏损
  leverage: 3                # 固定杠杆倍数
  kline_interval: "1h"       # K线周期
  atr_period: 14             # ATR 计算周期
  rsi_period: 14             # RSI 计算周期
  ema_period: 5              # EMA 计算周期
  volume_ma_period: 20       # 成交量均量周期
  atr_pct_threshold: 0.5     # ATR% 最低阈值（%）
  pullback_min_pct: 0.5      # 回调幅度下限（%）
  pullback_max_pct: 2.0      # 回调幅度上限（%）
  rsi_min: 30                # RSI 下限
  rsi_max: 70                # RSI 上限
  reward_risk: 3             # 默认盈亏比
  limit_order_timeout_sec: 30 # 限价单超时转市价（秒）

risk:
  max_daily_stops: 2         # 每日最大连续止损次数
  total_asset_target: 22.5   # USDT，总资产目标
  rebalance_threshold_pct: 20 # 偏离阈值（%）

watchlist:
  - symbol: LINKUSDT
  - symbol: SOLUSDT
  - symbol: AVAXUSDT
  - symbol: BTCUSDT
  - symbol: ETHUSDT
  - symbol: DOGEUSDT
    reward_risk: 1.5         # 按标的覆盖示例

schedule:
  funding_report: "08:05"
  heartbeat:
    - "08:00"
    - "12:00"
    - "18:00"
  vpn_check_interval_sec: 300

log_level: INFO  # DEBUG | INFO | WARNING | ERROR
```

### 3.2 .env 结构

```env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx
```

### 3.3 按标的参数覆盖
config.yaml 中 watchlist 下的每个标的均可选择性覆盖 `strategy` 中的任意参数（如 `reward_risk`、`atr_pct_threshold` 等），未覆盖的参数使用全局默认值。

---

## 4. 行情数据

### 4.1 数据源
- **启动预热**：REST API 拉取每个标的最近 100 根 1H K 线，用于指标计算预热
- **实时更新**：WebSocket 订阅 kline 流，增量更新本地 K 线缓存
- **断线补齐**：WebSocket 重连后，REST 补齐断线期间缺失的 K 线数据

### 4.2 指标计算
基于最近一根**已收盘**的 1H K 线计算：

| 指标 | 计算方式 | 用途 |
|------|----------|------|
| ATR(14) | 14 周期平均真实波幅 | ATR% = ATR / 收盘价 × 100 |
| RSI(14) | 14 周期相对强弱指数 | 判断是否在 [rsi_min, rsi_max] 区间 |
| EMA(5) | 5 周期指数移动均线 | 回调幅度基准 |
| Volume MA(20) | 20 周期成交量均值 | 判断缩量回调 |

### 4.3 信号评估时机
- **触发点**：每根 1H K 线收盘时（WebSocket 监听到新 K 线开盘）
- **评估窗口**：使用刚收盘的那根 K 线数据计算所有指标

---

## 5. 进场逻辑

### 5.1 进场条件（全部满足）

1. **ATR% 过滤**：前一根 1H K 线的 ATR% > 标的设定阈值
2. **回调幅度**：回调% = (EMA5 - 收盘价) / EMA5 × 100，落在 [pullback_min_pct, pullback_max_pct] 区间内（EMA5 在上方为正值）
3. **RSI 区间**：前一根 RSI 在 [rsi_min, rsi_max] 区间内
4. **成交量确认**：前一根成交量 < 20 周期均量（缩量回调）
5. **单品种持仓**：当前没有任何未平仓仓位（全局唯一）

### 5.2 扫描模式（scan mode）
当 `mode: scan` 时：
1. **第一轮粗筛**：REST 批量拉取所有 USDT 永续合约最近 20 根 1H K 线，计算 ATR%，保留 ATR% > 阈值的标的
2. **第二轮细筛**：对粗筛通过的标的计算 RSI、EMA5、回调幅度、成交量，全部满足才纳入候选
3. **排序开仓**：候选标的按 ATR% 降序排序，取第一个开仓

### 5.3 开仓执行

#### 仓位计算
```
名义价值 = fixed_loss / (ATR% / 100)
开仓数量 = 名义价值 / 当前K线开盘价
保证金 = 名义价值 / leverage（固定 3 倍）
```

#### 下单流程
1. **LIMIT 优先**：以当前市场价挂限价单（不偏移）
2. **超时转 MARKET**：30 秒内未成交则撤销 LIMIT 单，立即下 MARKET 单
3. **开仓成功后**：立即设置止损止盈条件单（见第 6 节）

---

## 6. 出场逻辑

### 6.1 止损止盈价格
```
止损价 = 开仓价 × (1 - 1 × ATR%)
止盈价 = 开仓价 × (1 + reward_risk × ATR%)
```
- 止损 → 亏损约 0.21 USDT
- 止盈 → 盈利约 reward_risk × 0.21 USDT

### 6.2 条件单设置
开仓成功后立即通过 REST API 挂两个条件单：
- **止损单**：STOP_MARKET，触发价 = 止损价
- **止盈单**：TAKE_PROFIT_MARKET，触发价 = 止盈价

### 6.3 联动撤销（OCO Watcher）
币安合约不支持原生 OCO，程序自行实现联动：

**主通道——WebSocket：**
- 订阅账户订单流（USER_DATA stream）
- 检测到止盈单 FILLED → 立即撤销对应止损单
- 检测到止损单 FILLED → 立即撤销对应止盈单
- 正常延迟 < 1 秒

**兜底通道——轮询：**
- 每 30 秒查询一次未完成条件单
- 发现一方已成交但另一方仍挂单 → 立即撤销
- WebSocket 断线时此通道作为唯一保障

---

## 7. 风控模块

### 7.1 日间风控（连续亏损暂停）
- 当日连续 2 笔止损 → 策略自动暂停，当日不再开仓
- 状态持久化到 state.json，跨 session 保留
- 每日 00:00 重置连续止损计数

### 7.2 全局风控（资产偏离检测）
- 总资产目标：合约账户 + 现货账户 = 22.5 USDT
- 偏离阈值：±20%
- 超出阈值时钉钉提醒，由用户手动操作划转
- 检测频率：每小时一次（随信号评估触发）

### 7.3 代理连通性检测
- 每 5 分钟通过代理请求币安 API（GET /fapi/v1/time）
- 不通时：钉钉告警 + 暂停开仓（已有仓位不受影响）
- 恢复后：自动恢复开仓能力 + 钉钉通知恢复

---

## 8. 定时任务

### 8.1 资金费率日报（每日 08:05）
- 统计当日资金费率收支
- 统计累计资金费率收支
- 计算净盈亏 = 交易盈亏 + 资金费率收支
- 钉钉推送报告

### 8.2 心跳播报（每日 08:00 / 12:00 / 18:00）
- 当前持仓状态
- 账户余额
- 当日盈亏
- 连续止损计数
- 代理状态
- 钉钉推送

---

## 9. 通知规范

### 9.1 通知渠道
钉钉 Sign 机器人（Webhook + Secret 签名验证），代码中做通知抽象层，预留扩展能力。

### 9.2 通知类型

| 事件 | 级别 | 内容 |
|------|------|------|
| 开仓成交 | INFO | 标的、方向、数量、入场价、止损价、止盈价 |
| 止盈成交 | INFO | 标的、盈亏金额、盈亏比 |
| 止损成交 | WARNING | 标的、亏损金额、当日连续止损次数 |
| 连续止损暂停 | WARNING | 当日已暂停开仓 |
| 资产偏离 | WARNING | 当前总资产、偏离百分比 |
| 代理断线 | ERROR | 代理不通，暂停开仓 |
| 代理恢复 | INFO | 代理恢复，恢复开仓 |
| 资金费率日报 | INFO | 费率收支明细 |
| 心跳播报 | INFO | 系统状态摘要 |
| 程序异常 | ERROR | 异常信息 + traceback |

---

## 10. 容错与运行保障

### 10.1 WebSocket 断线
- 自动重连，指数退避（1s → 2s → 4s → 8s → ... → 最大 60s）
- 重连后 REST 补齐断线期间 K 线数据

### 10.2 API 限频（429）
- 遇到 429 自动 sleep（按 Retry-After 头）并重试
- 不丢弃请求

### 10.3 程序崩溃
- Linux：systemd 自动重启
- Windows：任务计划自动重启
- 重启后从 state.json 恢复运行状态，从交易所同步持仓和条件单

### 10.4 优雅停机（SIGINT / SIGTERM）
- **保留**：已有仓位和已触发的止损止盈条件单留在交易所
- **撤销**：未成交的 LIMIT 开仓单自动撤销
- **持久化**：将当前状态写入 state.json
- 下次启动时从交易所同步状态继续管理

---

## 11. 项目结构

```
multi-strategy/
├── config.yaml              # 策略/风控/通知配置
├── .env                     # API Key、钉钉 Secret
├── state.json               # 运行状态（持仓、止损计数等）
├── trades.db                # SQLite 交易记录
├── requirements.txt         # 依赖
├── main.py                  # 入口，asyncio 事件循环
├── core/
│   ├── engine.py            # 主引擎：协调行情/信号/下单
│   ├── strategy.py          # 信号计算（ATR/RSI/EMA/回调/成交量）
│   ├── executor.py          # 下单执行（LIMIT优先+超时转MARKET）
│   └── oco_watcher.py       # 止损止盈联动撤销（WebSocket+轮询兜底）
├── risk/
│   ├── position_sizing.py   # 固定风险仓位计算
│   ├── daily_guard.py       # 连续亏损暂停
│   └── balance_monitor.py   # 资产偏离检测
├── data/
│   ├── market_feed.py       # 行情获取（REST预热+WebSocket增量）
│   ├── kline_cache.py       # K线缓存与指标预热
│   └── database.py          # SQLite 交易记录
├── notify/
│   └── dingtalk.py          # 钉钉推送（抽象层，预留扩展）
├── monitor/
│   ├── vpn_check.py         # 代理连通性检测（请求币安API）
│   ├── funding_rate.py      # 资金费率统计
│   └── heartbeat.py         # 心跳播报
└── utils/
    ├── config_loader.py     # YAML + .env 加载
    └── logger.py            # 日志配置
```

---

## 12. 开发计划

### Phase 1：基础框架（模拟盘）
1. 项目骨架搭建，config.yaml + .env 加载
2. 行情模块：REST 预热 + WebSocket 实时 K 线
3. 指标计算：ATR/RSI/EMA/Volume MA
4. SQLite 数据库初始化

### Phase 2：策略核心（模拟盘）
5. 进场信号判断（5 个条件）
6. 仓位计算 + LIMIT 优先下单
7. 止损止盈条件单设置
8. OCO Watcher（WebSocket + 轮询联动撤销）

### Phase 3：风控与通知（模拟盘）
9. 日间风控（连续止损暂停 + state.json）
10. 钉钉通知模块
11. 资产偏离检测
12. 代理连通性检测

### Phase 4：定时任务（模拟盘）
13. 资金费率日报
14. 心跳播报
15. 扫描模式（全市场标的筛选）

### Phase 5：实盘验证
16. 模拟盘完整测试通过
17. config.yaml 切换实盘
18. 小仓位实盘验证
19. systemd / 任务计划部署配置

---

## 13. 验收标准

1. 模拟盘上能完整运行：信号检测 → 开仓 → 止损/止盈联动撤销 → 状态持久化
2. OCO Watcher 在 WebSocket 正常和断线两种情况下都能正确撤销对手单
3. 连续 2 笔止损后当日不再开仓，次日 00:00 自动恢复
4. 所有通知类型能正确推送到钉钉
5. 程序崩溃重启后能从 state.json 恢复状态并继续管理已有仓位
6. 优雅停机不丢失仓位、不遗留未管理的条件单
7. scan 模式能从全市场中按 ATR% 降序筛选并开仓
8. 按标的覆盖参数能正确生效
