# quantllm

PKU 当代量化交易系统课程作业。基于 [vnpy](https://github.com/vnpy/vnpy) 的 CTP 量化交易学习仓库。

## 入口脚本

| 脚本 | 跑什么 | 命令 | 前置 |
|---|---|---|---|
| `ctp_demo.py` | Step 3+4：CTP 极简 DEMO，订阅 5 个活跃合约、持续打 tick | `python ctp_demo.py` | `.env` 有 SIMNOW 凭证；非交易时段 SIMNOW `实盘` 前置会拒连 |
| `ctp_breakout_demo.py` | Step 6：突破价阈值触发限价开多 1 手 | `python ctp_breakout_demo.py` | 同上 |
| `eval_factors.py` | Step 7：评估一轮候选因子的分段 IC/IR | `python eval_factors.py runs/gap_round_01/candidates.json` | `config.json` 里 `lab_path` 指向你的沪深300日线数据；依赖 `vnpy[alpha]` |
| `final_report.py` | Step 7：出 `runs/final/performance_report.md` | `python final_report.py` | 同上 |
| `intraday_alpha/research/run.py` | Capstone 离线：CSV → LightGBM → 信号 JSON → 回测 | `cd intraday_alpha/research && python run.py --predict-only` | 1-min OHLCV CSV；依赖 polars / lightgbm |
| `intraday_alpha/live/ctp_runner.py` | Capstone 实盘：连 SIMNOW、按 1-min bar 下单 | `cd intraday_alpha/live && python ctp_runner.py` | 先跑上一行产 `live_signal.json`；`.env` 有凭证 |

## 凭证

```bash
cp .env.example .env
# 编辑 .env 填入 SIMNOW 账号
```

`SIMNOW模拟平台账户密码.txt` 也存了这份内容（gitignored）。所有脚本都从根 `.env` 读。

## 装依赖

```bash
# VeighNa 三个源码克隆（按 setup.py 的 name= 注册到 site-packages，目录名无关）
pip install -e raw/vnpy -e raw/vnpy_ctp -e raw/vnpy_ctastrategy

# vnpy[alpha]（私有源；带 vnpy.alpha + polars + matplotlib + alphalens-reloaded）
pip install "vnpy[alpha]" --index=https://pypi.vnpy.com

# capstone 额外依赖
pip install lightgbm python-dotenv plotly
```

## Step 7 数据准备（`eval_factors.py` / `final_report.py` 必需）

`config.json` 里的 `lab_path` 当前是 `D:/pku_demo/raw/alpha_researcher/lab/csi300`（作业原始工作区的 Windows 绝对路径），**改成本机数据位置**：

```json
"lab_path": "你机器上的 /path/to/csi300"   // 沪深300日线，~859 只成分股
```

数据按 vnpy.alpha 的 `AlphaLab` 目录约定组织：`lab_path/<vt_symbol>.parquet`，列名 `date, open, high, low, close, volume, turnover, open_interest`。改完直接 `python eval_factors.py ...` 即可（首次构建会缓存到 `runs/cache/`，之后复用）。

## Capstone 数据准备

`intraday_alpha/research/run.py` 消费 1-min OHLCV CSV（列：`datetime, open, high, low, close, volume, turnover, open_interest`，`vwap` 可选）：

```bash
cd intraday_alpha/research
python run.py --ingest-csv ../../../data/au2612_1min.csv    # 一次性灌数据
python run.py --predict-only                                 # 写 live_signal.json
```

产出的 `intraday_alpha/research/runs/lab/intraday/signal/intraday_live_signal.json` 就是 live 策略读的信号文件。

## 其它目录

- `raw/` — vnpy / vnpy_ctp / vnpy_ctastrategy 源码克隆（gitignored 子内容，按上面 `pip install -e` 装）
- `notes/` — Step 5 的 VeighNa 11 概念分层 Obsidian 笔记（`vnpy/` `vnpy_ctp/` `vnpy_ctastrategy/` 三个 vault）
- `runs/` — Step 7 因子挖掘产物（6 轮 round + 2 个 final）
- `intraday_alpha/research/runs/` — Capstone 离线产物（pickle 缓存 gitignored）
- `data/`, `lab/` — gitignored

## 参考作业

[mabucai/repo_pku_quantllm](https://github.com/mabucai/repo_pku_quantllm) · [mabucai/factor_pku_quantllm](https://github.com/mabucai/factor_pku_quantllm)