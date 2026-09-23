"""最小化 CTP 交易 DEMO：从底层 Python 交易 API 开始（vnpy_ctp.api，非 CtpGateway）。

参考：`projects/ctp_demo/demo.py`（已经验证能连 SimNow）。
本 demo 主要是把连接参数与代码组织对齐到项目里的已知能跑版本：
  - 端点：182.254.243.31:30001 / 30011（SimNow 第一套"实盘"环境）
  - 公开测试账号：276266 / @Abc303802 （我的唯一仿真账号，禁止用其他账号！）
  - 终端认证：simnow_client_test / 0000000000000000
  - 必须建流文件目录 flow/

流程：
1. 连接交易前置（TdApi）：发起网络连接 -> 终端认证 -> 登录账号
2. 连接行情前置（MdApi）：发起网络连接 -> 登录账号
3. 订阅合约行情（subscribeMarketData）
4. 打印每一条行情推送（onRtnDepthMarketData）

CTP 是纯异步回调驱动的 API：所有 req 只负责发出请求，
结果通过对应的 onRsp/onRtn 回调（CTP 内部线程）返回。

用法：python demo.py
"""

from datetime import datetime
from pathlib import Path
from time import monotonic as _monotonic, sleep

from vnpy_ctp.api import MdApi, TdApi


# SimNow 模拟账号配置（7×24 仿真环境，全天候可连；公开测试账号对所有 IP 开放）
SETTING = {
    "userid":     "276266",                      # 私有账号（来自 SIMNOW模拟平台账户密码.txt）
    "password":   "@Abc303802",                  # 私有密码
    "brokerid":   "9999",                        # 经纪商代码（SimNow 固定 9999）
    "td_address": "tcp://182.254.243.31:30001",  # 实盘环境：td=30001（仅交易日交易时段）
    "md_address": "tcp://182.254.243.31:30011",  # 实盘环境：md=30011
    "appid":      "simnow_client_test",          # 产品名称（终端认证用）
    "auth_code":  "0000000000000000",            # 授权编码（终端认证用）
}
# 收盘后切换到 7×24 仿真环境（私有账号默认未开通 7×24，需去 simnow.com.cn 单独申请）：
#   "td_address": "tcp://182.254.243.31:40001",
#   "md_address": "tcp://182.254.243.31:40011",
#
# 调试时临时切到公开测试账号（任意 IP、不限时段）：
#   "userid":   "000300",
#   "password": "Vnpy@123456789",
# 其他端点：
#   - 第一套"实盘"环境：td=30001 / md=30011，仅交易日交易时段可用
#   - 用私有账号（如 276266）：需去 simnow.com.cn 申请 IP 白名单，否则 ErrorID=3

# 是否开启终端认证（SimNow Q&A：默认开启，程序化用户可选择不开）
#   True  = 走 SimNow 默认（必须 appid/auth_code 正确）
#   False = 跳过认证直接登录（对未开启认证的环境适用）
USE_AUTHENTICATION: bool = True

# 订阅的合约列表（须为当前挂牌月份；摘牌合约订阅无任何错误回报，只是收不到行情）
SUBSCRIBED_SYMBOLS = ["au2612"]

# 流文件目录（CTP 底层要求一个可写的本地目录存放流文件）
FLOW_PATH = Path(__file__).parent / "flow"
FLOW_PATH.mkdir(exist_ok=True)


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
    """交易 API：演示 连接 -> 认证 -> 登录 -> 确认结算单 -> 下单/撤单/查持仓/资金 完整握手状态机。"""

    # ---- CTP 委托状态码（THOST_FTDC_OST_*） -> 中文 ----
    ORDER_STATUS_MAP = {
        "0": "全部成交",
        "1": "部分成交还在队列中",
        "2": "部分成交不在队列中",
        "3": "未成交还在队列中",
        "4": "未成交不在队列中",
        "5": "已撤销",
        "a": "未知(已提交)",
        "b": "未知(待触发)",
    }

    def __init__(self) -> None:
        """初始化请求/委托计数与连接、登录、结算、就绪状态。"""
        super().__init__()                         # 必须调用：C++ 扩展对象初始化
        self.reqid: int = 0                        # 请求编号（每次请求自增）
        self.order_ref: int = 0                    # 本会话委托编号（每次下单自增）
        self.connect_status: bool = False          # 是否已发起过连接（防重复）
        self.login_status: bool = False            # 是否登录成功
        self.settlement_confirmed: bool = False    # 是否确认结算单（下单前置条件）
        self._last_tick: dict[str, float] = {}     # 合约 -> 最新价（撤单时计算超价用）

    # ============== 连接 ==============
    def connect(self) -> None:
        """发起网络连接（非阻塞，结果在 onFrontConnected 回调返回）。"""
        if self.connect_status:
            return
        # PyPI vnpy_ctp 6.6.9.1 在不同平台的 pybind11 绑定里签名不一样：
        #   - Linux:   createFtdcTraderApi(path: str, use_resume: bool)
        #   - Windows: createFtdcTraderApi(path: str)        # 简化的单参数版
        # 兼容两种签名；第二个 bool 参数含义是"是否使用 UDP 私有流恢复模式"。
        flow_path = str(FLOW_PATH / "Td")
        try:
            self.createFtdcTraderApi(flow_path, True)
        except TypeError:
            self.createFtdcTraderApi(flow_path)
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
        """确认结算单（CTP 下单的前置条件，未确认前 reqOrderInsert 会被拒）。"""
        req = {
            "BrokerID": SETTING["brokerid"],
            "InvestorID": SETTING["userid"],
        }
        self.reqid += 1
        self.reqSettlementInfoConfirm(req, self.reqid)

    # ============== 下单 / 撤单 / 查 ==============
    def send_order(
        self,
        instrument: str,
        exchange: str,
        direction: str,    # '0' 买 / '1' 卖
        offset: str,       # '0' 开 / '1' 平 / '3' 平今 / '4' 平昨
        price: float,
        volume: int,
    ) -> int:
        """发出限价单。返回 OrderRef（用于后续撤单）；返回 -1 表示未就绪/参数错。"""
        if not (self.login_status and self.settlement_confirmed):
            log("!! 通道未就绪（下单需登录 + 结算单确认），忽略本次下单")
            return -1

        self.order_ref += 1
        req = {
            "InstrumentID": instrument,
            "ExchangeID": exchange,
            "LimitPrice": price,
            "VolumeTotalOriginal": volume,
            "OrderPriceType": "2",          # 限价 THOST_FTDC_OPT_LimitPrice
            "Direction": direction,         # '0' 买 '1' 卖
            "CombOffsetFlag": offset,       # '0' 开 '1' 平 '3' 平今 '4' 平昨
            "OrderRef": str(self.order_ref),
            "InvestorID": SETTING["userid"],
            "UserID": SETTING["userid"],
            "BrokerID": SETTING["brokerid"],
            "CombHedgeFlag": "1",           # 投机
            "ContingentCondition": "1",     # 立即触发
            "ForceCloseReason": "0",        # 非强平
            "IsAutoSuspend": 0,
            "TimeCondition": "3",           # 当日有效
            "VolumeCondition": "1",         # 任何数量
            "MinVolume": 1,
        }
        self.reqid += 1
        ret = self.reqOrderInsert(req, self.reqid)
        dir_cn = "买" if direction == "0" else "卖"
        off_cn = {"0": "开", "1": "平", "3": "平今", "4": "平昨"}.get(offset, offset)
        if ret != 0:
            log(f"!! 委托发送失败（柜台流控/字段错误），返回码 {ret}")
            return -1
        log(f">> 已发委托：{dir_cn}{off_cn} {instrument} {volume}手 @ {price} "
            f"OrderRef={self.order_ref}")
        return self.order_ref

    def cancel_order(
        self,
        instrument: str,
        exchange: str,
        order_ref: str,
        front_id: int = 0,
        session_id: int = 0,
    ) -> None:
        """撤单。

        front_id / session_id 可从 onRtnOrder 里取；不传则用最新登录回报里的（适用本会话内的单）。
        """
        if not (self.login_status and self.settlement_confirmed):
            log("!! 通道未就绪，忽略撤单")
            return
        req = {
            "InstrumentID": instrument,
            "ExchangeID": exchange,
            "OrderRef": str(order_ref),
            "FrontID": front_id,
            "SessionID": session_id,
            "ActionFlag": "0",              # THOST_FTDC_AF_Delete
            "BrokerID": SETTING["brokerid"],
            "InvestorID": SETTING["userid"],
            "UserID": SETTING["userid"],
        }
        self.reqid += 1
        ret = self.reqOrderAction(req, self.reqid)
        if ret != 0:
            log(f"!! 撤单发送失败，返回码 {ret}")

    def query_account(self) -> None:
        """查资金。"""
        if not self.login_status:
            return
        self.reqid += 1
        self.reqQryTradingAccount({}, self.reqid)

    def query_position(self) -> None:
        """查持仓。"""
        if not self.login_status:
            return
        self.reqid += 1
        self.reqQryInvestorPosition({}, self.reqid)

    # ============== 行情注入（撤单超价用） ==============
    def feed_tick(self, instrument: str, last_price: float) -> None:
        """外部把收到的最新价喂给 TdApi，撤单时用于计算超价。"""
        self._last_tick[instrument] = last_price

    # ============== 回调 ==============
    def onFrontConnected(self) -> None:
        """回报：前置连接成功，按 USE_AUTHENTICATION 决定走认证还是直接登录。"""
        if USE_AUTHENTICATION:
            log("交易前置连接成功，开始终端认证")
            self.authenticate()
        else:
            log("交易前置连接成功，跳过终端认证直接登录")
            self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开 —— 关键状态全部复位，等待 onFrontConnected 自动重连。"""
        log(f"!! 交易前置连接断开，原因码 {reason}（CTP 会自动重连）")
        self.login_status = False
        self.settlement_confirmed = False

    def onRspAuthenticate(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：终端认证结果，成功则接着登录。"""
        if not error["ErrorID"]:
            log("终端认证成功，开始登录")
            self.login()
        else:
            log(f"终端认证失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：登录成功后立即确认结算单（必须！否则 reqOrderInsert 会被拒）。"""
        if not error["ErrorID"]:
            self.login_status = True
            self.front_id: int = data["FrontID"]
            self.session_id: int = data["SessionID"]
            log(f"交易账号登录成功（FrontID={self.front_id} SessionID={self.session_id}），"
                f"确认结算单")
            self.confirm_settlement()
        else:
            self.login_status = False
            err_id = error["ErrorID"]
            err_msg = error["ErrorMsg"]
            log(f"!! 交易登录失败：{err_id} {err_msg}")
            # ErrorID 常见含义：
            #   3  = 不合法的登录（密码错 / IP 不在白名单 / 账号被锁）
            #   7  = 未授权（终端认证未通过 / 账号不在该 broker 下）
            #   21 = 连接数量超限（SimNow 每账号最多 4 个 session）
            log("   常见原因：① 密码错 ② 当前 IP 不在 SimNow 白名单"
                " ③ 该账号已被 4 个 session 占满"
                " ④ appid/auth_code 与该 broker 不匹配")

    def onRspSettlementInfoConfirm(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：结算单确认 —— 此时交易通道完全就绪。"""
        if not error["ErrorID"]:
            self.settlement_confirmed = True
            log("结算单确认成功，交易通道就绪")
        else:
            log(f"结算单确认失败：{error['ErrorID']} {error['ErrorMsg']}")

    # ----- 委托 / 成交回报 -----
    def onRspOrderInsert(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：场内拒单（资金不足/合约不存在/字段非法等）。"""
        log(f"!! 委托被柜台拒绝：{error['ErrorID']} {error['ErrorMsg']} "
            f"ref={data.get('OrderRef')}")

    def onRtnOrder(self, data: dict) -> None:
        """委托状态流推送：每笔委托状态变化都会推一条。

        注意：会收到**整个账户**的所有委托（包括其他程序下的单）。
        """
        # 只打印 demo 关心的合约，过滤噪音
        if data["InstrumentID"] != SUBSCRIBED_SYMBOLS[0]:
            return
        orderid = f"{data['FrontID']}_{data['SessionID']}_{data['OrderRef']}"
        status = self.ORDER_STATUS_MAP.get(data["OrderStatus"], data["OrderStatus"])
        log(f"[委托] {orderid} {data['InstrumentID']} "
            f"{'买' if data['Direction'] == '0' else '卖'}{data['VolumeTotalOriginal']}手 "
            f"已成交{data['VolumeTraded']}手 状态={status} ({data['StatusMsg']})")

    def onRtnTrade(self, data: dict) -> None:
        """成交回报：一笔委托可能多次成交。"""
        if data["InstrumentID"] != SUBSCRIBED_SYMBOLS[0]:
            return
        log(f"[成交] {data['InstrumentID']} "
            f"{'买' if data['Direction'] == '0' else '卖'}"
            f"{data['Volume']}手 @ {data['Price']}  TradeID={data['TradeID']}")

    # ----- 查资金 / 持仓回报 -----
    def onRspQryTradingAccount(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """查资金回报。last=True 表示分页结束。"""
        if error and error["ErrorID"]:
            log(f"!! 查资金失败：{error['ErrorID']} {error['ErrorMsg']}")
            return
        if data:
            log(f"[资金] 可用={data.get('Available', 0):.2f}  "
                f"余额={data.get('Balance', 0):.2f}  "
                f"冻结={data.get('FrozenMargin', 0):.2f}  "
                f"持仓盈亏={data.get('PositionProfit', 0):.2f}  "
                f"手续费={data.get('Commission', 0):.4f}  "
                f"风险度={data.get('RiskRatio', 0):.2%}")

    def onRspQryInvestorPosition(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """查持仓回报。data 里 Position > 0 才打印（避免冻结/今日平仓挂出 0 持仓噪音）。"""
        if error and error["ErrorID"]:
            log(f"!! 查持仓失败：{error['ErrorID']} {error['ErrorMsg']}")
            return
        if data and data.get("Position", 0) > 0:
            log(f"[持仓] {data['InstrumentID']} "
                f"{'多' if data['PosiDirection'] == '2' else '空' if data['PosiDirection'] == '3' else '净'} "
                f"持仓={data['Position']}手  均价={data['PositionCost']/max(data['Position'],1):.2f}  "
                f"今开={data['TodayPosition']}  昨开={data['YdPosition']}  "
                f"盈亏={data['PositionProfit']:.2f}")


class SimpleMdApi(MdApi):
    """行情 API：连接 -> 登录 -> 订阅 -> 打印推送。

    同时通过 `td_api_ref` 把最新价喂给交易 API（撤单超价用）。
    """

    def __init__(self, td_api: "SimpleTdApi | None" = None) -> None:
        """初始化请求计数、连接/登录状态与订阅列表、关联的 TdApi。"""
        super().__init__()                 # 必须调用：C++ 扩展对象初始化
        self.reqid: int = 0                # 请求编号，每次请求自增
        self.login_status: bool = False    # 是否登录成功
        self.connect_status: bool = False  # 是否已发起过连接
        self.subscribed: list = []         # 已登记的订阅合约列表
        self.tick_count: int = 0           # 已收到的 tick 计数（用于判断是否在推送）
        self.td_api_ref: "SimpleTdApi | None" = td_api  # 关联的 TdApi（行情→交易桥梁）

    def connect(self) -> None:
        """发起网络连接（非阻塞，结果在 onFrontConnected 回调返回）。"""
        if not self.connect_status:
            # 同 TdApi：兼容 PyPI vnpy_ctp 6.6.9.1 在不同平台的两种绑定签名。
            flow_path = str(FLOW_PATH / "Md")
            try:
                self.createFtdcMdApi(flow_path, True)
            except TypeError:
                self.createFtdcMdApi(flow_path)
            self.registerFront(SETTING["md_address"])
            self.init()
            self.connect_status = True

    def login(self) -> None:
        """发起登录请求。"""
        self.reqid += 1
        self.reqUserLogin(make_login_req(), self.reqid)

    def subscribe(self, symbols: list) -> None:
        """登记订阅列表；实际订阅在登录回报 onRspUserLogin 中统一执行。"""
        self.subscribed.extend(symbols)

    def onFrontConnected(self) -> None:
        """回报：前置连接成功，接着登录。"""
        log("行情前置连接成功，开始登录")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开 —— CTP 会自动重连，登录成功回调里会重新订阅。"""
        self.login_status = False
        log(f"!! 行情前置连接断开，原因码 {reason}（CTP 会自动重连）")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：登录成功后统一订阅（断线重连时也会走到这里，恢复订阅）。"""
        if not error["ErrorID"]:
            self.login_status = True
            log(f"行情账号登录成功，交易日 {data.get('TradingDay')}，开始订阅 {self.subscribed}")
            for symbol in self.subscribed:  # 本绑定版本一次只接受一个合约，逐个订阅
                self.subscribeMarketData(symbol)
        else:
            log(f"行情登录失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspSubMarketData(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：订阅结果（仅失败时打印）。"""
        if error and error["ErrorID"]:
            log(f"订阅失败 {data.get('InstrumentID')}：{error['ErrorID']} {error['ErrorMsg']}")

    def onRtnDepthMarketData(self, data: dict) -> None:
        """行情推送：CTP 主动推送的深度行情快照，逐条打印。

        实时 vs 离线的判断：
          - 实时推送：data['UpdateTime'] 跟系统时钟相差 < 1 秒
          - 收盘/盘后：data['UpdateTime'] 停在收盘那一刻，差几分钟甚至几小时
        """
        self.tick_count += 1
        # 把最新价喂给 TdApi（撤单超价 / 策略参考价）
        if self.td_api_ref is not None:
            self.td_api_ref.feed_tick(data["InstrumentID"], data["LastPrice"])
        # 每 10 个 tick 打一行汇总（避免成交活跃时刷屏；要看每条 tick 把下行注释打开）
        if self.tick_count % 10 == 1:
            print(
                f"[{now()}] {data['InstrumentID']}"
                f" 最新={data['LastPrice']:.1f}"
                f" 买一={data['BidPrice1']:.1f}x{data['BidVolume1']}"
                f" 卖一={data['AskPrice1']:.1f}x{data['AskVolume1']}"
                f" 成交量={data['Volume']} 持仓量={data['OpenInterest']}"
                f" 时间={data['UpdateTime']}.{data['UpdateMillisec']}"
                f" (累计 {self.tick_count} ticks)",
                flush=True,
            )
        


def _detect_outbound_ip() -> str:
    """尝试探测当前机器的对外 IP（用于申请 SimNow IP 白名单时复制）。

    通过 HTTP 查询 api.ipify.org（无需 TLS，绕过证书问题）；失败时返回 "未知"。
    """
    import socket
    try:
        s = socket.socket()
        s.settimeout(5)
        s.connect(("api.ipify.org", 80))
        s.sendall(b"GET /?format=text HTTP/1.0\r\nHost: api.ipify.org\r\n\r\n")
        data = b""
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        s.close()
        # 解析 HTTP 响应：状态行 + 头 + 空行 + body
        body = data.split(b"\r\n\r\n", 1)[-1].decode().strip()
        return body or "未知（响应为空）"
    except Exception as e:  # noqa: BLE001
        return f"未知（{type(e).__name__}: {e}）"


def main() -> None:
    """主流程：连接交易/行情前置 -> 登记订阅 -> 等待通道就绪 -> 演示下单。

    同时启动 5 秒一次的查资金/查持仓循环（demo 用），让你能直观看到账户状态。
    """
    log(f"账号={SETTING['userid']}  broker={SETTING['brokerid']}")
    log(f"TD前置={SETTING['td_address']}  MD前置={SETTING['md_address']}")
    log(f"当前出口 IP = {_detect_outbound_ip()}（申请白名单时复制这个）")
    log(f"订阅合约={SUBSCRIBED_SYMBOLS}")

    td_api = SimpleTdApi()
    md_api = SimpleMdApi(td_api)  # 把 TdApi 注入 MdApi，让 tick 喂给 TdApi

    log("发起交易前置连接...")
    td_api.connect()
    log("发起行情前置连接...")
    md_api.connect()

    md_api.subscribe(SUBSCRIBED_SYMBOLS)

    log("主线程进入等待（每 5s 查一次资金/持仓；按 Ctrl+C 退出）")

    # ---- demo 用：行情到达 + 通道就绪后，自动买开 1 手 au2612 ----
    demo_order_sent = {"done": False}

    def _demo_buy_when_ready():
        if demo_order_sent["done"]:
            return
        if not (td_api.login_status and td_api.settlement_confirmed):
            return
        if md_api.tick_count == 0:
            return  # 还没收到 tick，等
        # 取最新价 + 1.0 做追价委托（确保成交）
        last = td_api._last_tick.get(SUBSCRIBED_SYMBOLS[0])
        if last is None:
            return
        demo_order_sent["done"] = True
        log(">>> demo 自动买开 1 手 au2612（追价 1.0）")
        td_api.send_order(
            instrument=SUBSCRIBED_SYMBOLS[0],
            exchange="SHFE",
            direction="0",  # 买
            offset="0",     # 开
            price=last + 1.0,
            volume=1,
        )

    last_account_ts = 0.0
    try:
        while True:
            now_ts = _monotonic()
            _demo_buy_when_ready()
            if now_ts - last_account_ts >= 5.0:
                td_api.query_account()
                td_api.query_position()
                last_account_ts = now_ts
            sleep(1)
    except KeyboardInterrupt:
        log("退出")
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        log("退出")
        td_api.exit()
        md_api.exit()


if __name__ == "__main__":
    main()
