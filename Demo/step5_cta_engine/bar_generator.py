"""Step 5: BarGenerator —— 把 tick 聚成 1 分钟 K 线。

设计：
- 每个 BarGenerator 实例绑定一个 vt_symbol（一个策略订阅一个合约）
- 状态：当前 minute 的 Bar + 上一次 tick 的累计成交量（用于算本 minute delta）
- on_tick:
    - 用 tick 时间戳算出"它属于哪一分钟"
    - 若跟当前 Bar 的 minute 不同 → 推旧 Bar 给回调，开新 Bar
    - 否则用 tick 更新当前 Bar 的 OHLCV
- Bar 触发时调回调（由 CtaTemplate 在 init 时注入 strategy.on_bar 的入口）

时间来源：
    CTP tick 自带 TradingDay + UpdateTime + UpdateMillisec，
    本类负责合成 datetime；如 tick 已有 datetime 字段则直接用。
"""

from collections import deque
from datetime import datetime
from typing import Callable


class Bar:
    """1 分钟 K 线 OHLCV。"""

    __slots__ = ("vt_symbol", "datetime", "open", "high", "low", "close", "volume")

    def __init__(self, vt_symbol: str, dt: datetime) -> None:
        self.vt_symbol = vt_symbol
        self.datetime = dt
        self.open = self.high = self.low = self.close = 0.0
        self.volume = 0

    def update(self, tick: dict, prev_volume: int) -> None:
        """用最新 tick 更新 OHLCV。"""
        last = tick.get("LastPrice", 0.0) or 0.0
        if last <= 0:
            return
        if self.open == 0.0:
            self.open = self.high = self.low = last
        else:
            if last > self.high:
                self.high = last
            if last < self.low:
                self.low = last
        self.close = last
        cur_vol = tick.get("Volume", 0) or 0
        delta = max(0, cur_vol - prev_volume)        # 本 minute 内新增成交量
        self.volume += delta

    def to_dict(self) -> dict:
        return {
            "vt_symbol": self.vt_symbol,
            "datetime": self.datetime,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class BarGenerator:
    """把 tick 聚成 K 线。"""

    def __init__(self, vt_symbol: str, on_bar: Callable[[dict], None]) -> None:
        self.vt_symbol = vt_symbol
        self._on_bar = on_bar
        self._cur_bar: Bar | None = None
        self._prev_vol: int = 0                       # 上一根 tick 的当日累计成交量

    def on_tick(self, tick: dict) -> None:
        ts = _parse_ts(tick)
        if ts is None:
            return
        minute = ts.replace(second=0, microsecond=0)
        cur_vol = tick.get("Volume", 0) or 0
        if self._cur_bar is None or minute > self._cur_bar.datetime:
            # 新一分钟 —— 推旧 Bar，开新 Bar
            if self._cur_bar is not None:
                self._on_bar(self._cur_bar.to_dict())
            self._cur_bar = Bar(self.vt_symbol, minute)
        self._cur_bar.update(tick, self._prev_vol)
        self._prev_vol = cur_vol

    def reset(self) -> None:
        """清空当前 Bar（跨交易日 / 换合约时用）。"""
        self._cur_bar = None
        self._prev_vol = 0


def _parse_ts(tick: dict) -> datetime | None:
    """合成 tick 的 datetime。优先用已有 datetime 字段，否则用 TradingDay + UpdateTime。"""
    if "datetime" in tick and tick["datetime"]:
        return tick["datetime"]
    td = tick.get("TradingDay", "")
    ut = tick.get("UpdateTime", "00:00:00")
    if not td:
        return None
    try:
        dt = datetime.strptime(f"{td} {ut}", "%Y%m%d %H:%M:%S")
        ms = tick.get("UpdateMillisec", 0) or 0
        return dt.replace(microsecond=ms * 1000)
    except (ValueError, TypeError):
        return None