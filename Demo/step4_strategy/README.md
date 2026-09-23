# Step 4 ｜ 把"业务"从 main.py 里搬出来

> 前面 3 步我们建了 EventEngine → Gateway → MainEngine 三层。但"看到 tick 就下单"这种业务逻辑还全堆在 main.py 里——这跟把所有代码塞 main() 一样糟糕。

## 为什么需要策略基类？

到 Step 3 为止，你只能在 `main.py` 里写"看到 tick 就下单"。这有两个立刻能碰到的问题：

1. **业务没有边界**：将来想同时跑双均线 + 海龟 + 网格，每个策略的逻辑不能全塞同一个文件
2. **回测代码要重写**：回测是用历史数据 replay EventEngine，如果业务全在 main.py，回测就没法直接用同一份代码

解决方案：**抽出"策略"这个角色**。策略 = 一个会监听 EventEngine 事件的类，自己管自己的生命周期和持仓。

## 这一步我们做了什么？

```text
新增 3 个文件：
├── cta_engine.py      # Step 4 占位版（只是 main_engine + event_engine 的命名空间）
├── cta_template.py    # CtaTemplate —— 所有策略的"父类"
└── strategy.py        # DummyStrategy —— 第一个策略（只看不买）

复用 3 个文件（Step 3 没改动）：
├── event_engine.py    # ← 0 行改动
├── ctp_gateway.py     # ← 0 行改动
└── main_engine.py     # ← 0 行改动
```

`main.py` 改了：原来 handler 里"看到 tick 就下单"，现在改成"看到 tick 就转给 `strategy.on_tick()`"。

## 概念 1 ｜ 策略是被动的

策略自己不主动 poll 数据——它只等被回调。这跟 Step 0 的"CTP 回调驱动"是同一回事，只不过包了一层：

```
CTP 回调线程 ──push──→ EventEngine ──dispatch──→ 策略.on_tick()
                                            ↓
                                    （策略只在此时被打醒）
```

DummyStrategy 怎么"被回调"？在 main.py 里一行就够了：

```python
ee.register(EVENT_TICK, lambda e: strategy.on_tick(e.data))
```

**这是 Step 4 最值得记住的一行**——所有策略的接入方式都是这个模式。

## 概念 2 ｜ 持仓是策略自己的事

```python
class CtaTemplate:
    def __init__(self, ...):
        self.pos = 0   # ← 策略自己维护，不查 CTP
```

为什么不让策略直接查 CTP 持仓？因为**回测时没有 CTP，只有历史数据**。只要 `self.pos` 是在 `on_trade` 里加减——实盘时引擎监听 trade 自动加减，回测时引擎从虚拟成交里加减——**同一份策略代码在实盘和回测都能跑**。

## 概念 3 ｜ buy / sell 是策略的"内部 API"

`CtaTemplate.buy(...)` 帮你封装好"调 main_engine.send_order"：

```python
def buy(self, price, volume=1):
    return self.cta_engine.send_order(self.vt_symbol, "0", "0", price, volume)
```

注意：**Gateway 只有 `send_order(limit_price)`，没有"市价单 / 追价单"概念**。怎么定价是策略的责任——Demo 里你看到 main.py 用 `last + 1.0` 追价，这种逻辑将来都写在策略自己的 `on_tick` 里。

## 生命周期

```
on_init()  ─→  [策略可以订阅行情 / 加载历史]
       │
       ▼
on_start() ─→  self.trading = True
       │
       ▼
┌─── on_tick() ────┐
│    on_order()    │  ← 这四个会被反复回调（实盘时每秒几十次）
│    on_trade()    │
│    on_bar()      │
└──────────────────┘
       │
       ▼
on_stop()  ─→  self.trading = False
```

`self.trading` 是策略的"开关"：False 时 `buy()` 直接返回 ""，**防止策略意外在停止状态发单**。

## 跑一下

```bash
cd ~/quantllm/Demo/step4_strategy && python main.py
```

按顺序在日志里找这 3 类行：

1. **启动瞬间**：`[dummy_au] DummyStrategy on_init 完成（无历史数据加载）` → `on_start：开始接收 tick`
2. **行情持续**：每 10 个 tick 一行 `[dummy_au] TICK #1 最新=940.x ...`（注意 `#1`、`#11`、`#21` 是递增的）
3. **Ctrl+C 后**：`[dummy_au] DummyStrategy on_stop` → `网关 CTP 已关闭`

注意：DummyStrategy **不下单**——Step 4 只是验证事件链通了，不是验证策略逻辑。

## 试试自己改

打开 `strategy.py`，在 `DummyStrategy.on_tick` 末尾加 3 行，让它在第 100 个 tick 时买 1 手：

```python
def on_tick(self, tick):
    self._tick_count += 1
    if self._tick_count % 10 == 1:
        self.write_log(...)
    if self._tick_count == 100:                          # ← 加这 3 行
        last = round(tick["LastPrice"] + 1.0, 2)        # ← 追价 1.0
        self.buy(last, volume=1)                        # ← 买 1 手
```

重新跑，应该看到：
- 第 100 个 tick 后立即：`ORDER ... ref=N 状态=a 报单已提交`
- simnow 撮合后：`TRADE ... 1手 @ xxx TradeID=...`

这就从"只观察"变成"真下单"了。

## 下一步预告

Step 5 做三件事：

1. 把 `cta_engine.py` 从"命名空间"升级为**真正的事件分发 + 多策略管理**
2. 加 **BarGenerator** —— 把 tick 聚成 1 分钟 K 线，给 `on_bar` 用
3. 写一个**真策略** `DoubleMaStrategy`（5/20 均线金叉买、死叉卖）

到那一步，你就有了一个能在你账户里**自动交易**的双均线策略。Step 4 是骨架，Step 5 才长肉。