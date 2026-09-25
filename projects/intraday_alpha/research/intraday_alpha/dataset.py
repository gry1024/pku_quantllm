"""Minute-bar factor set for single-instrument intraday alpha.

Inherits :class:`vnpy.alpha.AlphaDataset` and registers ~30 minute-bar-appropriate
features (a subset of Alpha158 scaled down to the minute-bar regime) plus three
precomputed intraday columns (``tod_sin``, ``tod_cos``, ``dow``).

Label is the 5-minute forward return. ``ts_delay(..., -N)`` is only used for the
label — every feature uses non-negative lags to prevent look-ahead bias.
"""
from __future__ import annotations

import polars as pl

from vnpy.alpha import AlphaDataset
from vnpy.alpha.dataset import (
    process_drop_na,
    process_fill_na,
    process_replace_inf,
)


class IntradayAlphaDataset(AlphaDataset):
    """Intraday factor set on 1-minute bars (single instrument)."""

    # Rolling windows tuned for 1-min bars (5/15/30/60 min)
    _WINDOWS: list[int] = [5, 15, 30, 60]

    def __init__(
        self,
        df: pl.DataFrame,
        train_period: tuple[str, str],
        valid_period: tuple[str, str],
        test_period: tuple[str, str]
    ) -> None:
        """Constructor — registers features + label + processors."""
        super().__init__(
            df=df,
            train_period=train_period,
            valid_period=valid_period,
            test_period=test_period,
        )

        # ---- K-line shape (5) ----
        self.add_feature("kmid",  "(close - open) / open")
        self.add_feature("klen",  "(high - low) / open")
        self.add_feature("kmid2", "(close - open) / (high - low + 1e-12)")
        self.add_feature("kup",   "(high - ts_greater(open, close)) / open")
        self.add_feature("klow",  "(ts_less(open, close) - low) / open")

        # ---- Price ratios (4) ----
        self.add_feature("open_c",  "open / close")
        self.add_feature("high_c",  "high / close")
        self.add_feature("low_c",   "low / close")
        self.add_feature("vwap_c",  "vwap / close")

        # ---- Micro-price deviation (1) ----
        self.add_feature("vwap_dev", "(close - vwap) / close")

        # ---- Returns / means / std / RSV over rolling windows ----
        for w in self._WINDOWS:
            self.add_feature(f"roc_{w}",   f"ts_delay(close, {w}) / close")
            self.add_feature(f"ma_{w}",    f"ts_mean(close, {w}) / close")
            self.add_feature(f"std_{w}",   f"ts_std(close, {w}) / close")
            self.add_feature(f"rsv_{w}",   f"(close - ts_min(low, {w})) / (ts_max(high, {w}) - ts_min(low, {w}) + 1e-12)")

        # ---- Volume microstructure ----
        self.add_feature("vma_5",      "ts_mean(volume, 5) / (volume + 1e-12)")
        self.add_feature("vma_30",     "ts_mean(volume, 30) / (volume + 1e-12)")
        self.add_feature("vstd_30",    "ts_std(volume, 30) / (volume + 1e-12)")
        self.add_feature(
            "vol_z_30",
            "(volume - ts_mean(volume, 30)) / (ts_std(volume, 30) + 1e-12)"
        )

        # ---- Precomputed intraday time features ----
        # AlphaDataset's expression DSL has no datetime-arithmetic ops, so we
        # compute tod_sin/tod_cos/dow from the input df and register them as
        # precomputed feature_results (left-joined on datetime/vt_symbol).
        self._register_intraday_extras(df)

        # ---- Label: 5-min forward return (the ONLY forward-looking expression) ----
        self.set_label("ts_delay(close, -5) / close - 1")

        # ---- Processors ----
        # learn path drops NaN rows (training-time); infer path fills with 0
        # so a single future bar never gets silently dropped. Both replace ±inf
        # with the column mean to keep LightGBM finite.
        self.add_processor("learn", process_replace_inf)
        self.add_processor("learn", process_drop_na)
        self.add_processor("infer", process_replace_inf)
        self.add_processor("infer", lambda df: process_fill_na(df, fill_value=0.0, fill_label=False))

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _register_intraday_extras(self, df: pl.DataFrame) -> None:
        """Build tod_sin / tod_cos / dow as precomputed feature_results.

        Each precomputed frame has columns ``[datetime, vt_symbol, data]`` and
        gets renamed to the feature name during ``prepare_data()``.
        """
        dt = pl.col("datetime")
        seconds_in_day: int = 24 * 60 * 60
        # Use polars datetime accessors on the source df
        seconds: pl.Expr = (
            dt.dt.hour().cast(pl.Int32) * 3600
            + dt.dt.minute().cast(pl.Int32) * 60
            + dt.dt.second().cast(pl.Int32)
        )
        tod_rad: pl.Expr = (seconds.cast(pl.Float64) * (2 * 3.141592653589793 / seconds_in_day))

        extras: dict[str, pl.DataFrame] = {
            "tod_sin": df.select([
                pl.col("datetime"),
                pl.col("vt_symbol"),
                tod_rad.sin().alias("data"),
            ]),
            "tod_cos": df.select([
                pl.col("datetime"),
                pl.col("vt_symbol"),
                tod_rad.cos().alias("data"),
            ]),
            "dow": df.select([
                pl.col("datetime"),
                pl.col("vt_symbol"),
                dt.dt.weekday().cast(pl.Float64).alias("data"),
            ]),
        }
        for name, frame in extras.items():
            self.add_feature(name=name, result=frame)