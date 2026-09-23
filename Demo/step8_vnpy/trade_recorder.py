"""Step 8: trade_recorder —— 订阅 vnpy 的 EVENT_TRADE → 落盘到 data/trades.csv。"""

from __future__ import annotations

import csv
from pathlib import Path

from vnpy.trader.event import EVENT_TRADE
from vnpy.event import EventEngine


_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_TRADES_CSV = _DATA_DIR / "trades.csv"

_FIELDS = [
    "time", "vt_symbol", "direction", "offset",
    "price", "volume", "order_ref", "trade_id",
]


class TradeRecorder:
    """订阅 EVENT_TRADE → 追加 trades.csv。"""

    def __init__(self, event_engine: EventEngine) -> None:
        self.event_engine = event_engine
        self._count: int = 0
        self._ensure_header()
        self.event_engine.register(EVENT_TRADE, self._on_trade)

    def _ensure_header(self) -> None:
        if not _TRADES_CSV.exists():
            with _TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(_FIELDS)
            self._count = 0
        else:
            with _TRADES_CSV.open(encoding="utf-8") as f:
                self._count = sum(1 for _ in f) - 1

    def _on_trade(self, event) -> None:
        d = event.data
        vt_symbol = f"{d.symbol}.{d.exchange.value}"
        row = {
            "time": d.datetime.strftime("%Y-%m-%d %H:%M:%S") if d.datetime else "",
            "vt_symbol": vt_symbol,
            "direction": "BUY" if d.direction.value == "LONG" else "SELL",
            "offset": d.offset.value,
            "price": d.price,
            "volume": d.volume,
            "order_ref": d.orderid,
            "trade_id": d.tradeid,
        }
        try:
            with _TRADES_CSV.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, _FIELDS).writerow(row)
            self._count += 1
        except Exception as e:
            print(f"[trade_recorder] ⚠️ 写盘失败: {e} row={row}")

    def stop(self) -> None:
        self.event_engine.unregister(EVENT_TRADE, self._on_trade)

    @property
    def count(self) -> int:
        return self._count