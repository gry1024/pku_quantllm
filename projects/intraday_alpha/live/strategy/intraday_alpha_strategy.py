"""Live CtaTemplate consumer for the intraday alpha pipeline.

Each 1-minute bar, reads the latest model signal from the JSON file emitted
by :mod:`intraday_alpha.run` and flips between +fixed_size / -fixed_size / 0
based on threshold crossings. Atomic file write (tmp + os.replace) on the
research side guarantees we never read a half-written signal file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from vnpy.trader.constant import Direction, Offset
from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy.trader.utility import BarGenerator, ArrayManager, round_to

from vnpy_ctastrategy import CtaTemplate


if TYPE_CHECKING:
    pass


class IntradayAlphaStrategy(CtaTemplate):
    """Reads a JSON signal file and places CTP orders based on threshold."""

    author: str = "intraday_alpha_pipeline"

    # ---- Tunable parameters (passed via ``setting`` dict) ----
    fixed_size: int = 1                       # 手数
    signal_threshold_long: float = 0.0008     # signal > +this → 做多
    signal_threshold_short: float = 0.0008    # signal < -this → 做空
    price_add_ticks: int = 2                  # 限价单超出最新价 tick 数
    signal_path: str = "./lab/intraday/signal/intraday_live_signal.json"
    stale_signal_hours: int = 24              # 信号超过 N 小时认为失效

    # ---- Tracked variables ----
    signal_value: float = 0.0
    signal_version: str = ""
    target_pos: float = 0.0
    pricetick: float = 0.0

    # ---- CtaEngine introspection surface ----
    parameters: list[str] = [
        "fixed_size",
        "signal_threshold_long",
        "signal_threshold_short",
        "price_add_ticks",
        "signal_path",
        "stale_signal_hours",
    ]
    variables: list[str] = [
        "signal_value",
        "signal_version",
        "target_pos",
        "pricetick",
    ]

    # -------------------------------------------------------------------------
    # Lifecycle callbacks
    # -------------------------------------------------------------------------
    def on_init(self) -> None:
        """Strategy initialization callback — wire up BarGenerator + signal cache."""
        self.write_log("IntradayAlphaStrategy 初始化")

        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        # Lazy signal index: dt_iso (str) → signal value
        self._signal_index: dict[str, float] = {}
        self._signal_mtime: float = 0.0

        # Resolve pricetick once from the OMS-loaded contract info.
        contract = self.cta_engine.main_engine.get_contract(self.vt_symbol)
        if contract is not None and contract.pricetick:
            self.pricetick = float(contract.pricetick)
        else:
            # Default for SHFE gold if the contract hasn't loaded yet (will retry in on_start)
            self.pricetick = 0.02

        # Pull warm-up bars so on_bar starts inited. SIMNOW supports query_history.
        warmup_days: int = 5
        try:
            self.load_bar(warmup_days)
            self.write_log(f"已加载 {warmup_days} 天 warm-up 历史 K 线")
        except Exception as exc:  # noqa: BLE001
            self.write_log(f"加载 warm-up K 线失败（{exc}），仍可继续运行")

    def on_start(self) -> None:
        """Strategy start callback — refresh signal cache + check staleness."""
        self.write_log("IntradayAlphaStrategy 启动")
        # Re-resolve pricetick now that more contract info may be available.
        contract = self.cta_engine.main_engine.get_contract(self.vt_symbol)
        if contract is not None and contract.pricetick:
            self.pricetick = float(contract.pricetick)
        self._load_signal_file(force=True)

    def on_stop(self) -> None:
        """Strategy stop callback — cancel all open orders and log."""
        self.write_log("IntradayAlphaStrategy 停止")
        self.cancel_all()

    # -------------------------------------------------------------------------
    # Market data callbacks
    # -------------------------------------------------------------------------
    def on_tick(self, tick: TickData) -> None:
        """Tick callback — feed BarGenerator for 1-min bar synthesis."""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """Bar callback — look up signal and place orders if signal crosses threshold."""
        if not self.trading:
            return

        # Refresh signal cache if file mtime changed
        self._load_signal_file(force=False)

        # Key format: ``YYYY-MM-DDTHH:MM:SS`` (matches signal JSON).
        bar_key: str = bar.datetime.strftime("%Y-%m-%dT%H:%M:%S")
        self.signal_value = float(self._signal_index.get(bar_key, 0.0))

        # Decide target position
        if self.signal_value > self.signal_threshold_long:
            self.target_pos = float(self.fixed_size)
        elif self.signal_value < -self.signal_threshold_short:
            self.target_pos = -float(self.fixed_size)
        else:
            self.target_pos = 0.0

        # Skip if position already matches target
        if self.target_pos == self.pos:
            return

        # Cancel any open orders from the previous bar before flipping
        self.cancel_all()

        # Stale-signal guard: refuse to flip direction during a long weekend / holiday
        if self._is_signal_stale():
            self.write_log(f"信号版本 {self.signal_version} 已超过 {self.stale_signal_hours}h，不开新仓")
            return

        order_price: float = round_to(
            bar.close_price + self.price_add_ticks * self.pricetick * (1 if self.target_pos > 0 else -1),
            self.pricetick,
        )

        # Direction transition: 0 → +1 (buy), 0 → -1 (short), ±1 ↔ ∓1 (close+reopen)
        if self.pos == 0 and self.target_pos > 0:
            self.buy(order_price, abs(self.target_pos))
        elif self.pos == 0 and self.target_pos < 0:
            self.short(order_price, abs(self.target_pos))
        elif self.target_pos == 0:
            self._flat(bar.close_price)
        else:
            # Flipping direction: close current then open opposite
            self._flat(bar.close_price)
            # The reverse-open order will be placed on the next bar (signal-driven)

        self.put_event()

    # -------------------------------------------------------------------------
    # Order / trade callbacks — log only
    # -------------------------------------------------------------------------
    def on_order(self, order: OrderData) -> None:
        """Order callback."""
        self.write_log(
            f"[委托] {order.vt_orderid} {order.symbol}.{order.exchange.value} "
            f"{order.direction.value}{order.offset.value} {order.volume}手 @ {order.price} "
            f"status={order.status.value}"
        )

    def on_trade(self, trade: TradeData) -> None:
        """Trade callback."""
        self.write_log(
            f"[成交] {trade.vt_orderid} {trade.symbol}.{trade.exchange.value} "
            f"{trade.direction.value} {trade.volume}手 @ {trade.price}"
        )
        self.put_event()

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _flat(self, ref_price: float) -> None:
        """Close current position at a tick-adjusted limit price."""
        if self.pos == 0:
            return
        order_price: float = round_to(
            ref_price - self.price_add_ticks * self.pricetick * (1 if self.pos > 0 else -1),
            self.pricetick,
        )
        if self.pos > 0:
            self.sell(order_price, abs(self.pos))
        else:
            self.cover(order_price, abs(self.pos))

    def _load_signal_file(self, force: bool) -> None:
        """Load ``signal_path`` if mtime advanced (or ``force=True``)."""
        path: Path = Path(self.signal_path)
        if not path.exists():
            return
        try:
            mtime: float = path.stat().st_mtime
        except OSError:
            return

        if not force and mtime <= self._signal_mtime:
            return

        try:
            with open(path, encoding="UTF-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.write_log(f"读 signal 失败：{exc}")
            return

        self._signal_mtime = mtime
        self._signal_index = {row["datetime"]: float(row["signal"]) for row in payload.get("rows", [])}
        self.signal_version = payload.get("version", "")
        self.write_log(
            f"signal 已刷新，version={self.signal_version}，{len(self._signal_index)} 行"
        )

    def _is_signal_stale(self) -> bool:
        """True if the signal version is older than ``stale_signal_hours``."""
        if not self.signal_version:
            return False
        try:
            ts: datetime = datetime.fromisoformat(self.signal_version.rstrip("Z"))
        except ValueError:
            return False
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        age: float = (datetime.utcnow() - ts).total_seconds() / 3600.0
        return age > self.stale_signal_hours