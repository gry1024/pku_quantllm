"""
最小化 CTP 交易 DEMO：从底层 Python 交易 API 开始（vnpy_ctp.api，非 CtpGateway）。

流程：
1. 连接交易前置（TdApi）：发起网络连接 -> 终端认证 -> 登录账号
2. 连接行情前置（MdApi）：发起网络连接 -> 登录账号
3. 订阅合约行情（subscribeMarketData）
4. 打印每一条行情推送（onRtnDepthMarketData）

CTP 是纯异步回调驱动的 API：所有 req 只负责发出请求，
结果通过对应的 onRsp/onRtn 回调（CTP 内部线程）返回。

用法::

    cp .env.example .env    # 第一次跑前；填入 SIMNOW 凭证
    python demo.py

凭证全部走环境变量：``CTP_USERID`` / ``CTP_PASSWORD`` / ``CTP_BROKERID`` /
``CTP_TD_ADDRESS`` / ``CTP_MD_ADDRESS`` / ``CTP_APPID`` / ``CTP_AUTH_CODE``。
本文件不持有任何默认账号，避免共享账号泄漏到 git 历史。
"""

import os
from datetime import datetime
from pathlib import Path
from time import sleep

from vnpy_ctp.api import MdApi, TdApi


# ---------------------------------------------------------------------------
# 凭证加载：仅从环境变量（或 .env 文件）读取，绝不硬编码默认值
# ---------------------------------------------------------------------------
def _load_setting() -> dict[str, str]:
    """从环境变量组装 SIMNOW 连接参数。缺失则报错，不静默回落到默认账号。"""
    # 优先读本地 .env（不强制依赖 python-dotenv）
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

# 订阅的合约列表（须为当前挂牌月份；摘牌合约订阅无任何错误回报，只是收不到行情）
SUBSCRIBED_SYMBOLS = ["au2612", "au2706", "rb2601", "rb2605", "rb2610"]

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
    """交易 API：演示 连接 -> 认证 -> 登录 的完整握手状态机。"""

    def __init__(self) -> None:
        """初始化请求计数与连接/登录状态。"""
        super().__init__()                 # 必须调用：C++ 扩展对象初始化
        self.reqid: int = 0                # 请求编号，每次请求自增
        self.login_status: bool = False    # 是否登录成功
        self.connect_status: bool = False  # 是否已发起过连接（防重复连接导致崩溃）

    def connect(self) -> None:
        """发起网络连接（非阻塞，结果在 onFrontConnected 回调返回）。"""
        if not self.connect_status:
            self.createFtdcTraderApi(str(FLOW_PATH / "Td").encode("GBK"), True)
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

    def onFrontConnected(self) -> None:
        """回报：前置连接成功，接着走认证。"""
        log("交易前置连接成功，开始终端认证")
        self.authenticate()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开。"""
        log(f"交易前置连接断开，原因码 {reason}")

    def onRspAuthenticate(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：终端认证结果，成功则接着登录。"""
        if not error["ErrorID"]:
            log("终端认证成功，开始登录")
            self.login()
        else:
            log(f"终端认证失败：{error['ErrorID']} {error['ErrorMsg']}")

    def onRspUserLogin(self, data: dict, error: dict, reqid: int, last: bool) -> None:
        """回报：登录结果。"""
        if not error["ErrorID"]:
            self.login_status = True
            log(f"交易账号登录成功，交易日 {data.get('TradingDay')}")
        else:
            log(f"交易登录失败：{error['ErrorID']} {error['ErrorMsg']}")


class SimpleMdApi(MdApi):
    """行情 API：连接 -> 登录 -> 订阅 -> 打印推送。"""

    def __init__(self) -> None:
        """初始化请求计数、连接/登录状态与订阅列表。"""
        super().__init__()                 # 必须调用：C++ 扩展对象初始化
        self.reqid: int = 0                # 请求编号，每次请求自增
        self.login_status: bool = False    # 是否登录成功
        self.connect_status: bool = False  # 是否已发起过连接
        self.subscribed: list = []         # 已登记的订阅合约列表

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

    def subscribe(self, symbols: list) -> None:
        """登记订阅列表；实际订阅在登录回报 onRspUserLogin 中统一执行。"""
        self.subscribed.extend(symbols)

    def onFrontConnected(self) -> None:
        """回报：前置连接成功，接着登录。"""
        log("行情前置连接成功，开始登录")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """回报：前置连接断开。"""
        self.login_status = False
        log(f"行情前置连接断开，原因码 {reason}")

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
        """行情推送：CTP 主动推送的深度行情快照，逐条打印。"""
        print(
            f"[{now()}] {data['InstrumentID']}"
            f" 最新={data['LastPrice']:.1f}"
            f" 买一={data['BidPrice1']:.1f}x{data['BidVolume1']}"
            f" 卖一={data['AskPrice1']:.1f}x{data['AskVolume1']}"
            f" 成交量={data['Volume']} 持仓量={data['OpenInterest']}"
            f" 时间={data['UpdateTime']}.{data['UpdateMillisec']}",
            flush=True,
        )


def main() -> None:
    """主流程：连接交易/行情前置 -> 登记订阅 -> 等待推送。"""
    td_api = SimpleTdApi()
    md_api = SimpleMdApi()

    log("发起交易前置连接...")
    td_api.connect()
    log("发起行情前置连接...")
    md_api.connect()

    md_api.subscribe(SUBSCRIBED_SYMBOLS)

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
