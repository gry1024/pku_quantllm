"""Step 3 复用 Step 2 的 CtpGateway（不变）。

证明 MainEngine 的存在不影响 Gateway —— Gateway 只关心"产生事件"，
不知道事件被谁消费。
"""

from pathlib import Path

from vnpy_ctp.api import MdApi, TdApi

from event_engine import (
    EventEngine, Event,
    EVENT_TICK, EVENT_ORDER, EVENT_TRADE,
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG,
)


SETTING = {
    "userid":     "276266",
    "password":   "@Abc303802",
    "brokerid":   "9999",
    "td_address": "tcp://182.254.243.31:30001",
    "md_address": "tcp://182.254.243.31:30011",
    "appid":      "simnow_client_test",
    "auth_code":  "0000000000000000",
}

FLOW_PATH = str(Path(__file__).parent.parent / "flow")


class CtpGateway:
    def __init__(self, event_engine: EventEngine, setting: dict) -> None:
        self.event_engine = event_engine
        self.setting = setting
        self.td_api = _TdApi(self)
        self.md_api = _MdApi(self)
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        self.td_api.connect()
        self.md_api.connect()
        self._connected = True

    def close(self) -> None:
        self.td_api.exit()
        self.md_api.exit()

    def subscribe(self, symbols: list[str]) -> None:
        self.md_api.subscribed.extend(symbols)

    def send_order(self, instrument, exchange, direction, offset, price, volume):
        return self.td_api.send_order(instrument, exchange, direction, offset,
                                      price, volume)

    def cancel_order(self, vt_orderid: str) -> None:
        self.td_api.cancel_order(vt_orderid)

    def query_account(self) -> None:
        self.td_api.query_account()

    def query_position(self) -> None:
        self.td_api.query_position()

    def is_ready(self) -> bool:
        """TD 是否就绪（登录 + 结算单确认）。用于上层判断能否下单。"""
        return self.td_api.login_status and self.td_api.settlement_confirmed

    def emit(self, type_: str, data: dict) -> None:
        self.event_engine.put(Event(type_, data))


class _TdApi(TdApi):
    def __init__(self, gateway: CtpGateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.reqid = 0
        self.order_ref = 0
        self.login_status = False
        self.settlement_confirmed = False
        self.front_id: int = 0
        self.session_id: int = 0

    def connect(self) -> None:
        try:
            self.createFtdcTraderApi(f"{FLOW_PATH}/Td", True)
        except TypeError:
            self.createFtdcTraderApi(f"{FLOW_PATH}/Td")
        self.registerFront(self.gateway.setting["td_address"])
        self.init()

    def send_order(self, instrument, exchange, direction, offset, price, volume):
        if not (self.login_status and self.settlement_confirmed):
            self.gateway.emit(EVENT_LOG, {"msg": "TD 未就绪，忽略下单"})
            return ""
        self.order_ref += 1
        req = {
            "InstrumentID": instrument,
            "ExchangeID": exchange,
            "LimitPrice": price,
            "VolumeTotalOriginal": volume,
            "OrderPriceType": "2",
            "Direction": direction,
            "CombOffsetFlag": offset,
            "OrderRef": str(self.order_ref),
            "InvestorID": self.gateway.setting["userid"],
            "UserID": self.gateway.setting["userid"],
            "BrokerID": self.gateway.setting["brokerid"],
            "CombHedgeFlag": "1",
            "ContingentCondition": "1",
            "ForceCloseReason": "0",
            "IsAutoSuspend": 0,
            "TimeCondition": "3",
            "VolumeCondition": "1",
            "MinVolume": 1,
        }
        self.reqid += 1
        self.reqOrderInsert(req, self.reqid)
        return str(self.order_ref)

    def cancel_order(self, vt_orderid: str) -> None:
        if not (self.login_status and self.settlement_confirmed):
            return
        try:
            front_id, session_id, order_ref = vt_orderid.split("_")
            req = {
                "OrderRef": order_ref,
                "FrontID": int(front_id),
                "SessionID": int(session_id),
                "ActionFlag": "0",
                "BrokerID": self.gateway.setting["brokerid"],
                "InvestorID": self.gateway.setting["userid"],
                "UserID": self.gateway.setting["userid"],
                "InstrumentID": "",
                "ExchangeID": "",
            }
            self.reqid += 1
            self.reqOrderAction(req, self.reqid)
        except ValueError:
            self.gateway.emit(EVENT_LOG, {"msg": f"cancel_order 参数错: {vt_orderid}"})

    def query_account(self) -> None:
        if not self.login_status:
            return
        self.reqid += 1
        self.reqQryTradingAccount({}, self.reqid)

    def query_position(self) -> None:
        if not self.login_status:
            return
        self.reqid += 1
        self.reqQryInvestorPosition({}, self.reqid)

    def onFrontConnected(self) -> None:
        self.gateway.emit(EVENT_LOG, {"msg": "TD 前置连接成功，开始认证"})
        self.reqid += 1
        self.reqAuthenticate({
            "UserID": self.gateway.setting["userid"],
            "BrokerID": self.gateway.setting["brokerid"],
            "AppID": self.gateway.setting["appid"],
            "AuthCode": self.gateway.setting["auth_code"],
        }, self.reqid)

    def onFrontDisconnected(self, reason: int) -> None:
        self.gateway.emit(EVENT_LOG, {"msg": f"TD 前置断开，原因 {reason}（CTP 自动重连）"})
        self.login_status = False
        self.settlement_confirmed = False

    def onRspAuthenticate(self, data, error, reqid, last) -> None:
        if not error["ErrorID"]:
            self.gateway.emit(EVENT_LOG, {"msg": "TD 认证成功，开始登录"})
            self.reqid += 1
            self.reqUserLogin({
                "UserID": self.gateway.setting["userid"],
                "Password": self.gateway.setting["password"],
                "BrokerID": self.gateway.setting["brokerid"],
            }, self.reqid)
        else:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"TD 认证失败 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRspUserLogin(self, data, error, reqid, last) -> None:
        if not error["ErrorID"]:
            self.login_status = True
            self.front_id = data["FrontID"]
            self.session_id = data["SessionID"]
            self.gateway.emit(EVENT_LOG, {
                "msg": f"TD 登录成功 FrontID={self.front_id} SessionID={self.session_id}"
            })
            self.reqid += 1
            self.reqSettlementInfoConfirm({
                "BrokerID": self.gateway.setting["brokerid"],
                "InvestorID": self.gateway.setting["userid"],
            }, self.reqid)
            # SimNow 仿真环境经常不回结算单确认；乐观置 True，等回调真回来再二次确认
            self.settlement_confirmed = True
        else:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"TD 登录失败 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRspSettlementInfoConfirm(self, data, error, reqid, last) -> None:
        if not error["ErrorID"]:
            # 已是 True（乐观），二次确认只是为了打日志
            self.settlement_confirmed = True
            self.gateway.emit(EVENT_LOG, {"msg": "TD 结算单确认，通道就绪"})
        else:
            self.gateway.emit(EVENT_LOG, {
                "msg": f"!! TD 结算单确认失败: {error['ErrorID']} {error['ErrorMsg']}"
            })

    def onRspOrderInsert(self, data, error, reqid, last) -> None:
        if error and error["ErrorID"]:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"!! 委托被拒 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRtnOrder(self, data) -> None:
        self.gateway.emit(EVENT_ORDER, data)

    def onRtnTrade(self, data) -> None:
        self.gateway.emit(EVENT_TRADE, data)

    def onRspQryTradingAccount(self, data, error, reqid, last) -> None:
        if data:
            self.gateway.emit(EVENT_ACCOUNT, data)

    def onRspQryInvestorPosition(self, data, error, reqid, last) -> None:
        if data:
            self.gateway.emit(EVENT_POSITION, data)


class _MdApi(MdApi):
    def __init__(self, gateway: CtpGateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.reqid = 0
        self.login_status = False
        self.subscribed: list[str] = []

    def connect(self) -> None:
        try:
            self.createFtdcMdApi(f"{FLOW_PATH}/Md", True)
        except TypeError:
            self.createFtdcMdApi(f"{FLOW_PATH}/Md")
        self.registerFront(self.gateway.setting["md_address"])
        self.init()

    def onFrontConnected(self) -> None:
        self.gateway.emit(EVENT_LOG, {"msg": "MD 前置连接成功，开始登录"})
        self.reqid += 1
        self.reqUserLogin({
            "UserID": self.gateway.setting["userid"],
            "Password": self.gateway.setting["password"],
            "BrokerID": self.gateway.setting["brokerid"],
        }, self.reqid)

    def onFrontDisconnected(self, reason: int) -> None:
        self.login_status = False
        self.gateway.emit(EVENT_LOG, {"msg": f"MD 前置断开，原因 {reason}（CTP 自动重连）"})

    def onRspUserLogin(self, data, error, reqid, last) -> None:
        if not error["ErrorID"]:
            self.login_status = True
            self.gateway.emit(EVENT_LOG, {"msg": f"MD 登录成功，订阅 {self.subscribed}"})
            for symbol in self.subscribed:
                self.subscribeMarketData(symbol)
        else:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"MD 登录失败 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRtnDepthMarketData(self, data) -> None:
        self.gateway.emit(EVENT_TICK, data)