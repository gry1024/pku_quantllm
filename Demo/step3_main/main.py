"""Step 3 main: 通过 MainEngine 操作 Gateway，并演示 send_order 端到端跑通。"""

import threading
from datetime import datetime
from time import monotonic, sleep

from ctp_gateway import CtpGateway, SETTING
from event_engine import EventEngine, EVENT_TICK, EVENT_ORDER, EVENT_TRADE, \
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG
from main_engine import MainEngine


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    ee = EventEngine()
    ee.start()

    me = MainEngine(ee)
    me.add_gateway("CTP", CtpGateway(ee, SETTING))   # ← 只和 MainEngine 打交道
    me.connect("CTP")                                 # ← 不再 import CtpGateway 的 send_order
    me.subscribe("CTP", ["au2612.SHFE"])              # ← vt_symbol 格式

    # ---- handlers ----
    def on_tick(e):
        d = e.data
        state["last_price"] = d["LastPrice"]
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"TICK {d['InstrumentID']} "
              f"最新={d['LastPrice']:.1f} "
              f"买一={d['BidPrice1']:.1f}x{d['BidVolume1']} "
              f"卖一={d['AskPrice1']:.1f}x{d['AskVolume1']}", flush=True)

    def on_order(e):
        d = e.data
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"ORDER {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"ref={d['OrderRef']} "
              f"状态={d['OrderStatus']} {d['StatusMsg']}", flush=True)

    def on_trade(e):
        d = e.data
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"TRADE {d['InstrumentID']} "
              f"{'买' if d['Direction'] == '0' else '卖'} "
              f"{d['Volume']}手 @ {d['Price']:.1f} "
              f"TradeID={d['TradeID']}", flush=True)

    def on_account(e):
        d = e.data
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"ACCOUNT 可用={d.get('Available', 0):.2f} "
              f"余额={d.get('Balance', 0):.2f} "
              f"冻结={d.get('FrozenMargin', 0):.2f}", flush=True)

    def on_position(e):
        d = e.data
        if d.get("Position", 0) > 0:
            print(f"[{now()}] [thread={threading.current_thread().name}] "
                  f"POSITION {d['InstrumentID']} "
                  f"持仓={d['Position']} 盈亏={d.get('PositionProfit', 0):.2f}",
                  flush=True)

    def on_log(e):
        print(f"[{now()}] [thread={threading.current_thread().name}] "
              f"{e.data['msg']}", flush=True)

    ee.register(EVENT_TICK, on_tick)
    ee.register(EVENT_ORDER, on_order)
    ee.register(EVENT_TRADE, on_trade)
    ee.register(EVENT_ACCOUNT, on_account)
    ee.register(EVENT_POSITION, on_position)
    ee.register(EVENT_LOG, on_log)

    print(f"[{now()}] === Step 3: MainEngine ===")
    print(f"[{now()}] 账号={SETTING['userid']}  broker={SETTING['brokerid']}")

    # ---- 演示：等 TD 就绪 + 收到第一个 tick 后，自动买开 1 手 ----
    state = {"last_price": None, "demo_order_sent": False}

    last_q = 0.0
    last_status = 0.0
    start = monotonic()
    try:
        while True:
            ts = monotonic()
            # 每 2 秒报告一次等待状态（方便看清卡在哪一步）
            if not state["demo_order_sent"] and ts - last_status >= 2.0:
                last_status = ts
                td_ready = me.gateways["CTP"].is_ready()
                tick_ok = state["last_price"] is not None
                print(f"[{now()}] 等待 demo 下单条件："
                      f"TD 就绪={td_ready}  首 tick={tick_ok}  "
                      f"已等 {ts - start:.1f}s", flush=True)
            # demo 下单（4 个条件都满足才执行，且只发一次）
            if (not state["demo_order_sent"]
                    and ts - start > 3.0
                    and me.gateways["CTP"].is_ready()
                    and state["last_price"] is not None):
                state["demo_order_sent"] = True
                order_price = round(state["last_price"] + 1.0, 2)  # 修浮点 + au 最小价位 0.02
                ref = me.send_order("au2612.SHFE", "0", "0",
                                    order_price, 1)
                if ref:
                    print(f"[{now()}] >>> demo 自动买开 1 手 @ {order_price:.1f} "
                          f"ref={ref}", flush=True)
                else:
                    print(f"[{now()}] !!! demo 下单被拒（ref 空，看 EVENT_LOG）",
                          flush=True)
            # 资金/持仓轮询
            if ts - last_q >= 5.0:
                me.query_account()
                me.query_position()
                last_q = ts
            sleep(0.5)
    except KeyboardInterrupt:
        print(f"[{now()}] 退出")
        me.close("CTP")
        ee.stop()


if __name__ == "__main__":
    main()