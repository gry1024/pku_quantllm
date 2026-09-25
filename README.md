# quantllm

> 当代量化交易系统原理与实现（pku_quantllm · 第二节）
> 基于 [vnpy](https://github.com/vnpy/vnpy) 的 CTP 量化交易学习仓库。

---

## 1. 课程作业 7 步交付物导航

| 步 | 作业要求 | 交付物 |
|---|---|---|
| 1 | 项目初始化（WIKI 学习项目） | 本 README + 各子项目 README（仓库即 WIKI） |
| 2 | VeighNa 三个模块源码克隆（放 `raw/`） | [`raw/vnpy/`](raw/vnpy/) · [`raw/vnpy_ctp/`](raw/vnpy_ctp/) · [`raw/vnpy_ctastrategy/`](raw/vnpy_ctastrategy/) |
| 3 | CTP 极简 DEMO（柜台连接 → 行情订阅 → 打印推送） | [`projects/ctp_demo/`](projects/ctp_demo/) |
| 4 | 持续行情输出（活跃合约订阅） | [`projects/ctp_demo/`](projects/ctp_demo/)（同 3，循环订阅不退出） |
| 5 | VeighNa 11 概念分层学习 | [`notes/`](notes/)（Obsidian 笔记 31 篇） |
| 6 | TD API 下单（突破阈值触发限价开多） | [`projects/ctp_breakout_demo/`](projects/ctp_breakout_demo/) |
| 7 | 因子挖掘（动量 + 跳空，4 轮 × 10-20 因子） | [`projects/alpha_mining_demo/`](projects/alpha_mining_demo/) |
| ➕ | 研究 → 实盘完整闭环（单标的日内 alpha） | [`projects/intraday_alpha/`](projects/intraday_alpha/) |

---

## 2. 仓库结构

```
quantllm/
├── README.md                                 # 本文件：7 步 TOC
├── .gitignore                                # 凭证 / 源码克隆 / 缓存忽略规则
├── SIMNOW模拟平台账户密码.txt                # 私有凭证，gitignored
│
├── raw/                                      # Step 2 源码克隆（gitignored；按 pip install -e raw/<name> 装）
│   ├── vnpy/                                 #     核心引擎（trader / event / alpha）
│   ├── vnpy_ctp/                             #     CTP 柜台网关（动态库自动编译）
│   └── vnpy_ctastrategy/                     #     CTA 策略模板 + 回测引擎
│
├── notes/                                    # Step 5 VeighNa 体系结构学习笔记（Obsidian 双链）
│   ├── vnpy/                                 #     主引擎 / 事件引擎 / 网关 / alpha / 回测 / K 线
│   ├── vnpy_ctp/                             #     CTP 类型 / 常量 / 网关 / MD/TD API / 代码生成器
│   └── vnpy_ctastrategy/                     #     CTA 模板 / 信号类 / 引擎 / 回测 / 移仓助手
│
└── projects/                                 # 课程 4 个交付物 + 1 个 capstone
    ├── README.md                             # 项目目录索引
    ├── ctp_demo/                             # Step 3 + Step 4（极简 CTP DEMO）
    │   ├── README.md / demo.py               #     从底层 Python API 直连 CTP
    │   └── .env.example                      #     SIMNOW 凭证模板（cp 为 .env 后填入）
    ├── ctp_breakout_demo/                    # Step 6（突破阈值 + TD API 下单）
    │   ├── README.md / demo.py               #     行情 → 决策 → 下单 → 回报完整闭环
    │   └── .env.example
    ├── alpha_mining_demo/                    # Step 7（动量 + 跳空因子挖掘，4 轮 + 终局报告）
    │   ├── README.md / config.json
    │   ├── eval_factors.py / final_report.py #     LLM 驱动的批量因子评估脚本
    │   └── runs/                             #     6 轮 round + final + final_mom 产物
    └── intraday_alpha/                       # Capstone（Polars + LightGBM 研究 → vnpy 实盘）
        ├── README.md                         #     投研流水线 + 实盘启动详解
        ├── .env.example                      #     凭证模板
        ├── research/                         #     离线：CSV → AlphaLab → 模型 → 信号 JSON → 回测
        │   ├── run.py                        #         编排器
        │   ├── intraday_alpha/               #         子包（dataset / model / strategy / config）
        │   └── runs/                         #         产物（signal.parquet + 统计 JSON）
        └── live/                             #     实盘：EventEngine + CtpGateway + CtaStrategyApp
            ├── README.md                     #         启动步骤 / 调试手册
            ├── ctp_runner.py                 #         headless 主循环
            └── strategy/                     #         IntradayAlphaStrategy（CtaTemplate）
```

---

## 3. 凭证与本地配置

**任何 SIMNOW / 经纪商账号都不允许硬编码到代码里**（[no-hardcoded-credentials](..)）——所有 demo 和 capstone 都从环境变量读取，缺失即报错。

```bash
# 任意 demo / capstone：cp .env.example .env → 填入 → 启动
cp projects/intraday_alpha/.env.example projects/intraday_alpha/.env
cp projects/ctp_demo/.env.example        projects/ctp_demo/.env
cp projects/ctp_breakout_demo/.env.example projects/ctp_breakout_demo/.env
```

`.env` / `SIMNOW模拟平台账户密码.txt` 都在 `.gitignore` 里，绝不会进 git 历史。

---

## 4. 安装

```bash
# 1) 源码克隆装为 editable（一次性；按目录命名，无论叫 raw/vnpy 还是 vnpy 都生效）
pip install -e raw/vnpy -e raw/vnpy_ctp -e raw/vnpy_ctastrategy

# 2) 额外依赖（Capstone 用）
pip install lightgbm polars python-dotenv plotly
```

---

## 5. 运行任意一步

| 步 | 命令 |
|---|---|
| 3+4 | `cd projects/ctp_demo && python demo.py` |
| 6 | `cd projects/ctp_breakout_demo && python demo.py` |
| 7 | `cd projects/alpha_mining_demo && python eval_factors.py runs/gap_round_01/candidates.json` |
| ➕ 研究 | `cd projects/intraday_alpha/research && python run.py --predict-only` |
| ➕ 实盘 | `cd projects/intraday_alpha/live && python ctp_runner.py` |

---

## 6. 调试与常见问题

详见各子项目 README：

- [`projects/ctp_demo/README.md`](projects/ctp_demo/README.md) — DLL 路径、流文件目录、订阅时序
- [`projects/ctp_breakout_demo/README.md`](projects/ctp_breakout_demo/README.md) — 结算单确认、OrderRef 自增、回报过滤
- [`projects/alpha_mining_demo/README.md`](projects/alpha_mining_demo/README.md) — 4 轮迭代协议、终局报告结构
- [`projects/intraday_alpha/README.md`](projects/intraday_alpha/README.md) — 研究流水线 + 实盘部署