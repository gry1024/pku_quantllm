"""Step 5 main: 跑 DoubleMaStrategy —— 验证 K 线合成 + 多策略调度 + 信号触发。"""

import threading
from datetime import datetime
from time import monotonic, sleep

from cta_engine import CtaEngine
from ctp_gateway import CtpGateway, SETTING
from double_ma_strategy import DoubleMaStrategy
from event_engine import EventEngine, EVENT_TICK, EVENT_ORDER, EVENT_TRADE, \
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG
from main_engine import MainEngine


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


_tick_count = [0]                                   # 用 list 避免 nonlocal 嵌套


def main() -> None:
    ee = EventEngine()
    ee.start()

    me = MainEngine(ee)
    me.add_gateway("CTP", CtpGateway(ee, SETTING))
    me.connect("CTP")
    me.subscribe("CTP", ["au2612.SHFE"])

    cta = CtaEngine(me, ee)
    strategy = DoubleMaStrategy(
        cta, "double_ma_au", "au2612.SHFE",
        {"fast_window": 5, "slow_window": 20, "test_only": False},
    )
    cta.add_strategy(strategy)

    # ---- handlers: 打印日志 ----
    def on_tick(e):
        d = e.data
        if not d.get("LastPrice"):
            return
        _tick_count[0] += 1
        # 每 50 个 tick 打一行（其余 tick 照样进 BarGenerator）
        if _tick_count[0] % 50 != 1:
            return
        print(f"[{now()}] TICK {d['InstrumentID']} 最新={d['LastPrice']:.1f} "
              f"买一={d['BidPrice1']:.1f} 卖一={d['AskPrice1']:.1f}", flush=True)

    def on_order(e):
        d = e.data
        print(f"[{now()}] ORDER {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"ref={d['OrderRef']} 状态={d['OrderStatus']} {d['StatusMsg']}",
              flush=True)

    def on_trade(e):
        d = e.data
        print(f"[{now()}] TRADE {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"{d['Volume']}手 @ {d['Price']:.1f} TradeID={d['TradeID']}",
              flush=True)

    def on_position(e):
        d = e.data
        if d.get("Position", 0) > 0:
            print(f"[{now()}] POSITION {d['InstrumentID']} 持仓={d['Position']} "
                  f"盈亏={d.get('PositionProfit', 0):.2f}", flush=True)

    def on_account(e):
        d = e.data
        print(f"[{now()}] ACCOUNT 可用={d.get('Available', 0):.2f} "
              f"余额={d.get('Balance', 0):.2f}", flush=True)

    def on_log(e):
        print(f"[{now()}] {e.data['msg']}", flush=True)

    ee.register(EVENT_TICK, on_tick)
    ee.register(EVENT_ORDER, on_order)
    ee.register(EVENT_TRADE, on_trade)
    ee.register(EVENT_ACCOUNT, on_account)
    ee.register(EVENT_POSITION, on_position)
    ee.register(EVENT_LOG, on_log)

    # ---- 启动策略生命周期 ----
    cta.init_all()
    cta.start_all()

    print(f"[{now()}] === Step 5: CtaEngine + DoubleMaStrategy ===")
    print(f"[{now()}] 策略={strategy.strategy_name} 合约={strategy.vt_symbol} "
          f"fast={strategy.fast_window} slow={strategy.slow_window} "
          f"test_only={strategy.test_only}")
    print(f"[{now()}] 需等 ≥ {strategy.slow_window + 1} 根 1 分钟 bar 才出信号")

    last_q = 0.0
    try:
        while True:
            ts = monotonic()
            if ts - last_q >= 5.0:
                me.query_account()
                me.query_position()
                last_q = ts
            sleep(0.5)
    except KeyboardInterrupt:
        print(f"[{now()}] 退出")
        cta.stop_all()
        me.close("CTP")
        ee.stop()


if __name__ == "__main__":
    main()