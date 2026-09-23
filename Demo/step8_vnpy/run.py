"""Step 8: run.py - 用 vnpy 现成栈跑 7x24 双均线。

整个文件 < 200 行，因为：
  - EventEngine 用 vnpy 的（不要自己造）
  - MainEngine 用 vnpy 的（gateway 注册 + 子引擎都自动）
  - CtpGateway 用 vnpy_ctp 的（一步 add_gateway）
  - CtaEngine 用 vnpy_ctastrategy 的（add_app 自动装）
  - CtaTemplate 用 vnpy 的（策略代码 0 改动）
  - BarGenerator 用 vnpy 自带的（on_tick 内置）

我们只补 3 件事：
  1. 异常自愈循环（while-try）
  2. loguru 滚动日志
  3. trades.csv 落盘

启动：python run.py
守护：supervisor / systemd
"""

import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from time import sleep

from loguru import logger
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.constant import Exchange
from vnpy.trader.object import SubscribeRequest
from vnpy_ctp import CtpGateway
from vnpy_ctastrategy import CtaStrategyApp

from cta_setting import setup_dll_path, load_credentials
from strategy.double_ma_strategy import DoubleMaStrategy


# ========== 配置 ==========
VT_SYMBOL = "au2612.SHFE"            # 上期所黄金主力
STRATEGY_NAME = "double_ma_au"       # 策略名（持久化文件名依赖这个）
STRATEGY_SETTING = {
    "fast_window": 5,
    "slow_window": 20,
}
LOG_DIR = Path(__file__).parent / "logs"
DATA_DIR = Path(__file__).parent / "data"
TRADES_CSV = DATA_DIR / "trades.csv"
RESTART_DELAY = 10                   # 异常后等 10s 再起

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


# ========== loguru ==========
def setup_logger() -> None:
    logger.remove()
    logger.add(
        sink=lambda m: print(m, end=""),
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<7}</level> | "
               "<cyan>{name}</cyan> - <level>{message}</level>",
        level="INFO",
    )
    logger.add(
        sink=str(LOG_DIR / f"run_{datetime.now():%Y-%m-%d}.log"),
        format="{time:HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} - {message}",
        level="INFO",
        rotation="00:00", retention="30 days",
        encoding="utf-8", enqueue=True,
    )
    logger.add(
        sink=str(LOG_DIR / f"error_{datetime.now():%Y-%m-%d}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
               "{name}:{function}:{line} - {message}\n{exception}",
        level="WARNING",
        rotation="00:00", retention="90 days",
        encoding="utf-8", enqueue=True,
    )


# ========== trades.csv ==========
def ensure_trades_header() -> None:
    if not TRADES_CSV.exists():
        with TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["time", "vt_symbol", "direction", "offset", "price",
                 "volume", "order_ref", "trade_id"]
            )


def _on_trade(event) -> None:
    d = event.data
    vt = f"{d.symbol}.{d.exchange.value}"
    row = {
        "time": d.datetime.strftime("%Y-%m-%d %H:%M:%S") if d.datetime else "",
        "vt_symbol": vt,
        "direction": "BUY" if d.direction.value == "LONG" else "SELL",
        "offset": d.offset.value,
        "price": d.price,
        "volume": d.volume,
        "order_ref": d.orderid,
        "trade_id": d.tradeid,
    }
    try:
        with TRADES_CSV.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, list(row.keys())).writerow(row)
        logger.info(f"📒 TRADE {row['direction']} {row['vt_symbol']} "
                    f"{row['volume']}手 @{row['price']}")
    except Exception as e:
        logger.exception(f"写 trades.csv 失败: {e}")


# ========== bootstrap ==========
def bootstrap():
    """建一个全新的运行时（EventEngine / MainEngine / CtpGateway / CtaEngine）。"""
    logger.info("=== bootstrap 开始 ===")

    ee = EventEngine()
    me = MainEngine(ee)
    me.add_gateway(CtpGateway, "CTP")
    cta_engine = me.add_app(CtaStrategyApp)                  # ← add_app 返回 engine
    if cta_engine is None:
        raise RuntimeError("CtaStrategyApp 没装上")

    # ---- 连 CTP ----
    setting = load_credentials()
    me.connect(setting, "CTP")
    logger.info("CTP 网关已 connect（异步，等 onRspUserLogin）")

    # ---- 等 CTP 登录完成 ----
    sleep(10)                                                # 7x24 连接一般 < 5s

    # ---- 订阅行情 ----
    symbol, exchange_str = VT_SYMBOL.split(".")
    exchange = Exchange(exchange_str)
    me.subscribe(SubscribeRequest(symbol=symbol, exchange=exchange), "CTP")
    logger.info(f"已订阅 {VT_SYMBOL}")

    # ---- 注册策略类（CtaEngine.classes 里要有名才能 add_strategy） ----
    cta_engine.load_strategy_class_from_module("strategy.double_ma_strategy")

    # ---- 加策略 + init + start ----
    cta_engine.add_strategy(
        DoubleMaStrategy.__name__, STRATEGY_NAME, VT_SYMBOL, STRATEGY_SETTING
    )
    cta_engine.init_strategy(STRATEGY_NAME)                  # ← vnpy 会自动加载 .json
    cta_engine.start_strategy(STRATEGY_NAME)
    logger.info(f"策略 {STRATEGY_NAME} 已 init + start")

    # ---- 订阅成交落盘 ----
    ee.register("eTrade", _on_trade)
    ensure_trades_header()

    logger.info("=== bootstrap 完成 ===")
    return {"ee": ee, "me": me, "cta": cta_engine}


def shutdown(parts) -> None:
    logger.info("=== shutdown 开始 ===")
    try:
        if "cta" in parts:
            parts["cta"].stop_strategy(STRATEGY_NAME)
    except Exception:
        logger.exception("stop_strategy 失败")
    try:
        if "me" in parts:
            parts["me"].close()
    except Exception:
        logger.exception("MainEngine close 失败")
    logger.info("=== shutdown 完成 ===")


def main() -> None:
    setup_dll_path()
    setup_logger()
    logger.info(f"===== run.py 启动 PID={__import__('os').getpid()} =====")

    while True:
        parts = {}
        try:
            parts = bootstrap()
            # ---- 阻塞主循环：每 60s 打心跳 ----
            last_q = time.monotonic()
            while True:
                now = time.monotonic()
                if now - last_q >= 60:
                    account = parts["me"].get_all_accounts()
                    pos = parts["me"].get_all_positions()
                    logger.info(
                        f"💓 心跳 | "
                        f"账户={[(a.accountid, a.available) for a in account]} "
                        f"持仓={[(p.symbol, p.volume, p.pnl) for p in pos if p.volume]}"
                    )
                    last_q = now
                sleep(1)

        except KeyboardInterrupt:
            logger.info("Ctrl+C 收到，正常退出")
            shutdown(parts)
            break

        except Exception as e:
            logger.exception(f"❌ 主循环异常: {e}")
            shutdown(parts)
            logger.info(f"{RESTART_DELAY}s 后自动重建")
            sleep(RESTART_DELAY)

    logger.info("===== run.py 退出 =====")


# ---- supervisor / kill -TERM 优雅退出 ----
def _sigterm(signum, frame):
    raise KeyboardInterrupt()
signal.signal(signal.SIGTERM, _sigterm)


if __name__ == "__main__":
    main()
