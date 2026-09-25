# quantllm

PKU 当代量化交易系统课程作业。基于 [vnpy](https://github.com/vnpy/vnpy) 的 CTP 量化交易学习仓库。

## 入口脚本

| 脚本 | 任务 | 启动 |
|---|---|---|
| `ctp_demo.py` | Step 3+4：CTP 极简 DEMO（柜台 → 行情订阅 → 持续打印） | `python ctp_demo.py` |
| `ctp_breakout_demo.py` | Step 6：突破价格阈值 + TD API 下单 | `python ctp_breakout_demo.py` |
| `eval_factors.py` | Step 7：批量评估一轮候选因子 | `python eval_factors.py runs/gap_round_01/candidates.json` |
| `final_report.py` | Step 7：留存因子的终局绩效报告（出 `runs/final/performance_report.md`） | `python final_report.py` |
| `intraday_alpha/research/run.py` | Capstone：Polars + LightGBM 离线投研流水线 | `cd intraday_alpha/research && python run.py --predict-only` |
| `intraday_alpha/live/ctp_runner.py` | Capstone：vnpy CtaTemplate 实盘启动（连 SIMNOW） | `cd intraday_alpha/live && python ctp_runner.py` |

## 凭证

```bash
cp .env.example .env       # 填入 SIMNOW 账号（不在代码里硬编码）
```

`.env` 在 `.gitignore` 里，不会进仓库。`intraday_alpha/live/ctp_runner.py` 也读同一份根 `.env`。

## 源码克隆（`raw/`）

```bash
pip install -e raw/vnpy -e raw/vnpy_ctp -e raw/vnpy_ctastrategy
```

三个模块仍在 `raw/` 下，按 `name=` 注册到 site-packages，跟目录叫 `vnpy` 还是 `raw/vnpy` 无关。`raw/` 本体 gitignored。

## 学习笔记（`notes/`）

Step 5 的 VeighNa 11 概念分层笔记，Obsidian 双链结构：

- `notes/vnpy/` — 主引擎 / 事件引擎 / 网关 / alpha / 回测
- `notes/vnpy_ctp/` — CTP 类型 / 常量 / MD/TD API / 网关 / 代码生成器
- `notes/vnpy_ctastrategy/` — CTA 模板 / 信号类 / 引擎 / 停止委托 / 移仓助手

用 Obsidian 直接打开 `notes/` 即可导航。

## 因子挖掘产物（`runs/`）

- `runs/gap_round_01..04/` · `runs/mom_round_01..06/` — 4+6 轮迭代的 `candidates.json` + `metrics.json` + `notes.md`
- `runs/final/` — 跳空方向终局产物（3 因子 + 报告）
- `runs/final_mom/` — 动量方向终局产物归档

`config.json` 顶层是数据路径 / 区间 / 标签定义。`runs/cache/` 是 pickle 缓存，gitignored。

## 依赖

```bash
pip install lightgbm polars python-dotenv plotly
```

VeighNa Studio 自带 Python 已预装 vnpy / vnpy_ctp / vnpy_ctastrategy / vnpy.alpha。

---

参考作业：[mabucai/repo_pku_quantllm](https://github.com/mabucai/repo_pku_quantllm) · [mabucai/factor_pku_quantllm](https://github.com/mabucai/factor_pku_quantllm)