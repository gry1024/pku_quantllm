"""Step 7: trade_recorder —— 把成交明细落盘到 data/trades.csv。

为什么用 CSV 不用 SQLite？
- 7×24 跑一周，trades 也就几百行（au 一天最多几笔），CSV 用 excel 打开直接看
- 启动时 append 模式追加，crash 后不会丢已有数据
- 真正量大再换 SQLite / TimescaleDB（learn.md "下一步" 里列了）

字段：
    time         成交时间（CTP 给的交易所时间，不是本地时间）
    vt_symbol    合约.SHFE
    direction   BUY / SELL（人类可读版，原版是 0/1）
    offset      OPEN / CLOSE
    price       成交价
    volume      手数
    order_ref   委托号
    trade_id    成交号

⚠️ position / account 不在这里——CTP 每秒推几百次，单独一份 csv 会爆炸。
    真要看账户状态，开 veighna GUI 或加 FastAPI（README 里有可选方案）。
"""

import csv
from datetime import datetime
from pathlib import Path

from event_engine import EventEngine, EVENT_TRADE


_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_TRADES_CSV = _DATA_DIR / "trades.csv"

# 字段顺序固定，方便后续 pandas 读
_FIELDS = ["time", "vt_symbol", "direction", "offset", "price",
           "volume", "order_ref", "trade_id"]


def _direction(d: str) -> str:
    """CTP 0/1 → 'BUY' / 'SELL'。"""
    return "BUY" if d == "0" else "SELL"


def _offset(o: str) -> str:
    """CTP 0/1/2/3 → 'OPEN' / 'CLOSE' / 'CLOSETODAY' / 'CLOSEYESTERDAY'。"""
    return {0: "OPEN", 1: "CLOSE", 2: "CLOSETODAY", 3: "CLOSEYESTERDAY"}.get(
        int(o), o
    )


class TradeRecorder:
    """订阅 EVENT_TRADE → 追加 trades.csv。

    用法：
        ee = EventEngine()
        recorder = TradeRecorder(ee)
        ee.start()
        # ... 系统运行中 ...
        recorder.stop()
    """

    def __init__(self, event_engine: EventEngine) -> None:
        self.event_engine = event_engine
        self._count: int = 0
        # CSV 头不存在就写一次
        self._ensure_header()
        self.event_engine.register(EVENT_TRADE, self._on_trade)

    def _ensure_header(self) -> None:
        """trades.csv 不存在时写入 header。"""
        if not _TRADES_CSV.exists():
            with _TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(_FIELDS)
            self._count = 0
        else:
            # 统计已有行数（不算 header）
            with _TRADES_CSV.open(encoding="utf-8") as f:
                self._count = sum(1 for _ in f) - 1

    def _on_trade(self, event) -> None:
        """EVENT_TRADE 回调 —— 收到成交回报就追加一行。"""
        d = event.data
        # CTP 的 InstrumentID + ExchangeID → vt_symbol
        vt_symbol = f"{d.get('InstrumentID', '')}.{d.get('ExchangeID', '')}"
        row = {
            "time": d.get("TradeTime", datetime.now().strftime("%H:%M:%S")),
            "vt_symbol": vt_symbol,
            "direction": _direction(d.get("Direction", "")),
            "offset": _offset(d.get("Offset", "")),
            "price": d.get("Price", 0.0),
            "volume": d.get("Volume", 0),
            "order_ref": d.get("OrderRef", ""),
            "trade_id": d.get("TradeID", ""),
        }
        try:
            with _TRADES_CSV.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, _FIELDS).writerow(row)
            self._count += 1
        except Exception as e:
            # 写盘失败不能让策略也跟着挂 —— 只打日志
            print(f"[trade_recorder] ⚠️ 写盘失败: {e} row={row}")

    def stop(self) -> None:
        """取消订阅。"""
        self.event_engine.unregister(EVENT_TRADE, self._on_trade)

    @property
    def count(self) -> int:
        return self._count