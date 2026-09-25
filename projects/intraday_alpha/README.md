# intraday_alpha — 单标的日内 Alpha 投研 → 实盘

基于 [vnpy](https://github.com/vnpy/vnpy) 的端到端最小闭环：
**离线研究（CSV → 因子 → LightGBM → 信号 JSON）** → **实盘消费信号（vnpy CtaTemplate → CTP 下单）**。

```
1-min bar (CSV / CTP)
       │
       ▼
   research/
   ├── run.py
   │   └── ingest → build → train → predict → emit → backtest
   └── intraday_live_signal.json  ◄──── 原子写（tmp + os.replace）
                                          │
                                          ▼
   live/
   ├── strategy/intraday_alpha_strategy.py   # CtaTemplate：每根 1-min bar 读信号 → 下单
   └── ctp_runner.py                         # EventEngine + MainEngine + CtpGateway + CtaStrategyApp
```

---

## 1. 目录结构

```
projects/intraday_alpha/
├── .env.example                  # SIMNOW 凭证模板（cp 为 .env 后填入）
├── README.md                     # 本文件
├── research/                     # 第 3 章：投研流水线（不连行情网关）
│   ├── run.py                    # 编排器：CSV → AlphaLab → 模型 → 信号 → 回测
│   ├── intraday_alpha/           # 子包
│   │   ├── dataset.py            #   IntradayAlphaDataset(AlphaDataset)：~30 个 1-min factor + 3 个日内时间特征
│   │   ├── model.py              #   IntradayLgbModel(AlphaModel)：LightGBM Huber 损失
│   │   ├── strategy.py           #   IntradayTopStrategy(AlphaStrategy)：单标的 on/off
│   │   └── config.json           #   所有可调参数
│   └── runs/                     # 回测产物（gitignored：runs/cache/）
│       └── intraday_lgb_{daily_pnl.parquet, statistics.json}
└── live/                         # 第 4 章：实盘启动（连 SIMNOW + 下单）
    ├── README.md                 # 启动步骤 / 调试手册
    ├── ctp_runner.py             # headless 入口
    └── strategy/
        └── intraday_alpha_strategy.py  # IntradayAlphaStrategy(CtaTemplate)
```

---

## 2. 研究流水线

研究端只读 CSV，不连行情网关。消费 1-min OHLCV CSV，列名：`datetime, open, high, low, close, volume, turnover, open_interest`（`vwap` 可选，无则自动 `turnover/volume`）。

```bash
cd research

# 一次性：灌数据到 lab
python run.py --ingest-csv ../../../data/au2612_1min.csv

# 全流程：构建 → 训练 → 预测 → 写 live_signal.json → BacktestingEngine
python run.py --show-chart

# 只产信号（复用缓存模型）
python run.py --predict-only

# 只训练 + 预测，不跑回测
python run.py --no-backtest

# 覆盖默认时间窗口
python run.py --start 2026-01-05 --end 2026-09-20
```

回测后会在 `research/runs/` 写：
- `intraday_lgb_daily_pnl.parquet` —— 逐日 PnL（原始事实）
- `intraday_lgb_statistics.json` —— 年化 / Sharpe / 回撤 等聚合指标
- 在项目根写 `lab/intraday/signal/intraday_live_signal.json` —— live 消费用

### 2.1 信号 JSON 格式

```json
{
  "version": "2026-09-25T11:42:00Z",
  "vt_symbol": "au2612.SHFE",
  "rows": [
    {"datetime": "2026-01-05T09:00:00", "vt_symbol": "au2612.SHFE", "signal": 0.000752},
    ...
  ]
}
```

每根 1-min bar live 策略用 `bar.datetime.strftime("%Y-%m-%dT%H:%M:%S")` 查表取信号。

### 2.2 调参

所有参数都在 `research/intraday_alpha/config.json`：

- `vt_symbol / contract` —— 标的（默认 `au2612.SHFE`，size=1000，pricetick=0.02）
- `train_period / valid_period / test_period` —— 切分窗口
- `model_hyperparams` —— LGB 超参（huber / alpha / num_leaves / min_data_in_leaf / …）
- `live.signal_threshold_long / short` —— 信号阈值（5-min forward return 量级）
- `live.price_add_ticks` —— 限价单超过最新价的 tick 数
- `live.stale_signal_hours` —— 信号版本超过 N 小时认为失效

---

## 3. 实盘（详见 [`live/README.md`](live/README.md)）

两步：先离线跑研究流水线产 `live_signal.json`，再起 live 消费它。

```bash
# 步骤 A：训练 + 写 live_signal.json
cd research && python run.py --predict-only

# 步骤 B：起 live（连 SIMNOW、按 1-min bar 下单）
cd ../live && python ctp_runner.py
```

---

## 4. 凭证

所有 SIMNOW 凭证走环境变量（[`../../README.md`](../../README.md) §3）：

```bash
cd projects/intraday_alpha
cp .env.example .env
# 编辑 .env 填入：CTP_USERID / CTP_PASSWORD / CTP_BROKERID / CTP_TD_ADDRESS / CTP_MD_ADDRESS / CTP_APPID / CTP_AUTH_CODE
# 可选：INTRADAY_VT_SYMBOL（默认 au2612.SHFE）/ INTRADAY_SIGNAL_PATH
```

---

## 5. 常见问题

| 问题 | 排错 |
|---|---|
| `polars` 找不到 / `vnpy` 找不到 | 用 veighna_studio 自带 Python（`E:/veighna_studio/python.exe`），别用系统默认 Python 3.8 |
| 回测报"成交记录为空" | `config.json` 里 `test_period` 的 end 日期必须落在已 import 的 1-min bar 范围内 |
| 回测统计 ZeroDivisionError | 测试段 < 2 天会因 `max_drawdown=0` 触发；`run.py` 已 try/except 兜底写入 `skip_reason` |
| CTA 策略类加载不到 | `IntradayAlphaStrategy` 必须在 `live/strategy/` 目录下；`ctp_runner.py` 会自动把它加进 sys.path |
| SIMNOW "未授权" | 检查 `.env` 里 `CTP_APPID` 和 `CTP_AUTH_CODE` 是否与 SIMNOW 后台签发的一致 |
| 信号 JSON 读不到 | 确认 `INTRADAY_SIGNAL_PATH`（默认指向 `research/lab/intraday/signal/...`）与 `config.json["signal_path"]` 兼容 |
| vnpy 包导入失败 | 先 `pip install -e raw/vnpy raw/vnpy_ctp raw/vnpy_ctastrategy`，再 `pip install vnpy vnpy_ctp vnpy_ctastrategy` |