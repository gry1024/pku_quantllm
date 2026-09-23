"""Step 4 占位 CtaEngine —— 只暴露 main_engine + event_engine 的命名空间。

Step 5 会扩展为完整版：
  - 多策略管理 (add_strategy / init_strategy / start_strategy / stop_strategy)
  - 事件分发 (tick → 所有 trading=True 的策略)
  - K 线合成 (BarGenerator)
  - 持仓 self.pos 自动维护
"""


class CtaEngine:
    """Step 4 minimal: 仅做 main_engine / event_engine 的桥接。"""

    def __init__(self, main_engine, event_engine) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine
        self.strategies: dict = {}      # Step 5 才用

    def send_order(self, vt_symbol: str, direction: str, offset: str,
                   price: float, volume: int) -> str:
        """委托给 main_engine.send_order。"""
        return self.main_engine.send_order(vt_symbol, direction, offset,
                                           price, volume)

    def write_log(self, msg: str) -> None:
        self.main_engine.write_log(msg)