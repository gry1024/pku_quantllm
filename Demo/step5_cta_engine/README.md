# Step 5 ｜ 把"裸 tick"变成"1 分钟 K 线"，再让策略在 K 线上做决策

> Step 4 我们让 DummyStrategy 收到每一笔 tick。但"看 tick"做决策太短视——真实策略要算 MA / MACD / 布林带这些**指标**，指标需要一段历史窗口。tick 进来的是"瞬时"数据，你需要**按时间聚合**成 1 分钟、5 分钟、日线才方便算指标。

## 为什么这一步不可跳过？

到 Step 4 结束，你能在 tick 上写策略——但问题是：

1. **tick 太快太碎**：au2612 每秒 2-5 笔 tick，1 分钟就有 200-300 笔。任何"看 20 个 tick 的均值"的策略，1 秒钟就要算几十次
2. **没法用现成指标库**：MACD / 布林带 / KDJ 都基于 K 线，不基于 tick
3. **没法做多策略**：Step 4 的 main.py 里 `strategy.on_tick(d)` 是写死的，没法同时跑双均线 + 海龟 + 网格

解决方案：
1. **BarGenerator** —— 把 tick 自动聚成 1 分钟 K 线（OHLCV），触发时推给策略 `on_bar`
2. **CtaEngine 真版** —— 管理多策略 + 按 vt_symbol 路由事件 + 自动维护 `self.pos`
3. **DoubleMaStrategy** —— 第一个真策略：5/20 均线金叉买开、死叉卖平

## 这一步我们做了什么？

```text
新增 4 个文件：
├── bar_generator.py       # tick → 1 分钟 K 线（OHLCV）
├── cta_template.py        # CtaTemplate 扩展：默认 on_tick 走 BarGenerator
├── cta_engine.py          # CtaEngine 真版：多策略管理 + 事件分发 + 持仓维护
├── double_ma_strategy.py  # 第一个真策略：5/20 双均线
└── main.py                # 入口：实例化 DoubleMaStrategy → add → init → start

复用 3 个文件（Step 2/3 没改动）：
├── event_engine.py        # ← 0 行改动
├── ctp_gateway.py         # ← 0 行改动
└── main_engine.py         # ← 0 行改动
```

## 概念 1 ｜ BarGenerator 把 tick 聚成 K 线

```python
class BarGenerator:
    def on_tick(self, tick: dict) -> None:
        ts = _parse_ts(tick)                           # 从 TradingDay+UpdateTime 合成 datetime
        minute = ts.replace(second=0, microsecond=0)   # 算"属于哪一分钟"
        if self._cur_bar is None or minute > self._cur_bar.datetime:
            # 新一分钟 —— 推旧 bar，开新 bar
            if self._cur_bar is not None:
                self._on_bar(self._cur_bar.to_dict())
            self._cur_bar = Bar(self.vt_symbol, minute)
        self._cur_bar.update(tick, self._prev_vol)     # OHLCV 更新
```

**核心是一个状态机**：

```
[没在聚合]    ──tick──→  [开新 minute 的 Bar，开始 update]
                    └─minute 切换──→ 推回上一根 Bar → 开新 Bar
                    └─同 minute──→ 继续 update 当前 Bar
```

一分钟键的当前 Bar = 一个状态：(vt_symbol, datetime, OHLCV). Volume(因为是你 累计) 不需要算成交 delta。

## 概念 2 ｜ 策略是被动的 + 默认 on_tick 自动走 BarGen

`CtaTemplate.on_tick` 默认实现只剩 1 行：

```python
def on_tick(self, tick: dict) -> None:
    self.bg.on_tick(tick)        # tick 推给 BarGenerator
```

子类**只覆盖 `on_bar`** 就够了——`on_tick` 自动帮你跑 K 线合成：

```python
class DoubleMaStrategy(CtaTemplate):
    def on_bar(self, bar: dict) -> None:        # ← 写这里！
        if len(self.bars) < self.slow_window + 1:
            return
        ...
        if 金叉:
            self.buy(bar["close"], volume=1)
```

每个策略自己**自带一个 BarGenerator 实例**（在 `__init__` 里建好），互不干扰。

## 概念 3 ｜ CtaEngine 按 vt_symbol 路由事件

Step 4 里 main.py 写死 `strategy.on_tick(d)`。Step 5 让 CtaEngine 自动接管：

```python
def _process_tick(self, event) -> None:
    vt = self._vt_symbol_of(event.data)         # 'au2612.SHFE'
    for s in self.strategies.values():
        if s.vt_symbol == vt:
            s.on_tick(event.data)               # 只分发给匹配 vt_symbol 的策略
```

**好处**：将来你加新策略：

```python
cta.add_strategy(DoubleMaStrategy(cta, "ma_5_20", "au2612.SHFE", {}))
cta.add_strategy(TurtleStrategy(cta, "turtle", "au2612.SHFE", {}))    # 同时跑两个
```

CtaEngine 自动按 vt_symbol 把 tick 派给两个策略的 `on_tick`。

## 概念 4 ｜ 策略持仓由 CtaEngine 自动维护

```python
def _process_trade(self, event) -> None:
    for s in self.strategies.values():
        if s.vt_symbol == vt:
            if d["Direction"] == "0":    # 买
                s.pos += d["Volume"]
            else:                        # 卖
                s.pos -= d["Volume"]
            s.on_trade(d)
```

**为什么不让策略查 CTP 持仓？** 因为：
- 回测时**没有 CTP**——回测引擎把成交"喂"进来，`self.pos` 自动加减
- 实盘时偶尔 POSITION 异步回报会晚于 TRADE——查 CTP 会有"看似平仓但策略还以为持仓"的窗口

**实盘 vs 回测共用同一份策略代码**——靠的就是 `self.pos` 由 trade 推过来、而不是查 CTP。

## 生命周期（Step 5 完整版）

```
                  ┌──── CtaEngine 启动 ────┐
                  │                       │
                  ▼                       │
        cta.init_all()                    │
                  │                       │
                  ▼                       │
        cta.start_all()                   │
                  │                       │
                  ▼                       ▼
        ┌─── on_init() ──┐      ┌─── [EventEngine 分发循环] ───┐
        │   加载历史    │      │  TICK ─→ BarGenerator ─→ on_bar() │  ← 双均线在这里算 MA
        └───┬────────────┘      │  ORDER ─→ on_order()               │
            ▼                   │  TRADE ─→ 更新 self.pos ─→ on_trade()
        on_start()              │  POSITION ─→ (丢弃)             │
        trading=True            └──────────────────────────────────┘
                                          │
                                          ▼
                                  Ctrl+C → cta.stop_all() → on_stop() → trading=False
```

## 跑一下

```bash
cd ~/quantllm/Demo/step5_cta_engine && python main.py
```

按顺序在日志里找这 4 类行：

1. **启动**：`策略 double_ma_au 已注册（合约=au2612.SHFE）` → `DoubleMa 初始化：fast=5 slow=20 test_only=True` → `DoubleMa 开始接收 bar`
2. **累积阶段**（前 20 根 bar）：每隔几秒会有一行 `[double_ma_au] bar 累积中：N/21`
3. **信号阶段**（第 21 根 bar 之后）：当 fast/slow 交叉时，出现 `金叉 cur=(...) prev=(...)` + `[测试模式] 本应买开 1 手 @ xxx`
4. **Ctrl+C 后**：`DoubleMa on_stop`（或 `on_stop` 不显式 log） → `网关 CTP 已关闭`

注意：`test_only=True` 默认值，**金叉信号触发时不会真下单**。下方的"试试自己改"告诉你怎么改。

## 试试自己改

### 改动 1 ｜ 让 DoubleMaStrategy 真下单

打开 `main.py`，把 `test_only: True` 改成 `False`：

```python
strategy = DoubleMaStrategy(
    cta, "double_ma_au", "au2612.SHFE",
    {"fast_window": 5, "slow_window": 20, "test_only": False},   # ← 改 False
)
```

重新跑，等金叉出现后日志里应该是 `ORDER ... 状态=a 报单已提交` 而不是 `[测试模式]`。⚠️ 真账户真资金，先用 au2612 这种波动小的主力合约小试。

### 改动 2 ｜ 同 vt_symbol 跑两个策略

CtaEngine 支持同合约多策略。在 `main.py` 的 `cta.add_strategy(strategy)` 后面加：

```python
from double_ma_strategy import DoubleMaStrategy

strategy2 = DoubleMaStrategy(
    cta, "double_ma_au_aggressive", "au2612.SHFE",
    {"fast_window": 3, "slow_window": 10, "test_only": True},    # 快一点的参数
)
cta.add_strategy(strategy2)
```

重新跑，au2612 的每笔 tick 会**同时**派给两个策略——日志里会出现两组 `bar 累积中` 行（带不同 strategy_name 前缀）。

### 改动 3 ｜ 把快慢均线窗口改成 MA(20, 60) 的"中线股" 风格

`fast_window=20, slow_window=60` 出来的信号少得多，更容易看懂金叉/死叉发生时刻。但等 60+ 根 bar 需要 1 小时。

## 下一步预告

Step 6 把同一份 `DoubleMaStrategy` 拿来回测——`vnpy.app.cta_strategy.backtesting.BacktestingEngine` 一行 `engine.run_backtesting()` + 一行 `engine.show_chart()` 出资金曲线 + 最大回撤阴影。**回测和实盘的策略代码 0 改动**——这正是 Step 4 把 `self.pos` 设计成"由 on_trade 推过来"换来的好处。