"""Step 6: DoubleMaStrategy —— vnpy 兼容版。

跟 Step 5 的 DoubleMaStrategy 逻辑完全一样：
    - 5/20 均线金叉买开、死叉卖平
    - 每根 1 分钟 K 线收盘时算一次
差异：
    - 父类换成 vnpy.app.cta_strategy.template.CtaTemplate
    - bar 不再走自己的 BarGenerator —— BacktestingEngine 会按根推 BarData
    - 持仓字段 self.pos 由 BacktestingEngine 直接维护（on_trade 内部累加）
    - buy / sell 走 vnpy 的语义（Direction.LONG + Offset.OPEN 等）

⚠️ 这个策略既能在 Step 5 自研 CtaEngine 里跑，
    又能在 vnpy BacktestingEngine 里跑 —— 因为逻辑只看 on_bar + self.pos。
"""

from collections import deque
from typing import Deque

from vnpy.trader.constant import Interval
from vnpy_ctastrategy.template import CtaTemplate


class DoubleMaStrategy(CtaTemplate):
    """双均线策略（分钟 K 线，金叉买开/死叉卖平）。"""

    author: str = "quantllm"
    parameters: list = ["fast_window", "slow_window"]
    variables: list = ["bars"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        # 参数默认值（setting 缺则用这些；父类已把 setting 同名 key 注入 self）
        if not hasattr(self, "fast_window"):
            self.fast_window: int = 5
        if not hasattr(self, "slow_window"):
            self.slow_window: int = 20

        # K 线历史（vnpy 的 BacktestingEngine 推送 BarData，我们自己只缓存 close）
        self.bars: Deque[dict] = deque(maxlen=500)

    def on_init(self) -> None:
        """回测/实盘 init：vnpy BacktestingEngine 会在 run_backtesting() 之前调一次。"""
        self.write_log(
            f"DoubleMa 初始化：fast={self.fast_window} slow={self.slow_window}"
        )

    def on_start(self) -> None:
        super().on_start()
        self.write_log("DoubleMa 开始接收 bar")

    def on_stop(self) -> None:
        super().on_stop()
        self.write_log("DoubleMa 停止")

    def on_bar(self, bar) -> None:
        """每根 1 分钟 K 线收盘时由 BacktestingEngine 调用。

        bar 是 vnpy.trader.object.BarData，有 close_price / datetime 等字段。
        我们缓存 close + datetime 到 self.bars（deque）算 SMA。
        """
        # 缓存 close（BarData 有 .close_price，跟 Step 5 dict 的 'close' 对应）
        self.bars.append({"datetime": bar.datetime, "close": bar.close_price})

        # 至少要 slow_window + 1 根 bar 才能对比"上一根 bar 的 MA"
        if len(self.bars) < self.slow_window + 1:
            return

        # 当前 bar 收盘的 SMA（用最近 N 根 close）
        closes = [b["close"] for b in self.bars]
        cur_fast = sum(closes[-self.fast_window:]) / self.fast_window
        cur_slow = sum(closes[-self.slow_window:]) / self.slow_window
        # 上一根 bar 的 SMA（去掉最后一根）
        prev_closes = closes[:-1]
        prev_fast = sum(prev_closes[-self.fast_window:]) / self.fast_window
        prev_slow = sum(prev_closes[-self.slow_window:]) / self.slow_window

        price = round(bar.close_price, 2)

        # 金叉：fast 上穿 slow，且当前空仓 → 买开
        if prev_fast <= prev_slow and cur_fast > cur_slow and self.pos == 0:
            self.write_log(
                f"金叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f}) @ {price}"
            )
            self.buy(price, volume=1)
            return

        # 死叉：fast 下穿 slow，且当前持 1 手多 → 卖平
        if prev_fast >= prev_slow and cur_fast < cur_slow and self.pos > 0:
            self.write_log(
                f"死叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f}) @ {price}"
            )
            self.sell(price, volume=1)