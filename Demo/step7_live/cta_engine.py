"""Step 5: CtaEngine —— 多策略管理 + 事件分发 + 持仓维护。

升级 vs Step 4 占位版：
- add_strategy / init_all / start_all / stop_all  —— 多策略生命周期
- 注册 EVENT_TICK / EVENT_ORDER / EVENT_TRADE / EVENT_POSITION 四个 handler
- 按 vt_symbol 匹配，把事件分发给对应策略
- on_trade 时自动维护策略 self.pos（+Volume / -Volume）

设计：策略引擎不直接 import CtaTemplate（避免循环依赖），
只在 add_strategy 时 duck-type（要求有 strategy_name / vt_symbol / on_xxx 方法）。
"""

from event_engine import (
    EVENT_TICK, EVENT_ORDER, EVENT_TRADE, EVENT_POSITION,
)


class CtaEngine:
    """策略引擎：管理多策略 + 把 Gateway 事件按 vt_symbol 路由给策略 + 维护策略持仓。"""

    def __init__(self, main_engine, event_engine) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine
        self.strategies: dict = {}

        # 把 4 类事件订阅到自己的分发函数
        self.event_engine.register(EVENT_TICK, self._process_tick)
        self.event_engine.register(EVENT_ORDER, self._process_order)
        self.event_engine.register(EVENT_TRADE, self._process_trade)
        self.event_engine.register(EVENT_POSITION, self._process_position)

    # ============== 策略管理 ==============
    def add_strategy(self, strategy) -> None:
        """注册一个策略实例。"""
        self.strategies[strategy.strategy_name] = strategy
        self.write_log(
            f"策略 {strategy.strategy_name} 已注册（合约={strategy.vt_symbol}）"
        )

    def init_all(self) -> None:
        """对所有策略调用 on_init。"""
        for s in self.strategies.values():
            s.on_init()
            s.inited = True

    def start_all(self) -> None:
        """对所有策略调用 on_start（若未 init 则先 init）。"""
        for s in self.strategies.values():
            if not s.inited:
                s.on_init()
                s.inited = True
            s.on_start()

    def stop_all(self) -> None:
        """对所有策略调用 on_stop。"""
        for s in self.strategies.values():
            s.on_stop()

    # ============== 委托给 MainEngine ==============
    def send_order(self, vt_symbol: str, direction: str, offset: str,
                   price: float, volume: int) -> str:
        return self.main_engine.send_order(
            vt_symbol, direction, offset, price, volume
        )

    def write_log(self, msg: str) -> None:
        self.main_engine.write_log(msg)

    # ============== 事件分发（按 vt_symbol 路由）==============
    @staticmethod
    def _vt_symbol_of(data: dict) -> str:
        """从 CTP 数据 dict 构造 vt_symbol: '合约.交易所'。"""
        return f"{data['InstrumentID']}.{data['ExchangeID']}"

    def _process_tick(self, event) -> None:
        vt = self._vt_symbol_of(event.data)
        for s in self.strategies.values():
            if s.vt_symbol == vt:
                s.on_tick(event.data)

    def _process_order(self, event) -> None:
        vt = self._vt_symbol_of(event.data)
        for s in self.strategies.values():
            if s.vt_symbol == vt:
                s.on_order(event.data)

    def _process_trade(self, event) -> None:
        vt = self._vt_symbol_of(event.data)
        d = event.data
        for s in self.strategies.values():
            if s.vt_symbol == vt:
                # 维护策略净持仓：买 +Volume，卖 -Volume
                if d["Direction"] == "0":
                    s.pos += d["Volume"]
                else:
                    s.pos -= d["Volume"]
                s.on_trade(d)

    def _process_position(self, event) -> None:
        # 简化：策略 pos 完全由 _process_trade 维护；
        # POSITION 事件只用来打日志，不反向覆写（避免 CTP 异步回报错位）
        pass