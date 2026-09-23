"""Step 5: DoubleMaStrategy —— 5/20 分钟均线金叉买开 / 死叉卖平。

⚠️ test_only=True（默认）：信号触发时只打 log，不真下单。
    真要下单：把 setting 里 test_only 改成 False。

用法：
    strategy = DoubleMaStrategy(cta, "double_ma_au", "au2612.SHFE", {
        "fast_window": 5,
        "slow_window": 20,
        "test_only": False,            # ← 改这里才真下单
    })
"""

from cta_template import CtaTemplate


class DoubleMaStrategy(CtaTemplate):
    """双均线策略（分钟 K 线）。"""

    parameters = ["fast_window", "slow_window", "test_only"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        # 参数默认值（setting 缺则用这些；父类 __init__ 已经把 setting 里的同名 key 注入 self）
        if not hasattr(self, "fast_window"):
            self.fast_window: int = 5
        if not hasattr(self, "slow_window"):
            self.slow_window: int = 20
        if not hasattr(self, "test_only"):
            self.test_only: bool = True

    def on_init(self) -> None:
        self.write_log(
            f"DoubleMa 初始化：fast={self.fast_window} slow={self.slow_window} "
            f"test_only={self.test_only}"
        )

    def on_start(self) -> None:
        super().on_start()
        self.write_log("DoubleMa 开始接收 bar（需累积 ≥ slow_window+1 根才出信号）")

    def on_bar(self, bar: dict) -> None:
        # 至少要 slow_window + 1 根 bar 才能对比"上一根 bar 的 MA"
        if len(self.bars) < self.slow_window + 1:
            self.write_log(
                f"bar 累积中：{len(self.bars)}/{self.slow_window + 1}"
            )
            return

        # 当前 bar 收盘的 SMA（用最近 N 根 close）
        closes = [b["close"] for b in self.bars]
        cur_fast = sum(closes[-self.fast_window:]) / self.fast_window
        cur_slow = sum(closes[-self.slow_window:]) / self.slow_window
        # 上一根 bar 的 SMA（去掉最后一根）
        prev_closes = closes[:-1]
        prev_fast = sum(prev_closes[-self.fast_window:]) / self.fast_window
        prev_slow = sum(prev_closes[-self.slow_window:]) / self.slow_window

        price = round(bar["close"], 2)

        # 金叉：fast 上穿 slow，且当前空仓
        if prev_fast <= prev_slow and cur_fast > cur_slow and self.pos == 0:
            self.write_log(
                f"金叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f})"
            )
            if self.test_only:
                self.write_log(f"[测试模式] 本应买开 1 手 @ {price}")
            else:
                self.buy(price, volume=1)
            return

        # 死叉：fast 下穿 slow，且当前持 1 手多
        if prev_fast >= prev_slow and cur_fast < cur_slow and self.pos > 0:
            self.write_log(
                f"死叉 cur=({cur_fast:.2f}/{cur_slow:.2f}) "
                f"prev=({prev_fast:.2f}/{prev_slow:.2f})"
            )
            if self.test_only:
                self.write_log(f"[测试模式] 本应卖平 1 手 @ {price}")
            else:
                self.sell(price, volume=1)