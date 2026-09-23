"""Step 4 复用 Step 3 的 MainEngine。"""

from event_engine import EventEngine, Event, EVENT_LOG
from ctp_gateway import CtpGateway


class MainEngine:
    def __init__(self, event_engine: EventEngine) -> None:
        self.event_engine = event_engine
        self.gateways: dict[str, CtpGateway] = {}

    def add_gateway(self, name: str, gateway: CtpGateway) -> None:
        self.gateways[name] = gateway
        self.write_log(f"已注册网关: {name}")

    def get_gateway(self, name: str) -> CtpGateway:
        if name not in self.gateways:
            raise KeyError(
                f"未注册的网关: {name}（已注册: {list(self.gateways)}）"
            )
        return self.gateways[name]

    def connect(self, name: str, setting: dict | None = None) -> None:
        gateway = self.get_gateway(name)
        if setting is not None:
            gateway.setting.update(setting)
        gateway.connect()
        self.write_log(f"网关 {name} 已发起连接")

    def close(self, name: str) -> None:
        gateway = self.get_gateway(name)
        gateway.close()
        self.write_log(f"网关 {name} 已关闭")

    def subscribe(self, name: str, vt_symbols: list[str]) -> None:
        gateway = self.get_gateway(name)
        symbols = [self._parse_vt_symbol(v)[0] for v in vt_symbols]
        gateway.subscribe(symbols)
        self.write_log(f"网关 {name} 订阅: {vt_symbols}")

    def send_order(self, vt_symbol: str, direction: str, offset: str,
                   price: float, volume: int,
                   gateway_name: str = "CTP") -> str:
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
        gateway = self.get_gateway(gateway_name)
        gateway.cancel_order(vt_orderid)
        self.write_log(f"<<< 撤单 {vt_orderid}")

    def query_account(self, gateway_name: str = "CTP") -> None:
        gateway = self.get_gateway(gateway_name)
        gateway.query_account()

    def query_position(self, gateway_name: str = "CTP") -> None:
        gateway = self.get_gateway(gateway_name)
        gateway.query_position()

    def write_log(self, msg: str) -> None:
        self.event_engine.put(Event(EVENT_LOG, {"msg": msg}))

    @staticmethod
    def _parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
        if "." not in vt_symbol:
            raise ValueError(f"vt_symbol 格式错 (应为 '合约.交易所'): {vt_symbol}")
        symbol, exchange = vt_symbol.rsplit(".", 1)
        return symbol, exchange