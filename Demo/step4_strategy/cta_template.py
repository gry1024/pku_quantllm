"""Step 4: CtaTemplate —— 所有策略的父类。

子类只需重写 on_xxx 方法即可：
  - on_init    : 初始化（订阅 / 加载历史数据）
  - on_start   : 开始交易（trading=True）
  - on_stop    : 停止（trading=False）
  - on_tick    : 行情推送（每笔 tick）
  - on_order   : 委托状态变化
  - on_trade   : 成交通知
  - on_bar     : K 线收盘（Step 5 接 BarGenerator）

设计原则：
  - 策略是"无状态的事件处理器"：所有信息都通过 on_xxx 喂进来
  - 持仓由策略自己维护（self.pos），不直接查 CTP —— 这样回测时策略代码 0 修改
  - buy/sell 的追价逻辑是策略的事，不是 Gateway 的事
"""


class CtaTemplate:
    """策略基类。"""

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        self.cta_engine = cta_engine
        self.strategy_name = strategy_name
        self.vt_symbol = vt_symbol          # 'au2612.SHFE'
        self.parameters: dict = setting      # 策略可自定义参数
        self.inited: bool = False            # on_init 是否调用过
        self.trading: bool = False           # 是否在 trading 状态
        self.pos: int = 0                    # 净持仓（+多 -空；Step 5 由引擎维护）
        self._tick_count: int = 0            # 收到的 tick 计数（demo 用）

    # ============== 生命周期（子类按需重写）==============
    def on_init(self) -> None:
        """初始化：通常在这里订阅行情 / 加载历史数据。"""
        pass

    def on_start(self) -> None:
        """开始：通常 super().on_start() 把 trading 置 True。"""
        self.trading = True

    def on_stop(self) -> None:
        """停止：通常 super().on_stop() 把 trading 置 False。"""
        self.trading = False

    # ============== 行情 / 回报（子类必重写）==============
    def on_tick(self, tick: dict) -> None:
        """每笔 tick 推送。"""
        pass

    def on_order(self, order: dict) -> None:
        """委托状态变化（含 onRspOrderInsert 拒单 + onRtnOrder 状态流）。"""
        pass

    def on_trade(self, trade: dict) -> None:
        """成交通知。"""
        pass

    def on_bar(self, bar: dict) -> None:
        """K 线收盘（Step 5 接 BarGenerator）。"""
        pass

    # ============== 交易工具方法（子类直接调用）==============
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

    def cancel_all(self) -> None:
        """撤所有本策略的活单（Step 5 实现）。"""
        pass

    # ============== 信息查询 / 持久化 ==============
    def get_pos(self) -> int:
        return self.pos

    def write_log(self, msg: str) -> None:
        """统一日志入口（带策略名前缀）。"""
        self.cta_engine.write_log(f"[{self.strategy_name}] {msg}")

    def save_param(self) -> None:
        """把策略参数保存到本地 JSON（Step 5 实现，pickle）。"""
        import json
        from pathlib import Path
        path = Path(__file__).parent / "data" / f"{self.strategy_name}.json"
        path.parent.mkdir(exist_ok=True)
        with path.open("w") as f:
            json.dump(self.parameters, f, indent=2)