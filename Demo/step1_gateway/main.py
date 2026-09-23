"""Step 1 main: 用 CtpGateway 跑 SimNow，验证事件能流出去。"""

from datetime import datetime
from time import monotonic, sleep

from ctp_gateway import CtpGateway, SETTING
from event_engine import (
    EventEngine,
    EVENT_TICK, EVENT_ACCOUNT, EVENT_POSITION, EVENT_LOG,
)


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    ee = EventEngine()
    gateway = CtpGateway(ee, SETTING)

    def on_tick(e):
        d = e.data
        print(f"[{now()}] TICK {d['InstrumentID']} "
              f"最新={d['LastPrice']:.1f} "
              f"买一={d['BidPrice1']:.1f}x{d['BidVolume1']} "
              f"卖一={d['AskPrice1']:.1f}x{d['AskVolume1']}", flush=True)

    def on_account(e):
        d = e.data
        print(f"[{now()}] ACCOUNT 可用={d.get('Available', 0):.2f} "
              f"余额={d.get('Balance', 0):.2f} "
              f"冻结={d.get('FrozenMargin', 0):.2f}", flush=True)

    def on_position(e):
        d = e.data
        if d.get("Position", 0) > 0:
            print(f"[{now()}] POSITION {d['InstrumentID']} "
                  f"持仓={d['Position']} 盈亏={d.get('PositionProfit', 0):.2f}",
                  flush=True)

    def on_log(e):
        print(f"[{now()}] {e.data['msg']}", flush=True)

    ee.register(EVENT_TICK, on_tick)
    ee.register(EVENT_ACCOUNT, on_account)
    ee.register(EVENT_POSITION, on_position)
    ee.register(EVENT_LOG, on_log)

    print(f"[{now()}] === Step 1: CtpGateway ===")
    print(f"[{now()}] 账号={SETTING['userid']}  broker={SETTING['brokerid']}")
    print(f"[{now()}] TD={SETTING['td_address']}  MD={SETTING['md_address']}")
    gateway.connect()
    gateway.subscribe(["au2612"])

    last_q = 0.0
    try:
        while True:
            ts = monotonic()
            if ts - last_q >= 5.0:
                gateway.query_account()
                gateway.query_position()
                last_q = ts
            sleep(1)
    except KeyboardInterrupt:
        print(f"[{now()}] 退出")
        gateway.close()


if __name__ == "__main__":
    main()