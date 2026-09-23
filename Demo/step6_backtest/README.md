# Step 6 ｜ 用真实历史数据回放一次，看看策略能不能赚钱

> Step 5 的 DoubleMaStrategy 已经在 SimNow 上"上膛"了——但你根本不知道它在历史上赚不赚钱。如果它过去一年亏 30%，你上实盘只会亏得更快。"回测"就是让策略回到过去，按 **akshare 拉的真实行情** 重新交易一次，看曲线。

## 为什么这一步不可跳过？

到 Step 5 结束，你能让策略**在活的 tick 上做决策**。但有几个问题回答不了：

1. **不知道策略在历史上赚不赚钱** ——可能你花了 5 小时调参数，结果在 2024 年那波下跌里亏得渣都不剩
2. **参数没优化** ——5/20 是拍脑袋定的，到底是 3/15 好还是 8/30 好？每个参数组合跑一遍才知道
3. **没看过"最大回撤"** ——实盘时遇到 30% 浮亏你能不能扛住？回测能提前告诉你
4. **手续费 / 滑点没算进去** ——au 一天交易 50 次，光手续费一年就能吞掉 30% 收益
5. **合成数据是骗自己** ——上一版的 `feed_data.py` 生成的是随机价格，跑出来的曲线毫无参考价值

解决方案：
1. **akshare** ——免费、无需 API Key、从新浪拉真实历史 K 线
2. **vnpy BacktestingEngine** ——把真实历史 bar 按时间顺序重放，每根推给策略 `on_bar`
3. **vnpy 默认 SQLite 数据库** ——历史 bar 必须先存到 vnpy 数据库里，回测引擎才会读
4. **calculate_statistics()** ——自动算夏普 / 最大回撤 / 收益回撤比
5. **show_chart()** ——一行出 plotly 资金曲线 + 回撤阴影

## 这一步我们做了什么？

```text
新增 3 个文件：
├── double_ma_strategy.py    # vnpy 兼容版 DoubleMaStrategy（继承 vnpy 的 CtaTemplate）
├── feed_data.py             # akshare 拉真实 K 线 → 写入 vnpy SQLite 数据库
└── run_backtest.py          # 主入口：load_data → run_backtesting → show_chart

依赖（必须 pip install）：
├── vnpy[cta]                # vnpy 框架本身
├── vnpy_ctastrategy         # 含 BacktestingEngine
├── vnpy_sqlite              # 默认数据库驱动
├── plotly                   # show_chart 出图
├── deap                     # 后续参数优化要用
└── akshare                  # ★ 这一步新增：拉真实行情
```

## 概念 1 ｜ 回测 = 用真实历史数据重放事件

回测引擎的本质和 Step 5 的 CtaEngine **结构完全一样**——都是"按时间顺序触发策略的回调"：

```python
# Step 5 的 CtaEngine 喂 tick → 策略 on_tick
# Step 6 的 BacktestingEngine 喂 bar → 策略 on_bar（注意是 bar 不是 tick）
```

```python
class BacktestingEngine:
    def new_bar(self, bar: BarData) -> None:
        self.cross_limit_order()      # 先撮合上一根挂的单
        self.cross_stop_order()
        self.strategy.on_bar(bar)     # 再把当前 bar 喂给策略
        self.update_daily_close(bar.close_price)
```

**唯一的区别**：数据源从 SimNow 实时 tick 换成 **vnpy SQLite 里的真实历史 bar**。其他都不变——策略代码 0 修改。

## 概念 2 ｜ akshare 拉数据：免费但有限制

```python
import akshare as ak

# 1 分钟线 —— 最近 3-5 个交易日（新浪接口限制，不能跨月）
df = ak.futures_zh_minute_sina(symbol="AU2606", period="1")

# 日线 —— 全部历史（推荐用于"中期双均线"回测）
df = ak.futures_zh_daily_sina(symbol="AU2606")
```

返回的 DataFrame 列：

| 列 | 含义 | → vnpy BarData 字段 |
|---|---|---|
| `datetime` / `date` | 时间戳 | `datetime` |
| `open` | 开盘价 | `open_price` |
| `high` | 最高价 | `high_price` |
| `low` | 最低价 | `low_price` |
| `close` | 收盘价 | `close_price` |
| `volume` | 成交量 | `volume` |
| `hold` | 持仓量 | `open_interest` |

**关键限制**：
- `futures_zh_minute_sina` 只返回**最近 ~3-5 个交易日**（新浪前端接口，没历史分页）
- `futures_zh_daily_sina` 返回**全历史**，但日线不适合双均线 5/20 这种分钟级策略
- 想拿更长的分钟线 → 用 tushare pro / vnpy_tushare（需 token）

**实战建议**：
- **验证流程**：用 1m 线（哪怕只有 3 天），看金叉 / 死叉信号能不能触发
- **验证策略**：用日线 + 调大窗口（fast=5 天, slow=20 天），看过去 1-2 年曲线
- **真做评估**：用 tushare pro 拉 1m 数据，至少覆盖半年

## 概念 3 ｜ 撮合逻辑："下一根 bar 开盘价成交"

```python
# BacktestingEngine.cross_limit_order() 核心（简化）：
for order in active_limit_orders.values():
    if order.direction == LONG and order.price <= bar.low_price:
        fill_price = min(order.price, bar.open_price)   # ← 用下一根 bar 的 open！
        order.traded = order.volume
        strategy.pos += order.volume                   # ← 直接改 strategy.pos
        strategy.on_trade(trade)
```

**关键陷阱**：回测里 buy(price) 不是"在 price 这根 bar 上成交"，而是"在**下一根** bar 的 open 上成交"。这导致：

- 你看到金叉那一刻 buy，**真正成交价是下一根 bar 的开盘**（不是金叉那根的收盘）
- 滑点（slippage=0.02）算在成交价里——买入 = open + slippage，卖出 = open - slippage
- 价格刚好没穿过 → 不成交（这是好事，避免未来函数）

## 概念 4 ｜ 关键参数 5 个，错一个结果就骗人

| 参数 | 含义 | au2606 怎么填 | 填错的代价 |
|---|---|---|---|
| `rate` | 手续费率 | 0.0005（万 0.5） | 一年少算 5% 利润 |
| `slippage` | 滑点（元） | 0.02 | 频繁交易者少算 20% |
| `size` | 合约乘数 | 1000（1000g/手） | 盈亏放大 1000 倍 |
| `pricetick` | 最小变动 | 0.02 | 价格取整错位 |
| `capital` | 初始资金 | 1_000_000 | 影响收益百分比基数 |

**经验法则**：填完之后人工算一遍——"一手 au 涨 1 元赚 1000 元？"对得上 size=1000 就填对了。

## 概念 5 ｜ 数据必须先喂给 vnpy 数据库

```python
# feed_data.py 核心（先存数据，回测才能 load）
bars = [BarData(symbol="AU2606", exchange=Exchange.SHFE, ...), ...]
db = get_database()                           # 默认 SQLite
db.save_bar_data(bars)                        # 存

# run_backtest.py 核心（回测读数据）
engine.load_data()                            # 内部调 db.load_bar_data(start, end)
engine.run_backtesting()                      # 按时间顺序回放
```

**为什么必须用 vnpy 数据库？** 因为 `engine.load_data()` 内部固定从 `get_database()` 拉数据——不接受内存里的 list。

```python
def load_bar_data(symbol, exchange, interval, start, end):
    return get_database().load_bar_data(symbol, exchange, interval, start, end)
```

所以 **akshare 拉数据 → 存 SQLite → 回测**，是不可绕开的环节。

## 完整数据流

```
   ┌──────────────┐                                       ┌────────────────────────┐
   │ akshare      │  futures_zh_minute_sina               │ vnpy SQLite（默认 db）   │
   │ futures_zh_  │ ───────────────save_bar_data────────→ │  表 dbbar_data         │
   │ daily_sina   │                                       └────────────┬───────────┘
   └──────────────┘                                                    │
                                                                        │ load_bar_data
                                                                        ▼
   ┌─────────────────────────────┐    ┌────────────────────────────┐
   │ BacktestingEngine           │    │ DoubleMaStrategy            │
   │ ─────────────────────────   │    │ ─────────────────────────   │
   │ set_parameters(...)         │    │ on_bar(bar):                │
   │ add_strategy(DoubleMa,{})   │    │   bars.append(...)          │
   │ load_data()    ←──DB        │    │   if 金叉 and pos==0:       │
   │ run_backtesting()           │ ─→ │     self.buy(price, 1)      │
   │   for bar in history:       │    │   if 死叉 and pos>0:        │
   │     new_bar(bar)            │    │     self.sell(price, 1)     │
   │       ├─ cross_limit_order  │    │                            │
   │       └─ strategy.on_bar    │    │  strategy.pos 由 Backtest-  │
   │ calculate_result()  → DataFrame     │ Engine 直接维护           │
   │ calculate_statistics() → dict        │
   │ show_chart()      → plotly HTML      │
   └─────────────────────────────┘
```

## 跑一下

```bash
# 0) 装依赖（一次性）
pip install vnpy_ctastrategy vnpy_sqlite plotly deap akshare

# 1) 拉真实 1m 数据（最近 3-5 个交易日）
cd ~/quantllm/Demo/step6_backtest
python feed_data.py

# 输出：
#   [feed_data] 合约=AU2606 周期=1m
#   [feed_data] 拉到 1024 根 bar，时间范围 2024-xx-xx 21:00 ~ 2024-xx-xx 15:00
#   [feed_data] 价格区间 612.50 ~ 615.20
#   [feed_data] 总成交量 12345
#   [feed_data] 数据库已确认：AU2606.SHFE [1m] 共 1024 条

# 2) 跑回测
python run_backtest.py

# 输出（关键三段）：
#   a) 回放进度：==== 100%  ====
#   b) 策略日志：金叉 cur=(...) prev=(...) @ price
#   c) 绩效统计：sharpe_ratio / max_drawdown / return_drawdown_ratio
```

按顺序在终端里找这 3 类输出：

1. **数据写入**：`数据库已确认：AU2606.SHFE [1m] 共 N 条 从 ... 到 ...`
2. **回放进度**：`回放进度：==========  [100%]`
3. **绩效统计**：找 `=== 回测绩效统计 ===` 这块下面 3 个核心字段：
   - `sharpe_ratio`（夏普比率，> 1.5 算还行）
   - `max_drawdown`（最大回撤，< 20% 算合格）
   - `return_drawdown_ratio`（收益回撤比，> 2 算好）

回测结束会在当前目录生成 `backtest_result.html`——双击在浏览器打开，能看到资金曲线 + 最大回撤阴影。

## 试试自己改

### 改动 1 ｜ 用日线 + 调大窗口：fast=5, slow=20（单位 = 天）

```bash
python feed_data.py --interval 1d
python run_backtest.py --interval 1d --fast 5 --slow 20
```

回测周期变成日线，5 天均线上穿 20 天均线买开。每天开盘只算一次，金叉/死叉信号比 1m 少得多，结果更稳。但数据覆盖能到 1-2 年（akshare 日线没限制）。

### 改动 2 ｜ 换合约：螺纹钢 RB2505

```bash
python feed_data.py --symbol RB2505
python run_backtest.py --symbol RB2505 --rate 0.0001 --slippage 0.5 --size 10 --pricetick 1
```

⚠️ RB 合约参数和 au **完全不同**：
- `size=10`（RB 是 10 吨/手，不是 1000g）
- `pricetick=1`（RB 最小变动 1 元）
- `slippage=0.5`（RB 滑点比 au 大）

填错一个，回测结果就崩。

### 改动 3 ｜ 打开 backtest_result.html 看资金曲线

```bash
open backtest_result.html        # macOS
xdg-open backtest_result.html    # Linux
start backtest_result.html       # Windows
```

plotly 默认会画 3 个子图：
1. **资金曲线 + 最大回撤阴影** ——曲线越平滑越好，回撤阴影越窄越好
2. **每日盈亏柱状图** ——绿色赚钱、红色亏钱
3. **回撤百分比** ——最深谷底对应 `max_ddpercent`

如果曲线整体向上但回撤很大 → 策略能用，但仓位要小；如果曲线横着走没收益 → 策略无意义。

## 调试手册（Step 6 专享）

| 故障 | 第一时间检查 |
|---|---|
| `ModuleNotFoundError: vnpy_ctastrategy` | `pip install vnpy_ctastrategy` |
| `ModuleNotFoundError: vnpy_sqlite` | `pip install vnpy_sqlite` |
| `ModuleNotFoundError: akshare` | `pip install akshare` |
| `feed_data: 拉到 0 根 bar` | 合约没挂牌（au 主力月是 06/12），改 `--symbol AU2506`；或网络问题重试 |
| `feed_data: 拉到 1024 根就停` | 正常，新浪只给最近 3-5 天；想更长用 `--interval 1d` |
| `回测成交记录为空` | 数据库里没数据 → 重新跑 `python feed_data.py` |
| `回测进度 < 100%` 异常退出 | 看策略日志里的 traceback，多半是 `bars` 索引越界（数据少于 slow_window+1） |
| `起始日期必须小于结束日期` | feed_data.py 跑得太早，生成的 bar 都在"今天"之前；调 `--days` |
| 资金曲线肉眼看着不对 | 检查 `size / pricetick` 是不是 1000 / 0.02；au 涨 1 元 = 赚 1000 元 |
| 收益爆炸 1000% | `size` 写错了，可能是 1 而不是 1000 |

## 下一步预告

Step 6 跑通了，但你只是"看到一组结果"。参数 5/20 是拍脑袋定的——

Step 7 我们做**参数优化**：`run_optimization(setting)` 让 vnpy 在 `fast_window ∈ [3,10]`、`slow_window ∈ [10,30]` 范围里枚举所有组合，按夏普或收益回撤比排序，看哪些参数组合是"真稳定赚钱"还是"恰好拟合这一段历史"。

⚠️ 但要小心**过拟合**：参数越拟合过去，未来越不稳。Step 7 也会教你用"样本内/样本外分割"做交叉验证。

## Sources（这一 step 用到的真实数据源）

- [akshare 期货数据文档](https://akshare.akfamily.xyz/data/futures/futures.html) — `futures_zh_minute_sina` 和 `futures_zh_daily_sina` 的列名、参数、限制
- [akshare GitLab](https://gitcode.cosmoplat.com/akshare/akshare/-/blob/dd1fa7447d6fb64b387c017019b0d0c3a0379959/akshare/futures/futures_zh_sina.py) — 源码确认接口返回字段
- [CSDN: 期货数据读取](https://blog.csdn.net/tcy23456/article/details/80946838) — 新浪期货 K 线数据原理