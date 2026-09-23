"""Step 7: run.py —— 生产入口（7×24 异常自愈主循环）。

跟 Step 5 的 main.py 区别：
- main.py 是 demo，跑一次 Ctrl+C 就退
- run.py 是生产入口：捕获所有异常 → 10 秒后重建整个系统再跑
- 配合 supervisor（systemd / supervisorctl）做进程级守护

⚠️ 这文件**不是**给 IDE F5 调试用的——调试用 main.py（Step 5 那个）。
    run.py 的作用是"挂了别怕，自动爬起来"。

完整启动流程：
    EventEngine.start() → MainEngine() → 加 CtpGateway → 读 SimNow 凭证 → 连接
    → CtaEngine() → add_strategy(DoubleMaStrategy) → init_all() → start_all()
    → TradeRecorder() → 阻塞主循环（每 60s 查一次账户）
    → 任意一步抛异常 → 10s sleep → 整个重建

进程级守护（超出本文件范围）：
    supervisor / systemd 看到进程退出 → 立刻拉起 run.py
    所以 run.py 内的 while-try 解决"运行中异常"，supervisor 解决"进程死掉"。
"""

import sys
import threading
import time
import traceback
from datetime import datetime
from time import sleep

from cta_engine import CtaEngine
from ctp_gateway import CtpGateway
from ctp_setting import build_setting
from double_ma_strategy import DoubleMaStrategy
from event_engine import EventEngine
from logger import setup_logger
from main_engine import MainEngine
from trade_recorder import TradeRecorder


# 让 logger 在主进程里只初始化一次
logger = setup_logger()

# ---- 配置 ----
VT_SYMBOL = "au2612.SHFE"
STRATEGY_NAME = "double_ma_au"
STRATEGY_SETTING = {
    "fast_window": 5,
    "slow_window": 20,
    "test_only": False,            # ← 生产模式真下单！先用 au2612 小试
}
RESTART_DELAY = 10                 # 异常后等 10 秒再重建（防"雪崩"反复挂）
QUERY_INTERVAL = 60                # 每 60 秒查一次账户/持仓
DAILY_RESTART_HOUR = None          # None = 不主动重启；=4 = 每天凌晨 4 点重启（换日清理）


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def bootstrap() -> dict:
    """建一个全新的运行时（EventEngine / MainEngine / CtpGateway / Cta / Recorder）。"""
    logger.info("=== bootstrap 开始 ===")

    ee = EventEngine()
    ee.start()

    me = MainEngine(ee)
    setting = build_setting()
    logger.info(f"CTP 凭证已读取（user_id={setting['用户名']}）")

    gw = CtpGateway(ee, setting)
    me.add_gateway("CTP", gw)
    me.connect("CTP")
    logger.info("CTP 网关已 add + connect（异步，连接结果稍后回调）")

    # 等 CTP 真正连上（onFrontConnected → onRspUserLogin → onRspSettlementInfoConfirm）
    # 简单做法：固定 sleep；严谨做法是订阅 EVENT_LOG 等待 'CTP 已登录'。
    # 7×24 仿真连接很快，10s 一般够；真要严谨就用 log 关键字判断。
    sleep(10)
    me.subscribe("CTP", [VT_SYMBOL])

    cta = CtaEngine(me, ee)
    strategy = DoubleMaStrategy(cta, STRATEGY_NAME, VT_SYMBOL, STRATEGY_SETTING)
    cta.add_strategy(strategy)
    cta.init_all()
    cta.start_all()
    logger.info(f"策略 {STRATEGY_NAME} 已 init + start（{VT_SYMBOL}）")

    recorder = TradeRecorder(ee)
    logger.info("TradeRecorder 已启动")

    logger.info("=== bootstrap 完成 ===")
    return {
        "ee": ee, "me": me, "gw": gw, "cta": cta,
        "strategy": strategy, "recorder": recorder,
    }


def shutdown(parts: dict) -> None:
    """优雅关闭：先停策略 → 关网关 → 停 EventEngine。"""
    logger.info("=== shutdown 开始 ===")
    try:
        if "cta" in parts:
            parts["cta"].stop_all()
    except Exception:
        logger.exception("stop_all 失败")
    try:
        if "gw" in parts:
            parts["gw"].close()
    except Exception:
        logger.exception("网关关闭失败")
    try:
        if "recorder" in parts:
            parts["recorder"].stop()
    except Exception:
        logger.exception("recorder stop 失败")
    try:
        if "ee" in parts:
            parts["ee"].stop()
    except Exception:
        logger.exception("EventEngine stop 失败")
    logger.info("=== shutdown 完成 ===")


def run_until_dead(parts: dict) -> None:
    """主阻塞循环：每 60 秒查一次账户/持仓。"""
    me = parts["me"]
    recorder = parts["recorder"]
    logger.info(f"[{_now()}] 进入主循环，每 {QUERY_INTERVAL}s 查一次账户")

    last_query = time.monotonic()
    while True:
        now = time.monotonic()
        if now - last_query >= QUERY_INTERVAL:
            try:
                me.query_account()
                me.query_position()
                logger.info(
                    f"[{_now()}] 心跳 ok | 累计 trade {recorder.count} 笔"
                )
            except Exception:
                logger.exception("查账户/持仓异常")
            last_query = now
        sleep(1)


def main() -> None:
    """外层 while-try：捕获一切异常 → 重建。"""
    logger.info(f"[{_now()}] ===== run.py 启动 =====")
    logger.info(f"[{_now()}] Python={sys.version.split()[0]}  PID={__import__('os').getpid()}")

    while True:
        parts = {}
        try:
            parts = bootstrap()
            run_until_dead(parts)

        except KeyboardInterrupt:
            logger.info("Ctrl+C 收到，正常退出")
            shutdown(parts)
            break

        except SystemExit:
            logger.warning("SystemExit 收到，正常退出")
            shutdown(parts)
            break

        except Exception as e:
            # 把整条 traceback 打到异常日志
            logger.exception(f"[{_now()}] ❌ 主循环异常: {e}")
            logger.info(f"[{_now()}] {RESTART_DELAY}s 后自动重建")
            shutdown(parts)
            sleep(RESTART_DELAY)

    logger.info(f"[{_now()}] ===== run.py 退出 =====")


if __name__ == "__main__":
    main()