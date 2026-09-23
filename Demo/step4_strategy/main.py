"""Step 4 main: 实例化 DummyStrategy，验证 on_tick 被持续调用。"""

import threading
from datetime import datetime
from time import monotonic, sleep

from cta_engine import CtaEngine
from ctp_gateway import CtpGateway, SETTING
from event_engine import EventEngine, EVENT_TICK, EVENT_ORDER, EVENT_TRADE, \
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG
from main_engine import MainEngine
from strategy import DummyStrategy


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    ee = EventEngine()
    ee.start()

    me = MainEngine(ee)
    me.add_gateway("CTP", CtpGateway(ee, SETTING))
    me.connect("CTP")
    me.subscribe("CTP", ["au2612.SHFE"])

    cta = CtaEngine(me, ee)                            # ← Step 4 占位版
    strategy = DummyStrategy(cta, "dummy_au",          # ← 实例化策略
                             "au2612.SHFE", {})

    # ---- handlers：把 EventEngine 事件路由给 strategy ----
    def on_tick(e):
        d = e.data
        cta.last_tick = d                               # 缓存最新 tick 给策略用
        strategy.on_tick(d)                             # ← 路由给策略
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"TICK {d['InstrumentID']} "
              f"最新={d['LastPrice']:.1f} "
              f"买一={d['BidPrice1']:.1f}x{d['BidVolume1']} "
              f"卖一={d['AskPrice1']:.1f}x{d['AskVolume1']}", flush=True)

    def on_order(e):
        d = e.data
        strategy.on_order(d)
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"ORDER {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"ref={d['OrderRef']} 状态={d['OrderStatus']} {d['StatusMsg']}",
              flush=True)

    def on_trade(e):
        d = e.data
        strategy.on_trade(d)
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"TRADE {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"{d['Volume']}手 @ {d['Price']:.1f} TradeID={d['TradeID']}",
              flush=True)

    def on_account(e):
        d = e.data
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"ACCOUNT 可用={d.get('Available', 0):.2f} "
              f"余额={d.get('Balance', 0):.2f}", flush=True)

    def on_position(e):
        d = e.data
        if d.get("Position", 0) > 0:
            print(f"[{now()}] [thread={threading.current_thread().name}] "
                  f"POSITION {d['InstrumentID']} 持仓={d['Position']} "
                  f"盈亏={d.get('PositionProfit', 0):.2f}", flush=True)

    def on_log(e):
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"{e.data['msg']}", flush=True)

    ee.register(EVENT_TICK, on_tick)
    ee.register(EVENT_ORDER, on_order)
    ee.register(EVENT_TRADE, on_trade)
    ee.register(EVENT_ACCOUNT, on_account)
    ee.register(EVENT_POSITION, on_position)
    ee.register(EVENT_LOG, on_log)

    # ---- 启动策略生命周期 ----
    strategy.on_init()
    strategy.on_start()

    print(f"[{now()}] === Step 4: CtaTemplate ===")
    print(f"[{now()}] 策略={strategy.strategy_name}  合约={strategy.vt_symbol}  "
          f"trading={strategy.trading}  pos={strategy.pos}")

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
        strategy.on_stop()
        me.close("CTP")
        ee.stop()


if __name__ == "__main__":
    main()