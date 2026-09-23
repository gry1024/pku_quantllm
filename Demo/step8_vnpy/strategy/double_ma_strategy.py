"""Step 8: DoubleMaStrategy —— 直接继承 vnpy CtaTemplate。"""

from __future__ import annotations

from vnpy_ctastrategy import CtaTemplate
from vnpy.trader.object import BarData


class DoubleMaStrategy(CtaTemplate):
    """双均线：5 上穿 20 买开，5 下穿 20 卖平（1 分钟 K 线）。"""

    author: str = "quantllm"

    parameters: list = ["fast_window", "slow_window"]
    variables: list = ["fast_ma", "slow_ma"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.fast_window: int = setting.get("fast_window", 5)
        self.slow_window: int = setting.get("slow_window", 20)

        self.fast_ma: float = 0.0
        self.slow_ma: float = 0.0

        self.bars: list = []

    def on_init(self) -> None:
        self.write_log(
            f"DoubleMa 初始化：fast={self.fast_window} slow={self.slow_window}"
        )

    def on_start(self) -> None:
        super().on_start()
        self.write_log("DoubleMa 开始接收 bar")

    def on_bar(self, bar: BarData) -> None:
        self.bars.append(bar)
        if len(self.bars) > self.slow_window + 1:
            self.bars = self.bars[-(self.slow_window + 1):]

        if len(self.bars) < self.slow_window + 1:
            return

        closes = [b.close_price for b in self.bars]
        cur_fast = sum(closes[-self.fast_window:]) / self.fast_window
        cur_slow = sum(closes[-self.slow_window:]) / self.slow_window
        prev_closes = closes[:-1]
        prev_fast = sum(prev_closes[-self.fast_window:]) / self.fast_window
        prev_slow = sum(prev_closes[-self.slow_window:]) / self.slow_window

        self.fast_ma = round(cur_fast, 4)
        self.slow_ma = round(cur_slow, 4)

        price = round(bar.close_price, 2)

        if prev_fast <= prev_slow and cur_fast > cur_slow and self.pos == 0:
            self.write_log(
                f"金叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f}) → 买开"
            )
            self.buy(price, volume=1)
            return

        if prev_fast >= prev_slow and cur_fast < cur_slow and self.pos > 0:
            self.write_log(
                f"死叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f}) → 卖平"
            )
            self.sell(price, volume=1)

    def on_stop(self) -> None:
        self.write_log("DoubleMa 停止")