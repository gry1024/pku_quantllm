# projects/ — 课程 7 步交付物索引

| 子项目 | 对应作业步 | 一句话定位 |
|---|---|---|
| [`ctp_demo/`](ctp_demo/) | Step 3 + Step 4 | 从底层 Python API 直连 SIMNOW，打印行情推送 |
| [`ctp_breakout_demo/`](ctp_breakout_demo/) | Step 6 | 突破价格阈值 + TD API 下单：行情 → 决策 → 下单 → 回报 |
| [`alpha_mining_demo/`](alpha_mining_demo/) | Step 7 | LLM 驱动的因子挖掘（动量 + 跳空，4 轮 × 10-20 因子 + 终局报告） |
| [`intraday_alpha/`](intraday_alpha/) | Capstone | 研究（CSV → LightGBM → 信号 JSON）→ 实盘（vnpy CtaTemplate → CTP 下单）端到端 |

详见各子项目 README。整体仓库导航见 [`../README.md`](../README.md)。