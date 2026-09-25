"""Single-instrument signal consumer for backtesting.

Reads the live signal at each bar slice (provided by
:class:`vnpy.alpha.strategy.BacktestingEngine` via ``self.get_signal()``) and
flips between ``+fixed_size`` / ``-fixed_size`` / ``0`` based on threshold
crossings. Intentionally simpler than
:class:`vnpy.alpha.strategy.strategies.equity_demo_strategy.EquityDemoStrategy`:
single symbol, no TopK ranking, no minimum-hold-days.
"""
from __future__ import annotations

import polars as pl

from vnpy.alpha import AlphaStrategy
from vnpy.trader.object import BarData, TradeData


class IntradayTopStrategy(AlphaStrategy):
    """Single-instrument on/off strategy driven by the model signal."""

    # ---- Tunable parameters (set via ``setting`` dict passed at construction) ----
    fixed_size: int = 1                # Position size when entering a directional trade
    signal_threshold_long: float = 0.0008   # Predicted 5-min return &gt; this → go long
    signal_threshold_short: float = 0.0008  # Predicted 5-min return &lt; -this → go short
    price_add: float = 0.002           # Price slippage buffer for limit orders

    def on_init(self) -> None:
        """Strategy initialization callback."""
        self.write_log("IntradayTopStrategy 初始化")

    def on_trade(self, trade: TradeData) -> None:
        """Trade execution callback — log fills for off-session review."""
        self.write_log(
            f"[成交] {trade.vt_symbol} "
            f"{'买' if trade.direction.value == '多' else '卖'}"
            f"{trade.volume}手 @ {trade.price}"
        )

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K-line slice callback — fires once per bar across the universe."""
        # The signal df is a polars frame with columns [datetime, vt_symbol, signal],
        # filtered to ``self.strategy_engine.datetime`` (the bar being processed).
        signal_df = self.get_signal()
        if signal_df.is_empty():
            return

        # Single-instrument case: vt_symbol comes from the strategy engine.
        vt_symbol: str = self.vt_symbols[0]
        bar: BarData | None = bars.get(vt_symbol)
        if bar is None:
            return

        # Pull the latest signal value for our symbol.
        row = signal_df.filter(pl.col("vt_symbol") == vt_symbol)
        if row.is_empty():
            return
        signal_value: float = row["signal"][0]

        # Translate signal into a discrete target.
        if signal_value > self.signal_threshold_long:
            target: float = float(self.fixed_size)
        elif signal_value < -self.signal_threshold_short:
            target: float = -float(self.fixed_size)
        else:
            target: float = 0.0

        self.set_target(vt_symbol, target)
        self.execute_trading(bars, price_add=self.price_add)