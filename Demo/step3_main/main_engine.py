"""Step 3: MainEngine —— Gateway 容器 + 高层交易 API。

设计意图：
- 上层（策略 / UI / 风控 / 记录器）只跟 MainEngine 打交道，不直接 import Gateway
- vt_symbol = '合约.交易所' 是 vnpy 内部的全局唯一 ID 约定
- 之后扩展多 Gateway（CTP + IB + 币安）时，调用方代码不变
"""

from event_engine import EventEngine, Event, EVENT_LOG
from ctp_gateway import CtpGateway


class MainEngine:
    """系统大脑：持有 N 个 Gateway + EventEngine；提供统一的高层交易 API。"""

    def __init__(self, event_engine: EventEngine) -> None:
        self.event_engine = event_engine
        self.gateways: dict[str, CtpGateway] = {}

    # ============== Gateway 管理 ==============
    def add_gateway(self, name: str, gateway: CtpGateway) -> None:
        """注册一个 Gateway。同一名字重复注册会覆盖。"""
        self.gateways[name] = gateway
        self.write_log(f"已注册网关: {name}")

    def get_gateway(self, name: str) -> CtpGateway:
        """按名字取 Gateway；不存在则抛错（避免静默失败）。"""
        if name not in self.gateways:
            raise KeyError(
                f"未注册的网关: {name}（已注册: {list(self.gateways)}）"
            )
        return self.gateways[name]

    def connect(self, name: str, setting: dict | None = None) -> None:
        """发起指定 Gateway 的连接。setting 可选，留空则用 Gateway 已有的。"""
        gateway = self.get_gateway(name)
        if setting is not None:
            gateway.setting.update(setting)        # 支持热切换连接参数
        gateway.connect()
        self.write_log(f"网关 {name} 已发起连接")

    def close(self, name: str) -> None:
        gateway = self.get_gateway(name)
        gateway.close()
        self.write_log(f"网关 {name} 已关闭")

    # ============== 行情 ==============
    def subscribe(self, name: str, vt_symbols: list[str]) -> None:
        """按 vt_symbol 列表订阅行情。vt_symbol 格式: '合约.交易所'。"""
        gateway = self.get_gateway(name)
        symbols = [self._parse_vt_symbol(v)[0] for v in vt_symbols]
        gateway.subscribe(symbols)
        self.write_log(f"网关 {name} 订阅: {vt_symbols}")

    # ============== 交易（高层 API）==============
    def send_order(self, vt_symbol: str, direction: str, offset: str,
                   price: float, volume: int,
                   gateway_name: str = "CTP") -> str:
        """发出限价单。返回 OrderRef（"" 表示未就绪）。"""
        gateway = self.get_gateway(gateway_name)
        symbol, exchange = self._parse_vt_symbol(vt_symbol)
        order_ref = gateway.send_order(symbol, exchange, direction, offset,
                                       price, volume)
        if order_ref:
            self.write_log(
                f">>> {vt_symbol} {direction} {offset} {volume}手 @ {price} "
                f"ref={order_ref}"
            )
        return order_ref

    def cancel_order(self, vt_orderid: str, gateway_name: str = "CTP") -> None:
        """撤单。vt_orderid 格式 'FrontID_SessionID_OrderRef'。"""
        gateway = self.get_gateway(gateway_name)
        gateway.cancel_order(vt_orderid)
        self.write_log(f"<<< 撤单 {vt_orderid}")

    # ============== 查询 ==============
    def query_account(self, gateway_name: str = "CTP") -> None:
        gateway = self.get_gateway(gateway_name)
        gateway.query_account()

    def query_position(self, gateway_name: str = "CTP") -> None:
        gateway = self.get_gateway(gateway_name)
        gateway.query_position()

    # ============== 日志（统一入口）==============
    def write_log(self, msg: str) -> None:
        """统一 LOG 入口；通过 EventEngine 发出 EVENT_LOG。"""
        self.event_engine.put(Event(EVENT_LOG, {"msg": msg}))

    # ============== 工具 ==============
    @staticmethod
    def _parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
        """'au2632.SHFE' -> ('au2632', 'SHFE')。

        用 rsplit 而不是 split：合约代码本身可能含 '.'（少见但保留兼容）。
        """
        if "." not in vt_symbol:
            raise ValueError(f"vt_symbol 格式错 (应为 '合约.交易所'): {vt_symbol}")
        symbol, exchange = vt_symbol.rsplit(".", 1)
        return symbol, exchange