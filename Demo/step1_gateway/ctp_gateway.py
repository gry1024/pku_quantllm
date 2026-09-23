"""Step 1: 把 TdApi + MdApi 包成一个 CtpGateway。

对外只暴露 5 个动作：connect / close / subscribe / send_order / cancel_order / query_*
回调不再 print，直接 emit 到 event_engine。

这是 learn.md Step 1 的产物 —— Step 2 会在外面再包一层真·EventEngine。
"""

from pathlib import Path

from vnpy_ctp.api import MdApi, TdApi

from event_engine import (
    EventEngine, Event,
    EVENT_TICK, EVENT_ORDER, EVENT_TRADE,
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG,
)


# SimNow 私有仿真账号（仅本人 276266）
SETTING = {
    "userid":     "276266",
    "password":   "@Abc303802",
    "brokerid":   "9999",
    "td_address": "tcp://182.254.243.31:30001",
    "md_address": "tcp://182.254.243.31:30011",
    "appid":      "simnow_client_test",
    "auth_code":  "0000000000000000",
}

# 流文件落盘目录：复用 Demo/flow/，分 Td/Md 两个子目录
FLOW_PATH = str(Path(__file__).parent.parent / "flow")


class CtpGateway:
    """对上层只暴露 5 个动作。"""

    def __init__(self, event_engine: EventEngine, setting: dict) -> None:
        self.event_engine = event_engine
        self.setting = setting
        self.td_api = _TdApi(self)
        self.md_api = _MdApi(self)
        self._connected = False

    # ============== 上层 API ==============
    def connect(self) -> None:
        """发起交易 + 行情前置连接（非阻塞）。"""
        if self._connected:
            return
        self.td_api.connect()
        self.md_api.connect()
        self._connected = True

    def close(self) -> None:
        """关闭交易 + 行情前置。"""
        self.td_api.exit()
        self.md_api.exit()

    def subscribe(self, symbols: list[str]) -> None:
        """登记要订阅的合约；实际订阅在 MdApi 登录成功后执行。"""
        self.md_api.subscribed.extend(symbols)

    def send_order(self, instrument: str, exchange: str, direction: str,
                   offset: str, price: float, volume: int) -> str:
        """发出限价单。返回 OrderRef（字符串）；返回 "" 表示未就绪。"""
        return self.td_api.send_order(instrument, exchange, direction, offset,
                                      price, volume)

    def cancel_order(self, vt_orderid: str) -> None:
        """撤单。vt_orderid 格式 "FrontID_SessionID_OrderRef"。"""
        self.td_api.cancel_order(vt_orderid)

    def query_account(self) -> None:
        self.td_api.query_account()

    def query_position(self) -> None:
        self.td_api.query_position()

    # ============== 内部 emit ==============
    def emit(self, type_: str, data: dict) -> None:
        """把事件塞给 event_engine（TdApi / MdApi 回调里调用）。"""
        self.event_engine.put(Event(type_, data))


# ============== 内部 TdApi ==============
class _TdApi(TdApi):
    """原 SimpleTdApi 改造版：print → emit。"""

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
        """注册前置 + init（vnpy_ctp 6.6.9.1 在 Linux 是 2 参，Windows 是 1 参）。"""
        try:
            self.createFtdcTraderApi(f"{FLOW_PATH}/Td", True)
        except TypeError:
            self.createFtdcTraderApi(f"{FLOW_PATH}/Td")
        self.registerFront(self.gateway.setting["td_address"])
        self.init()

    # ---------- 下单 / 撤单 ----------
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
            "OrderPriceType": "2",        # 限价
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
                "ActionFlag": "0",          # 撤单
                "BrokerID": self.gateway.setting["brokerid"],
                "InvestorID": self.gateway.setting["userid"],
                "UserID": self.gateway.setting["userid"],
                # 下面两个字段本应填，但 Step 1 简化版不做
                "InstrumentID": "",
                "ExchangeID": "",
            }
            self.reqid += 1
            self.reqOrderAction(req, self.reqid)
        except ValueError:
            self.gateway.emit(EVENT_LOG, {"msg": f"cancel_order 参数错: {vt_orderid}"})

    # ---------- 查询 ----------
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

    # ============== 回调 ==============
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
        else:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"TD 登录失败 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRspSettlementInfoConfirm(self, data, error, reqid, last) -> None:
        if not error["ErrorID"]:
            self.settlement_confirmed = True
            self.gateway.emit(EVENT_LOG, {"msg": "TD 结算单确认，通道就绪"})

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


# ============== 内部 MdApi ==============
class _MdApi(MdApi):
    """原 SimpleMdApi 改造版。"""

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
            self.gateway.emit(EVENT_LOG, {
                "msg": f"MD 登录成功，订阅 {self.subscribed}"
            })
            for symbol in self.subscribed:
                self.subscribeMarketData(symbol)
        else:
            self.gateway.emit(EVENT_LOG,
                              {"msg": f"MD 登录失败 {error['ErrorID']} {error['ErrorMsg']}"})

    def onRtnDepthMarketData(self, data) -> None:
        self.gateway.emit(EVENT_TICK, data)