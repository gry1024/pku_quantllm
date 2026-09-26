# quantllm

PKU 当代量化交易系统课程作业。

## 1. 学习笔记

位于 `notes/`，按仓库克隆的三个源码分子目录（每个子目录是一份 Obsidian vault）：

- `notes/vnpy/` — VeighNa 主框架 11 概念分层笔记
- `notes/vnpy_ctp/` — CTP 交易/行情接口 8 概念分层笔记
- `notes/vnpy_ctastrategy/` — CTA 策略模块 10 概念分层笔记

每篇笔记按 01/02/... 编号，对应一个具体模块/概念。

## 2. 最终产物运行命令（位于仓库根目录）

### (1) 对接 SIMNOW 行情 + 下单 demo

```bash
cp .env.example .env                       # 第一次跑前；填入 SIMNOW 凭证

python ctp_demo.py                          # 极简 demo：连接前置 → 订阅合约 → 持续打印 tick
python ctp_breakout_demo.py                 # 进阶 demo：行情 → 突破阈值 → 限价追价开仓 → 回报
#   BREAKOUT_TRIGGER_OFFSET=-0.5 python ctp_breakout_demo.py   # 立即触发验证发单链路
```

凭证全部走环境变量（`CTP_USERID` / `CTP_PASSWORD` / `CTP_TD_ADDRESS` / `CTP_MD_ADDRESS` / …），代码内不持有任何默认账号。
SIMNOW 7×24 仿真前置已配置为 `tcp://182.254.243.31:40001/:40011`（传统 `180.168.146.131:10202` 的 CTP 协议已停服，会立即断开）。
运行证明：`runs/ctp_proof/ctp_demo_ticks.txt`、`runs/ctp_proof/ctp_breakout_order.txt`。

### (2) 因子挖掘回测完整 pipeline

```bash
# 装依赖（一次）— VeighNa 私有源带 vnpy.alpha
pip install "vnpy>=4.4" "vnpy_ctp>=6.7" "vnpy_ctastrategy>=1.4" "vnpy[alpha]" \
    --index=https://pypi.vnpy.com

# 准备沪深300日线数据到 lab/csi300/（AlphaLab parquet 格式）— 一次
python lab/convert_qlib_to_alphalab.py      # 从 data/qlib_data/cn_data 转换（首次约 50s）

# 跑 pipeline
python eval_factors.py runs/gap_round_01/candidates.json   # 单轮候选因子：分段 IC/IR/胜率 → metrics.json
python final_report.py                                      # 终局报告：3 留存因子 → runs/final/performance_report.md + 13 张 PNG 曲线

# 查看产出
cat runs/final/performance_report.md          # 表格 + 图集 + 分段多空 / 分年 RankIC
ls runs/final/charts/                          # 分组累计 / 累积 RankIC / 多空累计 / 分年 RankIC × 3 因子 + 1 对比
```

股票池：沪深300 成分股 683 只日线；label = T+1→T+3 收益；分段区间见 `config.json`（train 2008–2016 / valid 2017–2018 / test 2019–2020.08）。
`lab/component_json.py` 是 Python 3.13 on Windows 上 `shelve` 失效的 JSON 替代（自动 patch 进 `AlphaLab`，无需手动干预）。