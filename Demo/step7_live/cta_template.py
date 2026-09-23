"""Step 5: CtaTemplate —— 策略基类（扩展 Step 4）。

Step 5 新增：
- 默认 on_tick 会把 tick 推给 self.bg（BarGenerator），
  BarGenerator 推 bar 时自动调 self.on_bar（用 _on_bar_internal 入口）
- self.bars: deque(maxlen=500)，累积 K 线历史，供策略算 MA / 指标用
- parameters 列表：子类声明参数名，CtaTemplate 自动从 setting 注入

子类的典型写法（DoubleMaStrategy 为例）：
    class DoubleMaStrategy(CtaTemplate):
        parameters = ["fast_window", "slow_window"]

        def on_bar(self, bar):
            if len(self.bars) < self.slow_window + 1:
                return
            closes = [b["close"] for b in self.bars]
            cur_fast = sum(closes[-self.fast_window:]) / self.fast_window
            ...
"""

from collections import deque
from typing import Deque

from bar_generator import BarGenerator


class CtaTemplate:
    """策略基类。"""

    parameters: list[str] = []                        # 子类声明需要的参数名

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        self.cta_engine = cta_engine
        self.strategy_name = strategy_name
        self.vt_symbol = vt_symbol
        self.parameters_dict: dict = setting or {}
        self.inited: bool = False
        self.trading: bool = False
        self.pos: int = 0                              # 净持仓（+多 -空；CtaEngine 在 on_trade 时维护）

        # Step 5 新增：bar 历史 + BarGenerator
        self.bars: Deque[dict] = deque(maxlen=500)
        self.bg = BarGenerator(
            vt_symbol=vt_symbol,
            on_bar=self._on_bar_internal,
        )

        # 子类声明的 parameters 自动从 setting 注入为实例属性
        for p in self.parameters:
            if p in self.parameters_dict:
                setattr(self, p, self.parameters_dict[p])

    # ============== 生命周期（子类按需重写）==============
    def on_init(self) -> None:
        pass

    def on_start(self) -> None:
        self.trading = True

    def on_stop(self) -> None:
        self.trading = False

    # ============== 行情 / 回报（子类按需重写）==============
    def on_tick(self, tick: dict) -> None:
        """默认实现：把 tick 推给 BarGenerator → 1 分钟 bar 触发时自动 on_bar。

        若子类覆盖 on_tick 而不调 super().on_tick()，则不会触发 on_bar。
        """
        self.bg.on_tick(tick)

    def on_order(self, order: dict) -> None:
        pass

    def on_trade(self, trade: dict) -> None:
        pass

    def on_bar(self, bar: dict) -> None:
        """K 线收盘。子类覆盖（分钟策略的主战场）。"""
        pass

    # ============== 交易工具（子类直接调用）==============
    def buy(self, price: float, volume: int = 1) -> str:
        """买开限价单。返回 OrderRef（"" 表示 trading=False 被拒）。"""
        if not self.trading:
            return ""
        return self.cta_engine.send_order(
            self.vt_symbol, "0", "0", price, volume
        )

    def sell(self, price: float, volume: int = 1) -> str:
        """卖平限价单。"""
        if not self.trading:
            return ""
        return self.cta_engine.send_order(
            self.vt_symbol, "1", "1", price, volume
        )

    # ============== 辅助 ==============
    def _on_bar_internal(self, bar: dict) -> None:
        """BarGenerator 触发时模板自动调：累积 bar 后调用户 on_bar。"""
        self.bars.append(bar)
        self.on_bar(bar)

    def write_log(self, msg: str) -> None:
        self.cta_engine.write_log(f"[{self.strategy_name}] {msg}")