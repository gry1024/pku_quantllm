"""Intraday alpha research pipeline — orchestrator.

Subcommands
-----------
* ``--ingest-csv <path>``    one-shot: read a CSV of 1-min OHLCV bars and persist
                              it via ``AlphaLab`` at ``lab_path/minute/<vt>.parquet``.
* ``--train`` (default)      build dataset, fit LightGBM, predict every segment,
                              save ``signal.parquet`` + ``live_signal.json``.
* ``--predict-only``         skip training — reuse cached ``model.pkl``.
* ``--backtest`` (default)   run :class:`BacktestingEngine` with
                              :class:`IntradayTopStrategy`.
* ``--no-backtest``          skip the backtest stage.

Examples
--------
Run from this directory (``projects/intraday_alpha/research/``) so the relative
``lab_path`` / ``socket_path`` in ``intraday_alpha/config.json`` resolve to the
right location::

    # Ingest 30 days of 1-min bars from a CSV (datetime, open, high, low, close,
    # volume, turnover, open_interest), then train + backtest + emit live signal
    python run.py --ingest-csv ../../../data/au2612_1min.csv --show-chart

    # Reuse cached model; emit fresh live_signal.json only
    python run.py --predict-only

    # Full pipeline + backtest + chart + benchmark
    python run.py --show-chart --show-performance
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

# Make ``intraday_alpha`` subpackage importable when running from anywhere.
# After the repo reorg, run.py lives at ``projects/intraday_alpha/research/run.py``
# and the package sits one directory deeper at ``.../research/intraday_alpha/``,
# so the *parent* of this file (the research/ dir) is what we want on sys.path.
#
# ``vnpy`` / ``vnpy_ctp`` / ``vnpy_ctastrategy`` are expected to be installed
# editable from raw/ — run ``pip install -e raw/vnpy raw/vnpy_ctp raw/vnpy_ctastrategy``
# once per machine. If they're not, the imports below will fail loudly.
_HERE: Path = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vnpy.alpha import (                       # noqa: E402  (sys.path tweak above)
    AlphaLab,
    BacktestingEngine,
    Segment,
    logger,
)
from vnpy.trader.constant import Interval, Exchange     # noqa: E402
from vnpy.trader.object import BarData         # noqa: E402

from intraday_alpha import (                   # noqa: E402
    IntradayAlphaDataset,
    IntradayLgbModel,
    IntradayTopStrategy,
    load_config,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Intraday alpha research pipeline (data → train → predict → signal → backtest)."
    )
    parser.add_argument("--ingest-csv",  type=str, default=None,
                        help="Path to CSV with columns datetime/open/high/low/close/volume/turnover/open_interest.")
    parser.add_argument("--start", type=str, default=None,
                        help="Override train_period start (YYYY-MM-DD).")
    parser.add_argument("--end",   type=str, default=None,
                        help="Override test_period end (YYYY-MM-DD).")
    parser.add_argument("--predict-only", action="store_true",
                        help="Skip training; reuse cached model.")
    parser.add_argument("--no-backtest",  action="store_true",
                        help="Skip the BacktestingEngine stage.")
    parser.add_argument("--show-chart", action="store_true",
                        help="Render plotly chart after backtest.")
    parser.add_argument("--show-performance", action="store_true",
                        help="Render benchmark comparison after backtest.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------
_REQUIRED_COLS: list[str] = ["datetime", "open", "high", "low", "close",
                             "volume", "turnover", "open_interest"]
_OPTIONAL_VWAP: bool = True  # if no vwap column, derive from turnover/volume


def ingest_csv(lab: AlphaLab, vt_symbol: str, csv_path: Path) -> int:
    """Read a CSV, build BarData objects, persist via ``lab.save_bar_data``.

    Returns the number of bars ingested.
    """
    df: pl.DataFrame = pl.read_csv(csv_path)

    missing: list[str] = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    if "vwap" not in df.columns:
        df = df.with_columns(
            (pl.col("turnover") / pl.col("volume")).alias("vwap")
        )

    df = df.with_columns(pl.col("datetime").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S%.f", strict=False)
                                       .alias("datetime"))
    df = df.drop_nulls(subset=["datetime"]).sort("datetime")

    # ``extract_vt_symbol`` style: pull symbol + exchange from the vt_symbol string
    symbol, exchange_str = vt_symbol.split(".", 1)

    bars: list[BarData] = []
    for row in df.iter_rows(named=True):
        bars.append(BarData(
            symbol=symbol,
            exchange=Exchange(exchange_str),
            datetime=row["datetime"],
            interval=Interval.MINUTE,
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            volume=float(row["volume"]),
            turnover=float(row["turnover"]),
            open_interest=float(row["open_interest"]),
            gateway_name="CSV",
        ))

    lab.save_bar_data(bars)
    logger.info(f"已写入 {len(bars)} 根 1-min bar 到 {lab.minute_path}/{vt_symbol}.parquet")
    return len(bars)


# ---------------------------------------------------------------------------
# Research pipeline
# ---------------------------------------------------------------------------
def build_dataset(lab: AlphaLab, vt_symbol: str, cfg: dict[str, Any]) -> IntradayAlphaDataset:
    """Load bars from AlphaLab, build the dataset, run prepare/process."""
    # ``vnpy.alpha.dataset.utility.to_datetime`` parses ``YYYY-MM-DD`` as midnight;
    # the downstream ``<= end`` filter would then drop every bar after 00:00.
    # Bump each ``end`` to end-of-day so inclusive-date configs work correctly.
    train_period: tuple[str, str] = _inclusive(cfg["train_period"])
    valid_period: tuple[str, str] = _inclusive(cfg["valid_period"])
    test_period:  tuple[str, str] = _inclusive(cfg["test_period"])

    # ``lab.load_bar_df`` has the same midnight issue — bump its end forward
    # too so the last day's bars survive.
    df: pl.DataFrame | None = lab.load_bar_df(
        vt_symbols=[vt_symbol],
        interval=Interval.MINUTE,
        start=cfg["train_period"][0],
        end=_bump_end(cfg["test_period"][1]),
        extended_days=30,
    )
    if df is None or df.is_empty():
        raise RuntimeError(
            f"AlphaLab 在 {lab.minute_path} 下找不到 {vt_symbol} 的 1-min bar。"
            f"先跑 --ingest-csv 灌数据。"
        )

    logger.info(f"加载 {len(df)} 根 1-min bar（{df['datetime'].min()} ~ {df['datetime'].max()}）")

    dataset = IntradayAlphaDataset(
        df=df,
        train_period=train_period,
        valid_period=valid_period,
        test_period=test_period,
    )
    dataset.prepare_data(filters=None, max_workers=4)
    dataset.process_data()
    return dataset


def train_and_predict(
    lab: AlphaLab,
    vt_symbol: str,
    cfg: dict[str, Any],
    dataset: IntradayAlphaDataset,
    predict_only: bool,
) -> pl.DataFrame:
    """Fit (or load) the model and produce a full signal DataFrame.

    The returned frame has columns ``[datetime, vt_symbol, signal]`` covering
    TRAIN + VALID + TEST segments concatenated in order.
    """
    if predict_only:
        model_name: str = "intraday_lgb"
        loaded = lab.load_model(model_name)
        if loaded is None:
            raise RuntimeError(f"--predict-only 但 lab 没有缓存模型 {model_name}")
        model = loaded
        logger.info(f"复用已缓存模型 {model_name}（best_iter={model.best_iteration}）")
    else:
        model = IntradayLgbModel(**cfg["model_hyperparams"])
        model.fit(dataset)
        model_name = "intraday_lgb"
        lab.save_model(model_name, model)
        logger.info(f"模型已训练并缓存到 {lab.model_path}/{model_name}.pkl")

    # Predict on every segment; concatenate; attach datetimes + vt_symbol.
    frames: list[pl.DataFrame] = []
    for seg in [Segment.TRAIN, Segment.VALID, Segment.TEST]:
        preds: np.ndarray = model.predict(dataset, seg)
        df_seg: pl.DataFrame = dataset.fetch_infer(seg).select(["datetime", "vt_symbol"])
        frames.append(df_seg.with_columns(pl.Series("signal", preds)))

    signal_df: pl.DataFrame = pl.concat(frames).sort(["datetime", "vt_symbol"])
    return signal_df


def emit_signal_files(
    lab: AlphaLab,
    cfg: dict[str, Any],
    vt_symbol: str,
    signal_df: pl.DataFrame,
) -> Path:
    """Save signal.parquet (research side) + live_signal.json (live consumer).

    The JSON file is written atomically (tmp + os.replace) to avoid partial
    reads by the live strategy.
    """
    # Parquet for offline reuse
    lab.save_signal("intraday_live_signal", signal_df)

    # JSON for live strategy lookup
    json_path: Path = Path(cfg["signal_path"]).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "version": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "vt_symbol": vt_symbol,
        "rows": [
            {
                "datetime": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "vt_symbol": vs,
                "signal": float(sig),
            }
            for dt, vs, sig in zip(
                signal_df["datetime"].to_list(),
                signal_df["vt_symbol"].to_list(),
                signal_df["signal"].to_list(),
            )
        ],
    }

    tmp_path: Path = json_path.with_suffix(json_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="UTF-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=None)
    os.replace(tmp_path, json_path)
    logger.info(f"live_signal.json 已写入 {json_path}（{len(payload['rows'])} 行）")
    return json_path


def run_backtest(
    lab: AlphaLab,
    vt_symbol: str,
    cfg: dict[str, Any],
    dataset: IntradayAlphaDataset,
    signal_df: pl.DataFrame,
    show_chart: bool,
    show_performance: bool,
) -> dict[str, Any]:
    """Spin up :class:`BacktestingEngine`, run :class:`IntradayTopStrategy`, save stats."""
    engine = BacktestingEngine(lab)

    # Register the contract so commission + size/pricetick lookups succeed.
    contract: dict[str, Any] = cfg["contract"]
    lab.add_contract_setting(
        vt_symbol=vt_symbol,
        long_rate=contract["long_rate"],
        short_rate=contract["short_rate"],
        size=contract["size"],
        pricetick=contract["pricetick"],
    )

    bt_cfg: dict[str, Any] = cfg["backtest"]
    # ``AlphaLab.load_bar_data`` filters ``datetime <= end``; passing the bare
    # ``YYYY-MM-DD`` (parsed as midnight) would drop every bar after 00:00.
    # Bump end forward by 1 day so the inclusive end-day actually gets loaded.
    engine.set_parameters(
        vt_symbols=[vt_symbol],
        interval=Interval.MINUTE,
        start=_to_dt(cfg["test_period"][0]),
        end=_to_dt(cfg["test_period"][1]) + timedelta(days=1),
        capital=int(bt_cfg["capital"]),
        risk_free=float(bt_cfg["risk_free"]),
        annual_days=int(bt_cfg["annual_days"]),
    )

    engine.add_strategy(
        IntradayTopStrategy,
        setting={
            "fixed_size":            cfg["live"]["fixed_size"],
            "signal_threshold_long": cfg["live"]["signal_threshold_long"],
            "signal_threshold_short":cfg["live"]["signal_threshold_short"],
            "price_add":             cfg["live"]["price_add_ticks"] * contract["pricetick"],
        },
        signal_df=signal_df,
    )

    engine.load_data()
    engine.run_backtesting()
    daily_df: pl.DataFrame | None = engine.calculate_result()

    runs_dir: Path = _HERE / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Persist the daily PnL frame (raw truth) regardless of stats outcome.
    if daily_df is not None:
        daily_path: Path = runs_dir / "intraday_lgb_daily_pnl.parquet"
        daily_df.write_parquet(daily_path)
        logger.info(f"逐日 PnL 已写入 {daily_path}（{daily_df.height} 行）")

    # ``calculate_statistics`` raises on degenerate inputs (single-day
    # synthetic data has ``max_drawdown=0`` causing div-by-zero; multi-day
    # data with all-zero returns has ``return.std()=None``). We guard so the
    # pipeline still emits ``daily_pnl.parquet`` + ``live_signal.json``.
    try:
        stats: dict[str, Any] = engine.calculate_statistics()
    except (ZeroDivisionError, TypeError) as exc:
        logger.info(f"统计计算跳过（{type(exc).__name__}: {exc}）——数据维度不足")
        stats = {
            "trade_count": len(engine.trades),
            "order_count": len(engine.limit_orders),
            "skip_reason": str(exc),
        }

    stats_path: Path = runs_dir / "intraday_lgb_statistics.json"
    with open(stats_path, "w", encoding="UTF-8") as f:
        json.dump(stats, f, indent=4, ensure_ascii=False)
    logger.info(f"回测统计已写入 {stats_path}")

    if show_chart:
        engine.show_chart()
    if show_performance:
        engine.show_performance(vt_symbol)

    return stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_dt(s: str) -> datetime:
    """Parse YYYY-MM-DD into naive datetime (consistent with AlphaLab's storage)."""
    return datetime.strptime(s, "%Y-%m-%d")


def _inclusive(period: list[str]) -> tuple[str, str]:
    """Convert ``[YYYY-MM-DD, YYYY-MM-DD]`` to a tuple covering the inclusive end-day.

    ``vnpy.alpha.dataset.utility.to_datetime`` parses ``YYYY-MM-DD`` as midnight,
    so a naive ``<= end`` filter would exclude every bar after 00:00 on the
    end date. We bump ``end`` forward by 1 day so the inclusive-day semantics
    used in ``config.json`` survive. There is a 1-row boundary overlap with the
    next segment (e.g. Sep 16's midnight bars appear in TRAIN and VALID) — for
    minute bars the overlap is at most a few NaN rows and doesn't materially
    affect model quality. If you need strict separation, pass explicit datetimes
    with a time component here.
    """
    return (period[0], _bump_end(period[1]))


def _bump_end(end_date: str) -> str:
    """Bump a bare ``YYYY-MM-DD`` end date forward by 1 day so downstream
    midnight-parse filters include the whole end-day. Pass-through if the
    user already supplied a time component."""
    if " " in end_date or "T" in end_date:
        return end_date
    return (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the CLI."""
    args = parse_args()
    cfg = load_config()
    vt_symbol: str = cfg["vt_symbol"]
    lab = AlphaLab(cfg["lab_path"])

    # ---- One-shot CSV ingestion ----
    if args.ingest_csv:
        ingest_csv(lab, vt_symbol, Path(args.ingest_csv))

    # ---- Build dataset ----
    dataset = build_dataset(lab, vt_symbol, cfg)

    # ---- Train / predict ----
    signal_df = train_and_predict(lab, vt_symbol, cfg, dataset, predict_only=args.predict_only)
    emit_signal_files(lab, cfg, vt_symbol, signal_df)

    # ---- Backtest ----
    if not args.no_backtest:
        run_backtest(
            lab, vt_symbol, cfg, dataset, signal_df,
            show_chart=args.show_chart,
            show_performance=args.show_performance,
        )


if __name__ == "__main__":
    main()