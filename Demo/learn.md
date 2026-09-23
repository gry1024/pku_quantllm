# 从 Demo/demo.py 到完整交易系统 —— 学习路径

> 目标：自底向上亲手构建一个最小交易系统的每一层（Gateway / EventEngine / MainEngine / 策略引擎 / 回测 / 7×24 / 可视化），再回头用 vnpy 现成抽象替换自研代码。
>
> 原则：每一步**只新增一个抽象层**，跑通后才进入下一步；每一步都能在 SimNow 实盘环境独立验证。

---

## Step 0 ｜ 摸清 Demo/demo.py 的"血肉"

**状态**：已完成。`Demo/demo.py` 能连 SimNow、收行情、查资金/持仓、模拟买开 1 手 au2612。

### 0.1 它做了什么（按调用顺序梳理一遍）

| 顺序 | 动作 | 关键回调 |
|---|---|---|
| 1 | `td_api.connect()` → 注册前置 → `init()` 异步线程启动 | `onFrontConnected` |
| 2 | 前置连上后 → `reqAuthenticate(appid, auth_code)` | `onRspAuthenticate` |
| 3 | 认证通过后 → `reqUserLogin(userid, password, brokerid)` | `onRspUserLogin` |
| 4 | 登录成功后 → `reqSettlementInfoConfirm`（CTP 下单前置条件） | `onRspSettlementInfoConfirm` |
| 5 | 同理 `md_api` 走 connect → login → `subscribeMarketData` | `onRtnDepthMarketData` |

### 0.2 必须内化的概念

- **CTP 是纯异步、回调驱动**：所有 `req*` 函数只负责"发出请求"，结果在另一根 C++ 内部线程通过 `onRsp*` / `onRtn*` 回调回来
- **状态机必须自己维护**：`connect_status / login_status / settlement_confirmed` 三个布尔位就是下单的前提守卫
- **回调线程 ≠ 主线程**：所以 demo 里用 `_monotonic()` + `sleep(1)` 在主线程轮询；直接拿 CTP 内部状态要小心（之后会被 EventEngine 统一接管）
- **`/dev/mem` 警告无害**：是 CTP 安全绑定库尝试采集机器码失败，不影响认证和行情

### 0.3 输出
- 一份"我在第 X 步触发了哪些回调、回调里做了什么"的脑图（贴在 Step 0 笔记里即可）

---

## Step 1 ｜ 把 TdApi + MdApi 合并为一个 `CtpGateway`

### 1.1 构建什么

新建 `Demo/step1_gateway/ctp_gateway.py`，定义：

```python
class CtpGateway:
    """对外只暴露 5 个动作：connect / close / subscribe / send_order / cancel_order / query_*"""
    def __init__(self, event_engine, setting: dict): ...
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def subscribe(self, symbols: list[str]) -> None: ...
    def send_order(self, req: dict) -> str: ...   # 返回 orderid/vt_orderid
    def cancel_order(self, req: dict) -> None: ...
    def query_account(self) -> None: ...
    def query_position(self) -> None: ...

    # ---- 内部仍用 SimpleTdApi / SimpleMdApi ----
    # ---- 但回调里**不再 print**，而是把数据塞进 event_engine ----
```

`main.py` 退化为：
```python
event_engine = EventEngine()       # 现在用最简单的"列表回调"先凑合
gateway = CtpGateway(event_engine, SETTING)
gateway.connect()
gateway.subscribe(["au2612"])
```

### 1.2 理解什么

- **Gateway = 一条通道的封装**（CTP / IB / 老虎 / 币安，每家一个 Gateway 类）
- Gateway 不应关心"上层怎么用"，只负责：**CTP 协议 ↔ 上层通用事件**
- 所有 vnpy 网关都遵守同一接口约定，之后替换/并联多个 Gateway 就靠这个抽象

### 1.3 验收

- 行情继续打印，资金/持仓仍能查
- `print` 语句从 main.py 消失 —— 改由 event_engine 把行情分发出来
- **尝试切到公开测试账号（000300 / Vnpy@123456789）跑一次**，再切回私有账号，理解 IP 白名单和 4 session 上限的影响

### 1.4 对应 vnpy 源码
读 `vnpy/trader/gateway.py` 的 `BaseGateway` 抽象类，**不读实现**，只看接口约定。

---

## Step 2 ｜ 实现 `EventEngine`（事件总线）

### 2.1 构建什么

新建 `Demo/step2_event/event_engine.py`：

```python
class Event:
    """通用事件：type + data"""
    def __init__(self, type_: str, data: dict | object = None): ...

class EventEngine:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._thread: threading.Thread = ...
        self._active: bool = False

    def put(self, event: Event) -> None: ...        # Gateway 回调线程调用
    def register(self, type_: str, handler: Callable) -> None: ...
    def unregister(self, type_: str, handler: Callable) -> None: ...
    def start(self) -> None: ...                    # 启动分发线程
    def stop(self) -> None: ...
    def _run(self) -> None:                         # 死循环 _queue.get() → 分发
        ...
```

预定义事件类型常量（先凑 6 个就够）：
- `EVENT_TICK = "eTick"`
- `EVENT_ORDER = "eOrder"`
- `EVENT_TRADE = "eTrade"`
- `EVENT_ACCOUNT = "eAccount"`
- `EVENT_POSITION = "ePosition"`
- `EVENT_LOG = "eLog"`

### 2.2 理解什么

- **回调线程 → 事件队列 → 分发线程 → 用户 handler**：这是 vnpy 的核心解耦模式
- 把"跨线程数据传递"统一成"事件"，handler 永远在 EventEngine 这一根线程上跑
- 之后策略类、`MainEngine` 内部、所有 UI/记录组件，都是 handler
- `put()` 必须线程安全（用 `Queue` 而非 `list.append`）

### 2.3 验收

- 在 main.py 里：
  ```python
  event_engine.register(EVENT_TICK, lambda e: print(e.data["InstrumentID"], e.data["LastPrice"]))
  ```
- 行情正常打印，**且 print 发生在 EventEngine 线程**（加一行 `print(threading.current_thread().name)` 验证）
- 故意在 handler 里 `sleep(10)` —— Gateway 不会被卡住（这正是引入 EventEngine 的核心收益）

### 2.4 对应 vnpy 源码
读 `vnpy/trader/event_engine.py` 全文（很短的 100 行），对比自己实现的差异。

---

## Step 3 ｜ 实现 `MainEngine`（系统大脑）

### 3.1 构建什么

新建 `Demo/step3_main/main_engine.py`：

```python
class MainEngine:
    def __init__(self, event_engine: EventEngine):
        self.event_engine = event_engine
        self.gateways: dict[str, Gateway] = {}    # name -> gateway
        self.engines: dict[str, object] = {}      # 子引擎：cta / data / risk ...

    def add_gateway(self, name: str, gateway: Gateway) -> None: ...
    def connect(self, name: str, setting: dict) -> None: ...
    def subscribe(self, name: str, symbols: list, exchange: str = "") -> None: ...

    # 高层交易 API（封装底层 req* 细节）
    def send_order(self, vt_symbol: str, direction: str, offset: str,
                   price: float, volume: int) -> str: ...
    def cancel_order(self, vt_orderid: str) -> None: ...

    def query_account(self) -> None: ...
    def query_position(self) -> None: ...

    def write_log(self, msg: str) -> None: ...    # 统一 LOG 入口
```

`main.py` 进一步收敛：
```python
ee = EventEngine(); ee.start()
me = MainEngine(ee)
me.add_gateway("CTP", CtpGateway(ee, SETTING))
me.connect("CTP", SETTING)
me.subscribe("CTP", ["au2612.SHFE"])
```

### 3.2 理解什么

- **MainEngine = Gateway 容器 + 高层 API**（vnpy 把它俩合一是为了"用户不直接碰 Gateway"）
- 之后所有上层（策略、UI、风控、数据）都通过 MainEngine 找 Gateway，不直接 import Gateway
- `vt_symbol = "合约.交易所"`（如 `au2612.SHFE`）是 vnpy 内部的全局唯一 ID 约定，记住它

### 3.3 验收

- 下单接口从 demo 里搬过来跑一次买开 → 看 onRtnOrder 收到 → onRtnTrade 收到
- 试着直接调 `me.send_order(...)` 而不是 `td_api.send_order(...)`，感受抽象的价值

### 3.4 对应 vnpy 源码
读 `vnpy/trader/engine.py` 的 `MainEngine` 类（约 300 行），重点看 `get_gateway / get_engine` 注册机制。

---

## Step 4 ｜ 实现 `CtaStrategy` 策略基类（策略引擎的雏形）

### 4.1 构建什么

新建 `Demo/step4_strategy/cta_template.py`：

```python
class CtaTemplate:
    """所有策略的父类。子类重写 on_xxx 即可。"""
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting: dict):
        self.cta_engine = cta_engine
        self.strategy_name = strategy_name
        self.vt_symbol = vt_symbol
        self.inited: bool = False
        self.trading: bool = False
        self.pos: int = 0                            # 净持仓（+多 -空）
        self.parameters: dict = setting

    def on_init(self) -> None: ...                  # 加载历史数据/订阅行情
    def on_start(self) -> None: ...                 # 开始交易
    def on_stop(self) -> None: ...                  # 停止
    def on_tick(self, tick: dict) -> None: ...      # 行情推送
    def on_order(self, order: dict) -> None: ...    # 委托状态变化
    def on_trade(self, trade: dict) -> None: ...    # 成交回报
    def on_bar(self, bar: dict) -> None: ...        # K 线收盘（之后 Step 5 接）

    # 子类常用的工具方法
    def buy(self, price: float, volume: int = 1) -> None: ...
    def sell(self, price: float, volume: int = 1) -> None: ...
    def cancel_all(self) -> None: ...
    def write_log(self, msg: str) -> None: ...
    def get_pos(self) -> int: ...
    def save_param(self) -> None: ...               # pickle 到本地
```

### 4.2 理解什么

- **策略是无状态的事件处理器**：所有外部信息（行情、订单、成交）都以事件喂进来
- **持仓由策略自己维护**（self.pos），不直接查 CTP；这是为了回测时策略代码一字不改能直接跑
- **`buy / sell` 的"追价 N 个 tick"是策略自己的事**，不是 Gateway 的事 —— Gateway 只接"限价单"请求
- 策略生命周期：`init → start → [tick/order/trade] 循环 → stop

### 4.3 验收

- 写一个 `DummyStrategy(CtaTemplate)`，只重写 `on_tick`：每来一个 tick 就打印价格，**不下单**
- 在 main.py 里实例化、init、start，确认 on_tick 被持续调用

### 4.4 对应 vnpy 源码
读 `vnpy/app/cta_strategy/template.py` 的 `CtaTemplate`，**只读不抄**，对比你的实现为什么这么写。

---

## Step 5 ｜ 实现 `CtaEngine`（策略调度 + K 线合成）

### 5.1 构建什么

新建 `Demo/step5_cta_engine/cta_engine.py`：

```python
class CtaEngine:
    def __init__(self, main_engine: MainEngine, event_engine: EventEngine):
        self.main_engine = me
        self.event_engine = ee
        self.strategies: dict[str, CtaTemplate] = {}

    def add_strategy(self, strategy_class, strategy_name, vt_symbol, setting) -> None: ...
    def init_strategy(self, strategy_name) -> None: ...
    def start_strategy(self, strategy_name) -> None: ...
    def stop_strategy(self, strategy_name) -> None: ...

    def load_bar(self, vt_symbol, days, interval) -> list[dict]: ...   # 从历史数据加载
    def _process_tick(self, event) -> None: ...                         # 分发给策略 + 更新 K 线
    def _process_order(self, event) -> None: ...
    def _process_trade(self, event) -> None: ...
    def _process_position(self, event) -> None: ...                     # 更新策略 self.pos

    # 内部：K 线合成器（按 interval 把 tick 聚成 1min / 5min K 线）
    # —— 可以暂时用 BarGenerator 简化版（vnpy 自带），但理解原理要自己写一版
```

### 5.2 理解什么

- **策略引擎是 MainEngine 的子引擎**，靠 EventEngine 通信，靠 MainEngine 下单/查
- **K 线合成 = tick → bar**：一个 BarGenerator 状态机，按 interval 把 tick 聚成 OHLCV
- 策略拿到的 `on_bar` 和 `on_tick` 是同一份数据的不同时间粒度，策略可以选其一或都用

### 5.3 验收

- 实现一个 `DoubleMaStrategy(CtaTemplate)`：
  - 5 均线、20 均线金叉买开、死叉卖平
  - 只看 1 分钟 K 线
  - 启动后让它在你 simnow 账号上跑 5 分钟，**用最小手数 + 远离市价的限价单**避免真的成交

### 5.4 对应 vnpy 源码
读 `vnpy/app/cta_strategy/engine.py` 的 `CtaEngine` 类（约 800 行）。**这是 vnpy 最复杂的一个引擎**，慢慢读，重点是 BarGenerator 和 strategy 调度。

---

## Step 6 ｜ 回测（用 vnpy 自带 BacktesterEngine，先不自己实现）

### 6.1 构建什么

新建 `Demo/step6_backtest/run_backtest.py`：

```python
from vnpy.app.cta_strategy.backtesting import BacktestingEngine, OptimizationSetting

engine = BacktestingEngine()
engine.set_parameters(
    vt_symbol="au2606.SHFE",       # 用历史数据能找到的合约
    interval="1m",
    start=datetime(2024, 1, 1),
    end=datetime(2024, 6, 1),
    rate=0.0005,                   # 手续费
    slippage=0.2,                  # 滑点
    size=1000,                     # 合约乘数（沪金 1000g）
    pricetick=0.02,
    capital=1_000_000,
)

engine.add_strategy(DoubleMaStrategy, {})
engine.load_data()                 # vnpy 会去 data_manager 找
engine.run_backtesting()
df = engine.calculate_result()
engine.show_chart()                # vnpy 自带的 plotly 资金曲线

# 参数优化
opt = OptimizationSetting()
opt.add_parameter("fast_window", 5, 20, 5)
opt.add_parameter("slow_window", 20, 60, 10)
engine.run_optimization(opt)       # 输出每个参数组合的收益/回撤
```

### 6.2 理解什么

- **回测 = 用历史 tick/bar 重放事件**，策略代码 0 修改
- 关键参数：`rate / slippage / size / pricetick / capital` —— 任何一个不对，结果就骗人
- vnpy 回测引擎的撮合逻辑："下一根 K 线开盘价成交"—— 简单但够用，HFT 不适用
- **回测陷阱**：过拟合、偷价、未来函数、滑点设低、复权没处理

### 6.3 数据从哪里来
- 短期：vnpy 自带的 `vnpy.app.data_manager` + 米筐/掘金等数据源
- 中期：自己用 `rqalpha` / `tushare` 拉历史 1m 数据存本地
- 参考 memory: `rqalpha-plus-chinese-font.md`（项目里已经有 RQAlpha 经验）

### 6.4 验收

- 同一策略、同一区间，`run_backtesting` 和 `run_optimization` 至少跑一次
- 把回测报告（`calculate_statistics()` 输出）打印出来，理解**夏普 / 最大回撤 / 收益回撤比**三个核心指标

---

## Step 7 ｜ 7×24 运行的工程化

### 7.1 构建什么

把 `main.py` 升级为生产级入口：

```text
Demo/
├── run.py                 # 生产入口（被 supervisor 调用）
├── strategy/
│   └── double_ma.py
├── logs/                  # 日志目录
│   ├── run.log            # 主日志（含所有 write_log）
│   ├── trade.log          # 成交明细（单独落盘）
│   └── tick_YYYYMMDD.log  # 当日行情（可选）
├── data/                  # 持久化
│   ├── strategy_xxx.json  # 策略状态/持仓
│   ├── trades.csv         # 累计成交
│   └── equity.csv         # 资金曲线原始数据
└── requirements.txt
```

#### 7.1.1 日志

用 `loguru`（或 vnpy 内置 `WRITE_LOG` 事件 + handler 写文件）：

```python
from loguru import logger
logger.add("logs/run_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="INFO")
```

#### 7.1.2 异常自愈

```python
# run.py 顶层
def main():
    while True:
        try:
            me = MainEngine(EventEngine())
            me.add_gateway("CTP", CtpGateway(me.event_engine, SETTING))
            me.connect("CTP", SETTING)
            cta = CtaEngine(me, me.event_engine)
            cta.init_strategy("double_ma")
            cta.start_strategy("double_ma")
            while True: sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception(f"主循环异常，10 秒后重启: {e}")
            sleep(10)   # 防抖：避免雪崩
```

#### 7.1.3 进程守护

WSL / Linux 上用 `supervisor`：

```ini
[program:quantllm]
command=/home/groy/miniconda3/envs/quantllm/bin/python /home/groy/quantllm/Demo/run.py
user=groy
autostart=true
autorestart=true
startretries=10
stopwaitsecs=30
stdout_logfile=/home/groy/quantllm/Demo/logs/supervisor.out
stderr_logfile=/home/groy/quantllm/Demo/logs/supervisor.err
```

### 7.2 理解什么

- **进程死了必须自动拉起**：交易系统不允许"周末发现程序挂了 3 天"
- **CTP 自身的重连** vs **进程级重连** 是两层；onFrontDisconnected 是第一层，`supervisor` 是第二层
- **周末/节假日 SimNow 关闭**（memory 已记）：要写"连接失败不报警"的容忍逻辑
- **日志是唯一证据**：策略赚钱了要看日志才知道为什么，亏钱了也要看日志

### 7.3 验收

- `supervisorctl status quantllm` 显示 RUNNING
- 手动 `kill -9` 主进程 → 5 秒内自动重启
- 连续运行 24 小时，统计：日志行数、断线重连次数、累计成交笔数

---

## Step 8 ｜ 资金曲线可视化

### 8.1 构建什么

#### 8.1.1 数据采集

新增 `EquityRecorder`，订阅 `EVENT_ACCOUNT` + `EVENT_TRADE`，每收到一次就 append 到 `data/equity.csv`：

```csv
timestamp,balance,available,frozen,position_value,total_equity
2026-09-23 09:35:01,1000000.00,950000.00,50000.00,120000.00,1070000.00
```

#### 8.1.2 实时曲线（盘后看 / Web 看板）

用 `plotly` + `dash`，或简单的 `matplotlib` + 定时刷新 PNG：

```python
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime

df = pd.read_csv("data/equity.csv", parse_dates=["timestamp"])
df["drawdown"] = df["total_equity"] / df["total_equity"].cummax() - 1

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True,
                                gridspec_kw={"height_ratios": [2, 1]})
ax1.plot(df["timestamp"], df["total_equity"], label="Total Equity")
ax1.fill_between(df["timestamp"], df["total_equity"], df["total_equity"].cummax(),
                 alpha=0.1, color="green")
ax1.legend()
ax2.fill_between(df["timestamp"], df["drawdown"], 0, alpha=0.4, color="red")
ax2.set_ylabel("Drawdown")
plt.tight_layout()
plt.savefig("data/equity_curve.png", dpi=150)
```

#### 8.1.3 进阶（可选）

- 回测 + 实盘**同一张图**：用 vnpy 的 `BacktesterEngine.calculate_result()` 的 df 拼到一起
- 用 `dataviz` skill 输出交互式 HTML（hover 看逐笔成交）

### 8.2 理解什么

- **资金曲线 ≠ 累计盈亏**：要算"持仓市值 + 可用 + 冻结"
- **回撤 = peak-to-trough**：最大回撤、夏普、Calmar 是 3 个最值得看的指标
- **可视化是验证系统的眼睛**：没有它你不知道策略到底赚没赚钱

### 8.3 验收

- 运行 24 小时后，`equity.csv` 至少有 ~2880 行（每 30 秒一次）
- `equity_curve.png` 上能看到清晰的资金曲线 + 阴影回撤区
- 对比 `calculate_statistics()` 的夏普 vs 曲线肉眼判断，**两者方向必须一致**

---

## Step 9 ｜ 用 vnpy 现成抽象替换自研代码

> 走到这一步，你已经把 vnpy 内部每一层都"重造过轮子"。现在才有资格说"我会用 vnpy 了"。

### 9.1 替换映射表

| 自研（Demo 里写的） | vnpy 现成 | 替换要点 |
|---|---|---|
| `CtpGateway` | `vnpy_ctp.gateway.ctp_gateway.CtpGateway` | 直接 `add_gateway(CtpGateway)` |
| `EventEngine` | `vnpy.trader.event_engine.EventEngine` | 一行 import，API 几乎一致 |
| `MainEngine` | `vnpy.trader.engine.MainEngine` | 一行 import |
| `CtaTemplate` | `vnpy.app.cta_strategy.template.CtaTemplate` | 把 `from ... import CtaTemplate` 改一下，策略 0 改动 |
| `CtaEngine` | `vnpy.app.cta_strategy.engine.CtaEngine` | 替换类名 |
| 自写 BarGenerator | `vnpy.trader.utility.BarGenerator` | vnpy 版本支持任意 interval + 嵌套（1m→5m→日） |
| 自写 EquityRecorder | `vnpy.app.data_manager` + `vnpy.app.recorder` | 直接落 SQLite |
| 自写回测 | `vnpy.app.cta_strategy.backtesting.BacktestingEngine` | 已经在 Step 6 用过了 |

### 9.2 替换流程（按风险顺序）

1. **先替换 EventEngine / MainEngine**：接口几乎一致，一行 import 改完
2. **再替换 CtpGateway**：vnpy_ctp 版本可能要求不同的 connect 设置
3. **最后替换 CtaEngine / CtaTemplate**：策略代码几乎不用动，主要是 engine 初始化方式
4. **保留你的 EquityRecorder**：vnpy 没现成"持久化资金曲线到 CSV"的组件，自己写的更可控

### 9.3 验收

- `Demo/run_vnpy.py` 用 100% vnpy 现成 API 跑通
- 双均线策略 0 改动即可工作
- 对比 `Demo/run.py`（自研）vs `Demo/run_vnpy.py`（vnpy）的代码量：后者应**短 60% 以上**
- 7×24 跑 48 小时，无 crash

### 9.4 必读源码（按顺序）

1. `vnpy/trader/event_engine.py`（110 行，10 分钟）
2. `vnpy/trader/engine.py` 的 `MainEngine`（300 行，30 分钟）
3. `vnpy/trader/gateway.py` 的 `BaseGateway`（仅接口，10 分钟）
4. `vnpy_ctp/gateway/ctp_gateway.py`（对照你 Step 1 写的，30 分钟）
5. `vnpy/app/cta_strategy/engine.py`（800 行，分 3 天读完）
6. `vnpy/app/cta_strategy/template.py`（200 行，30 分钟）
7. `vnpy/trader/utility.py` 的 `BarGenerator`（100 行）

---

## 总览时间线

| Step | 主题 | 预计时长 | 产出 |
|---|---|---|---|
| 0 | 摸清 demo.py | 已完成 | 脑图 |
| 1 | Gateway 抽象 | 0.5 天 | `ctp_gateway.py` |
| 2 | EventEngine | 0.5 天 | `event_engine.py` |
| 3 | MainEngine | 0.5 天 | `main_engine.py` |
| 4 | CtaTemplate | 0.5 天 | `cta_template.py` |
| 5 | CtaEngine + 双均线 | 1 天 | 双均线策略实盘 demo |
| 6 | 回测 | 1-2 天 | 优化报告 + 资金曲线 |
| 7 | 7×24 工程化 | 0.5 天 | supervisor + 日志 + 自愈 |
| 8 | 资金曲线可视化 | 0.5 天 | equity.csv + PNG 看板 |
| 9 | 替换为 vnpy 现成 | 1 天 | 全部 import vnpy |
| **合计** | | **6-7 天** | **生产级交易系统** |

---

## 调试手册（每个 Step 都会用到）

| 故障 | 第一时间检查 |
|---|---|
| 行情收不到 | `onRspUserLogin` 报错？订阅是否在 login 之后？合约是否挂牌？ |
| 下单被拒 | `onRspOrderInsert` 的 ErrorID + ErrorMsg；是否确认结算单；是否在交易时段 |
| 断线不重连 | `onFrontDisconnected` 是否触发；CTP 内部重连默认开启，但需要 `createFtdcTraderApi(path, True)` |
| 内存泄漏 | 大概率是 EventEngine 队列堆积，handler 抛异常会丢事件（用 `try/except` 包每个 handler） |
| 时间不对 | CTP 行情时间戳是交易所时间，**不是本地时间**；不要用它算本地延时 |
| IP 白名单 | 见 memory: `simnow-ctp-connection.md` |

---

## 下一步（学完之后再考虑）

- **多策略 + 组合**：CtaEngine 支持多策略实例，但要解决策略间互相成交的归属问题
- **风控层**：单独一个 RiskEngine，订阅所有 order/trade，做"单笔超限 / 日内亏损熔断"
- **数据层**：从 CSV → SQLite → TimescaleDB，EquityRecorder 自然落库
- **多账户 / 多通道**：1 个 MainEngine + N 个 Gateway，跑 多个 CTP 账号（vnpy 原生支持）
- **Web 看板**：vnpy 内置 VeighNa Trader（PyQt5），或者自己用 FastAPI + WebSocket 推 equity 数据