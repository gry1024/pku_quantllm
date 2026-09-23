"""Step 4 demo: DummyStrategy —— 只在 on_tick 里打印价格，不下单。

用于验证 CtaTemplate 的生命周期 + on_tick 被 EventEngine 持续调用。
"""

from cta_template import CtaTemplate


class DummyStrategy(CtaTemplate):
    """最简策略：只观察，不交易。"""

    def on_init(self) -> None:
        self.write_log("DummyStrategy on_init 完成（无历史数据加载）")

    def on_start(self) -> None:
        super().on_start()                  # ← trading=True
        self.write_log("DummyStrategy on_start：开始接收 tick")

    def on_stop(self) -> None:
        super().on_stop()                   # ← trading=False
        self.write_log("DummyStrategy on_stop")

    def on_tick(self, tick: dict) -> None:
        self._tick_count += 1
        # 每 10 个 tick 打一次（避免刷屏）
        if self._tick_count % 10 == 1:
            self.write_log(
                f"TICK #{self._tick_count} "
                f"最新={tick['LastPrice']:.1f} "
                f"买一={tick['BidPrice1']:.1f}x{tick['BidVolume1']} "
                f"卖一={tick['AskPrice1']:.1f}x{tick['AskVolume1']}"
            )

    def on_order(self, order: dict) -> None:
        # 父类没实现，DummyStrategy 也不需要，但为演示打个 log
        self.write_log(
            f"ORDER 状态={order['OrderStatus']} {order['StatusMsg']}"
        )

    def on_trade(self, trade: dict) -> None:
        self.write_log(
            f"TRADE {trade['Volume']}手 @ {trade['Price']:.1f} "
            f"TradeID={trade['TradeID']}"
        )