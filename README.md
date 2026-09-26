# quantllm

PKU 当代量化交易系统课程作业。三项交付物对应 3 个汇报章节。

---

## 交付物 1 — vnpy / vnpy_ctp / vnpy_ctastrategy 完整学习笔记

Obsidian 笔记 vault，按源码模块分子目录（每个子目录 1 份 vault，每篇笔记按 01/02/... 编号对应一个具体模块/概念）。

| 模块 | vault 位置 | 篇数 |
|---|---|---|
| vnpy 主框架 | [`notes/vnpy/`](notes/vnpy/) | 11（10 概念 + index） |
| CTP 交易/行情接口 | [`notes/vnpy_ctp/`](notes/vnpy_ctp/) | 9（8 概念 + index） |
| CTA 策略模块 | [`notes/vnpy_ctastrategy/`](notes/vnpy_ctastrategy/) | 11（10 概念 + index） |

**入口**：每个 vault 的 `index.md` 是概念分层总览，按编号往下读即可覆盖该模块全部源码。

---

## 交付物 2 — 基于 vnpy_ctp 的最小交易系统 Demo

5 项能力拆 2 个入口（前置 → 行情 → 策略 → 下单链路拆开，便于汇报时逐项讲解）：

| 能力 | Demo 入口 | 启动命令 | 备注 |
|---|---|---|---|
| ① 柜台登录诊断（连接 + 终端认证 + 登录回报） | [`ctp_demo.py`](ctp_demo.py) | `python ctp_demo.py` | 打印 FrontConnected / Authenticate / UserLogin 完整握手状态机 |
| ② 合约订阅（au2612 / au2706 / rb2601 等） | [`ctp_demo.py`](ctp_demo.py) | 同上 | 登录后统一发 `subscribeMarketData` |
| ③ 行情持续打印 | [`ctp_demo.py`](ctp_demo.py) | 同上 | `onRtnDepthMarketData` 逐条带毫秒戳打最新价/买卖一/量/持仓 |
| ④ 价格触发提示 | [`ctp_breakout_demo.py`](ctp_breakout_demo.py) | `python ctp_breakout_demo.py` | 首个 tick 设阈值，后续 tick 比较 |
| ⑤ 策略实现（突破开仓）+ 限价发单 + 回报闭环 | [`ctp_breakout_demo.py`](ctp_breakout_demo.py) | 同上 / `BREAKOUT_TRIGGER_OFFSET=-0.5 python ctp_breakout_demo.py` 立即触发 | onRtnOrder / onRspOrderInsert / onRtnTrade 全打印 |

**前置**：复制 `.env.example` 为 `.env` 并填 SIMNOW 凭证（`CTP_USERID` / `CTP_PASSWORD` / `CTP_BROKERID` / `CTP_TD_ADDRESS` / `CTP_MD_ADDRESS` / `CTP_APPID` / `CTP_AUTH_CODE`）。代码内不持有任何默认账号。SIMNOW 7×24 仿真前置已默认指向 `tcp://182.254.243.31:40001/:40011`（传统 `180.168.146.131:10202` 已停服）。

**运行证明**（真实 SIMNOW 7×24 抓取）：
- [`runs/ctp_proof/ctp_demo_ticks.txt`](runs/ctp_proof/ctp_demo_ticks.txt) — 行情持续打印样本
- [`runs/ctp_proof/ctp_breakout_order.txt`](runs/ctp_proof/ctp_breakout_order.txt) — 突破触发 → 限价发单 → TradeID=152 成交 @ 928.88 完整回报

---

## 交付物 3 — 基于 vnpy.alpha 的因子挖掘

### 入口与运行命令

```bash
# 装依赖（一次）— VeighNa 私有源带 vnpy.alpha
pip install "vnpy>=4.4" "vnpy_ctp>=6.7" "vnpy_ctastrategy>=1.4" "vnpy[alpha]" \
    --index=https://pypi.vnpy.com

# 准备沪深300日线数据到 lab/csi300/（AlphaLab parquet 格式）— 一次
python lab/convert_qlib_to_alphalab.py      # 从 data/qlib_data/cn_data 转换（首次约 50s）

# 跑 pipeline
python eval_factors.py runs/gap_round_01/candidates.json   # 单轮候选因子：分段 IC/IR/胜率 → metrics.json
python final_report.py                                      # 终局报告：3 留存因子 → performance_report.md + 13 张 PNG 曲线
```

### 思路

- **族选择**：跳空方向（gap / overnight-vs-intraday）。直觉假设是"高开低走 / 低开高走"携带隔夜与日内信息差。
- **过拟合防治**：抛弃旧 valid 段（前三族累计加热 ~1130 次）、新 valid 段（2017–2018）从 0 起算、test 段（2019–2020.08）挖掘期间隐藏、lockbox 段（2020.09–2024）冻结为留存后只读观察。
- **三票制筛选**：绝对优势（|valid RankIR| > 0.19）/ 经济机制 / 最简形式 — 三项同时满足才进入终局。
- **复杂度消融链**：从裸形出发，逐级加乘法器（×换手 / ×振幅），只保留增益超过噪声门槛的链节。

### 做了什么

- 4 轮 × 56 候选因子（预算 80），早停 + 预算上限同时命中。
- **核心发现**：裸跳空动量被证伪；**真信号是隔夜−日内分解**（`GAP_DIFF4 = Σ₄(隔夜收益 − 日内收益)`）——日内部分是强反转、隔夜部分不反转，分解后隔夜豁免日内反转。train RankIR +0.379 / valid +0.276 / test +0.198。
- **留存 3 因子**（复杂度消融链）：
  - `GAP_DIFF4_BARE`（裸分解）  train +0.379 / valid +0.276 / test +0.198 — 全样本多空 Sharpe 3.38
  - `GAP_DIFF4_TURN`（分解 × 截面换手，主推）  train +0.372 / valid +0.292 / test +0.200 — Sharpe 3.66
  - `GAP_DIFF4_TRIPLE`（分解 × 换手 × 振幅，链终点）  train +0.367 / valid +0.280 / test +0.191 — Sharpe 4.64
- 与 PV_ANTI / REV_TRIPLE 族的正交性确认：截面水平约 20% 方差重叠，作为新采样而非替代。
- 证伪记录（冻结）：裸跳空动量、缩量跳空延续、隔夜占比、趋势门控、vwap 族、1 日窗。

### 回测曲线与分析报告

| 内容 | 位置 |
|---|---|
| **回测曲线**（13 张 PNG：分组累计 / 累积 RankIC / 多空累计 / 分年 RankIC × 3 因子 + 1 三因子对比） | [`runs/final/charts/`](runs/final/charts/) |
| **分析报告**（Markdown：总览表 + 分段 IC + 分段多空 + 分年 RankIC + 成本拖累估算） | [`runs/final/performance_report.md`](runs/final/performance_report.md) |
| 结构化明细（JSON） | [`runs/final/perf_detail.json`](runs/final/perf_detail.json) |
| 留存因子定义（key / 表达式 / 名义 / 已知 RankIR） | [`runs/final/factors.json`](runs/final/factors.json) |
| pipeline 输入示例（gap 族 round 1 的 16 候选因子） | [`runs/gap_round_01/candidates.json`](runs/gap_round_01/candidates.json) |
| 同一轮评估输出（metrics.json） | [`runs/gap_round_01/metrics.json`](runs/gap_round_01/metrics.json) |

股票池：沪深300 成分股 683 只日线（来自本地 qlib 二进制）；label = T+1→T+3 收益；分段区间见 [`config.json`](config.json)（train 2008–2016 / valid 2017–2018 / test 2019–2020.08）。

### 支撑脚本（实现细节，非汇报入口）

- [`lab/convert_qlib_to_alphalab.py`](lab/convert_qlib_to_alphalab.py) — qlib 二进制 → AlphaLab parquet 转换器（解码 `.day.bin` 第 0 个 float32 的 calendar start index）
- [`lab/component_json.py`](lab/component_json.py) — Python 3.13 on Windows `shelve.open` 失效的 JSON 替代（自动 patch `AlphaLab.save/load_component_data`）
- [`eval_factors.py`](eval_factors.py) — 单轮候选因子评估（vnpy.alpha AlphaDataset 表达式引擎 → 分段 IC/IR/胜率）
- [`final_report.py`](final_report.py) — 终局报告（十分组 + 多空 + 分年 + 成本拖累 + 图表生成）