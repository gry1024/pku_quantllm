"""
CTP 交易 DEMO 2：突破价格触发下单（行情 -> 决策 -> 下单 -> 回报 完整闭环）。

在 demo1（连接/登录/订阅/打印行情）基础上扩展：
1. 对接交易下单：TdApi 走完下单前置流程（认证 -> 登录 -> 确认结算单）
2. 实现策略逻辑：行情价格向上突破阈值时，自动发出限价开多单（只发一单）
3. 打印全部交易回报：onRtnOrder（委托状态流）/ onRspOrderInsert（场内拒单）/ onRtnTrade（成交）

用法::

    cp .env.example .env    # 第一次跑前；填入 SIMNOW 凭证
    python demo.py          # 策略参数直接改下方常量

凭证全部走环境变量：``CTP_USERID`` / ``CTP_PASSWORD`` / ``CTP_BROKERID`` /
``CTP_TD_ADDRESS`` / ``CTP_MD_ADDRESS`` / ``CTP_APPID`` / ``CTP_AUTH_CODE``。
本文件不持有任何默认账号，避免共享账号泄漏到 git 历史。
"""

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import sleep

from vnpy_ctp.api import MdApi, TdApi


# ---------------------------------------------------------------------------
# 凭证加载：仅从环境变量（或 .env 文件）读取，绝不硬编码默认值
# ---------------------------------------------------------------------------
def _load_setting() -> dict[str, str]:
    """从环境变量组装 SIMNOW 连接参数。缺失则报错，不静默回落到默认账号。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=False)
    except ImportError:
        pass

    required: list[tuple[str, str]] = [
        ("userid",     "CTP_USERID"),
        ("password",   "CTP_PASSWORD"),
        ("brokerid",   "CTP_BROKERID"),
        ("td_address", "CTP_TD_ADDRESS"),
        ("md_address", "CTP_MD_ADDRESS"),
        ("appid",      "CTP_APPID"),
        ("auth_code",  "CTP_AUTH_CODE"),
    ]
    cfg: dict[str, str] = {}
    missing: list[str] = []
    for key, env_var in required:
        value: str | None = os.environ.get(env_var)
        if not value:
            missing.append(env_var)
        else:
            cfg[key] = value
    if missing:
        raise RuntimeError(
            f"缺少 SIMNOW 环境变量: {', '.join(missing)}。"
            f"先 `cp .env.example .env` 并填入凭证（或直接 export 后再启动）。"
        )
    return cfg


SETTING: dict[str, str] = _load_setting()

# ---------------- 策略配置（验证下单链路时可把 TRIGGER_OFFSET 设为负值立即触发） ----------------
SYMBOL =           "au2612"    # 交易合约（须为当前挂牌月份）
SYMBOL_EXCHANGE =  "SHFE"      # 交易所（demo 硬编码；正式做法用 reqQryInstrument 查询）
VOLUME =           1           # 下单手数
DIRECTION_UP =     True        # True: 向上突破开多；False: 向下跌破开空
TRIGGER_OFFSET =   0.1         # 触发阈值 = 首个 tick 最新价 + 该偏移
PRICE_OFFSET =     1.0         # 委托价 = 触发时最新价 + 该偏移（追价保证成交）

# 流文件目录（CTP 底层要求一个可写的本地目录存放流文件）
FLOW_PATH = Path(__file__).parent / "flow"
FLOW_PATH.mkdir(exist_ok=True)

# 委托状态码映射（CTP OrderStatus -> 中文说明）
STATUS_MAP = {
    "0": "全部成交",            # THOST_FTDC_OST_AllTraded
    "1": "部分成交还在队列中",       # THOST_FTDC_OST_PartTradedQueueing
    "2": "部分成交不在队列中",       # THOST_FTDC_OST_PartTradedNotQueueing
    "3": "未成交还在队列中",        # THOST_FTDC_OST_NoTradeQueueing
    "4": "未成交不在队列中",        # THOST_FTDC_OST_NoTradeNotQueueing
    "5": "已撤销",             # THOST_FTDC_OST_Canceled
    "a": "未知(已提交)",         # THOST_FTDC_OST_Unknown
    "b": "未知(待触发)",         # THOST_FTDC_OST_Touched
}


def now() -> str:
    """返回当前时间字符串（精确到毫秒），用作日志前缀。"""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    """带时间戳打印一行日志。"""
    print(f"[{now()}] {msg}", flush=True)


def make_login_req() -> dict:
    """构造登录请求体（两个 API 的登录请求体完全一样，共用一份）。"""
    return {
        "UserID": SETTING["userid"],
        "Password": SETTING["password"],
        "BrokerID": SETTING["brokerid"],
    }


class SimpleTdApi(TdApi):
    """交易 API：认证 -> 登录 -> 确认结算单 -> 等待策略发单。"""

    def __init__(self) -> None:
        """初始化请求/委托计数、会话信息与通道就绪状态。"""
        super().__init__()                                 # 必须调用：C++ 扩展对象初始化
        self.reqid: int = 0                                # 请求编号，每次请求自增
        self.order_ref: int = 0                            # 本会话委托编号，自增
        self.frontid: int = 0                              # 前置编号（登录回报中取得）
        self.sessionid: int = 0                            # 会话编号（登录回报中取得，重连后变化）
        self.connect_status: bool = False                  # 是否已发起过连接
        self.login_status: bool = False                    # 是否登录成功
        self.settlement_confirmed: bool = False            # 是否已确认结算单（下单前置条件）
        self.ready_hook: Callable[[], None] | None = None  # 通道就绪回调（主函数注入策略重试）

    def connect(self) -> None:
        """发起网络连接（非阻塞，结果在 onFrontConnected 回调返回）。"""
        if not self.connect_status:
            self.createFtdcTraderApi(str(FLOW_PATH / "Td").encode("GBK"), True)
            self.subscribePrivateTopic(0)   # 订阅私有流（委托/成交推送到本会话）
            self.subscribePublicTopic(0)    # 订阅公有流（合约等公告信息）
            self.registerFront(SETTING["td_address"])
            self.init()
            self.connect_status = True

    def authenticate(self) -> None:
        """发起终端认证（SimNow 要求，需 AppID + AuthCode）。"""
        req = {
            "UserID": SETTING["userid"],
            "BrokerID": SETTING["brokerid"],
            "AppID": SETTING["appid"],
            "AuthCode": SETTING["auth_code"],
        }
        self.reqid += 1
        self.reqAuthenticate(req, self.reqid)

    def login(self) -> None:
        """发起登录请求。"""
        self.reqid += 1
        self.reqUserLogin(make_login_req(), self.reqid)

    def confirm_settlement(self) -> None:
        """确认结算单（CTP 下单的前置条件，未确认前 reqOrderInsert 会被拒绝）。"""
        req = {
            "BrokerID": SETTING["brokerid"],
            "InvestorID": SETTING["userid"],
        }
        self.reqid += 1
        self.reqSettlementInfoConfirm(req, self.reqid)

    def send_order(self, direction: str, offset: str, price: float, volume: int) -> None:
        """
        发出限价单。

        direction: '0'买 / '1'卖；offset: '0'开 / '1'平（THOST_FTDC 常量值）。
        必填字段模板与 CtpGateway.send_order 一致。
        """
        self.order_ref += 1

        req = {
            "InstrumentID": SYMBOL,
            "ExchangeID": SYMBOL_EXCHANGE,
            "LimitPrice": price,
            "VolumeTotalOriginal": volume,
            "OrderPriceType": "2",        # 限价 THOST_FTDC_OPT_LimitPrice
            "Direction": direction,       # '0'买 '1'卖
            "CombOffsetFlag": offset,     # '0'开 '1'平
            "OrderRef": str(self.order_ref),
            "InvestorID": SETTING["userid"],
            "UserID": SETTING["userid"],
            "BrokerID": SETTING["brokerid"],
            "CombHedgeFlag": "1",         # 投机 THOST_FTDC_HF_Speculation
            "ContingentCondition": "1",   # 立即触发 THOST_FTDC_CC_Immediately
            "ForceCloseReason": "0",      # 非强平 THOST_FTDC_FCC_NotForceClose
            "IsAutoSuspend": 0,
            "TimeCondition": "3",         # 当日有效 THOST_FTDC_TC_GFD
            "VolumeCondition": "1",       # 任何数量 THOST_FTDC_VC_AV
            "MinVolume": 1,
        }

        self.reqid += 1
        n = self.reqOrderInsert(req, self.reqid)
        if n != 0:
            log(f"委托请求发送失败（柜台流控/字段错误），返回码 {n}")
        else:
            log(f">> 已发出委托：{'买入' if direction == '0' else '卖出'}{'开仓' if offset == '0' else '平仓'}"
                f" {SYMBOL} {volume}手 @ {price}  OrderRef={self.order_ref}")

    def onFrontConnected(self) -> None:
        """回报：前置连接成功，接着走认证。"""
        log("交易前置连接成功，开始终端认证")
        self.authenticate()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开。"""
        self.login_status = False
        self.settlement_confirmed = False
        log(f"交易前置连接断开，原因码 {reason}")

    def onRspAuthenticate(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：终端认证结果，成功则接着登录。"""
        if not error["ErrorID"]:
            log("终端认证成功，开始登录")
            self.login()
        else:
            log(f"终端认证失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：登录成功后记录会话信息，接着确认结算单。"""
        if not error["ErrorID"]:
            self.login_status = True
            self.frontid = data["FrontID"]
            self.sessionid = data["SessionID"]
            log(f"交易账号登录成功（FrontID={self.frontid} SessionID={self.sessionid}），确认结算单")
            self.confirm_settlement()
        else:
            log(f"交易登录失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspSettlementInfoConfirm(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：结算单确认成功即交易通道就绪，触发 ready_hook 让策略补触发。"""
        if not error["ErrorID"]:
            self.settlement_confirmed = True
            log("结算单确认成功，交易通道就绪（等待策略触发）")
            if self.ready_hook:
                self.ready_hook()
        else:
            log(f"结算单确认失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspOrderInsert(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：场内拒单（资金不足/合约不存在/字段非法等）。"""
        log(f"!! 委托被柜台拒绝：{error['ErrorID']} {error['ErrorMsg']}")

    def onRtnOrder(self, data: dict) -> None:
        """
        委托状态流推送：每笔委托状态变化都会推一条。

        注意：这里收到的是【整个账户】的所有委托（包括其他程序下的单），
        demo 中只打印本策略合约的委托。
        """
        if data["InstrumentID"] != SYMBOL:
            return
        orderid = f"{data['FrontID']}_{data['SessionID']}_{data['OrderRef']}"
        status = STATUS_MAP.get(data["OrderStatus"], data["OrderStatus"])
        log(f"[委托] {orderid} {data['InstrumentID']} "
            f"{'买' if data['Direction'] == '0' else '卖'}{data['VolumeTotalOriginal']}手 "
            f"已成交{data['VolumeTraded']}手 状态={status} ({data['StatusMsg']})")

    def onRtnTrade(self, data: dict) -> None:
        """成交回报：一笔委托可能多次成交（同样只打印本策略合约）。"""
        if data["InstrumentID"] != SYMBOL:
            return
        log(f"[成交] {data['InstrumentID']} {'买' if data['Direction'] == '0' else '卖'}"
            f"{data['Volume']}手 @ {data['Price']}  TradeID={data['TradeID']}")


class StrategyMdApi(MdApi):
    """行情 API + 突破策略状态机：WAITING -> TRIGGERED（只发一单）。"""

    def __init__(self, td_api: SimpleTdApi) -> None:
        """初始化行情连接状态与突破策略状态机。"""
        super().__init__()                 # 必须调用：C++ 扩展对象初始化
        self.td_api = td_api               # 交易 API 引用（触发时下单）
        self.reqid: int = 0                # 请求编号，每次请求自增
        self.connect_status: bool = False  # 是否已发起过连接
        self.login_status: bool = False    # 是否登录成功
        self.state: str = "WAITING"        # 策略状态：WAITING / TRIGGERED
        self.threshold: float = 0.0        # 触发阈值（首个 tick 到达后设定）
        self.last_price: float = 0.0       # 最新成交价
        self.wait_logged: bool = False     # "等待通道"提示只打一次

    def connect(self) -> None:
        """发起网络连接（非阻塞，结果在 onFrontConnected 回调返回）。"""
        if not self.connect_status:
            self.createFtdcMdApi(str(FLOW_PATH / "Md").encode("GBK"), True)
            self.registerFront(SETTING["md_address"])
            self.init()
            self.connect_status = True

    def login(self) -> None:
        """发起登录请求。"""
        self.reqid += 1
        self.reqUserLogin(make_login_req(), self.reqid)

    def onFrontConnected(self) -> None:
        """回报：前置连接成功，接着登录。"""
        log("行情前置连接成功，开始登录")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开。"""
        self.login_status = False
        log(f"行情前置连接断开，原因码 {reason}")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：登录成功后订阅目标合约。"""
        if not error["ErrorID"]:
            self.login_status = True
            log(f"行情账号登录成功，订阅 {SYMBOL}")
            self.subscribeMarketData(SYMBOL)
        else:
            log(f"行情登录失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRtnDepthMarketData(self, data: dict) -> None:
        """行情推送 = 策略主逻辑入口：记录最新价并检查突破。"""
        if data["InstrumentID"] != SYMBOL:
            return

        self.last_price = data["LastPrice"]

        if not self.threshold:
            self.threshold = self.last_price + (TRIGGER_OFFSET if DIRECTION_UP else -TRIGGER_OFFSET)
            log(f"策略就绪：当前价 {self.last_price}，触发阈值设为 {self.threshold}")

        self.try_trigger()

    def try_trigger(self) -> None:
        """突破检查：行情 tick 与 交易通道就绪（ready_hook）两条路径都会调用。"""
        if self.state != "WAITING" or not self.last_price:
            return

        last_price = self.last_price
        triggered = (
            (DIRECTION_UP and last_price >= self.threshold)
            or (not DIRECTION_UP and last_price <= self.threshold)
        )
        if not triggered:
            return

        # 交易通道就绪才能下单；未就绪时等待（就绪回调/后续 tick 会重试）
        if not (self.td_api.login_status and self.td_api.settlement_confirmed):
            if not self.wait_logged:  # 只提示一次，避免逐 tick 刷屏
                self.wait_logged = True
                log(f"!! 价格已突破（{last_price} {'>=' if DIRECTION_UP else '<='} {self.threshold}），"
                    f"但交易通道未就绪，等待就绪后自动下单")
            return

        self.wait_logged = False
        self.state = "TRIGGERED"
        log(f"!! 价格突破触发：{last_price} {'>=' if DIRECTION_UP else '<='} {self.threshold}")

        # 限价追价委托：多头用 价+偏移，空头用 价-偏移
        if DIRECTION_UP:
            price = last_price + PRICE_OFFSET
            self.td_api.send_order("0", "0", price, VOLUME)   # 买开
        else:
            price = last_price - PRICE_OFFSET
            self.td_api.send_order("1", "0", price, VOLUME)   # 卖开


def main() -> None:
    """主流程：连接交易/行情前置，等待策略触发与交易回报。"""
    td_api = SimpleTdApi()
    md_api = StrategyMdApi(td_api)
    td_api.ready_hook = md_api.try_trigger

    log("发起交易前置连接...")
    td_api.connect()
    log("发起行情前置连接...")
    md_api.connect()

    log("主线程进入等待，Ctrl+C 退出")
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        log("退出")
        td_api.exit()
        md_api.exit()


if __name__ == "__main__":
    main()
