"""Convert local qlib binary data → AlphaLab parquet format.

qlib daily format (per `calendars/day.txt`):
  features/<code>/{open,high,low,close,volume,change,factor}.day.bin
  每个文件 = (N+1) 个 float32，第一个值（index 0）是 0 占位，
  index i (i>=1) 对应 calendars[i-1]。

AlphaLab daily parquet:
  columns: datetime, open, high, low, close, volume, turnover, open_interest
  values are raw (脚本会自己再归一化)；我们直接把 qlib 的归一化价当成原始价传入，
  vnpy.alpha.load_bar_df 会再除以第一根 close，结果跟原 qlib 等价。
"""
from __future__ import annotations

import re
import shelve
import struct
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
QLIB_ROOT = REPO_ROOT / "data" / "qlib_data" / "cn_data"
LAB_PATH = REPO_ROOT / "lab" / "csi300"
DAILY_DIR = LAB_PATH / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)
COMP_DIR = LAB_PATH / "component"
COMP_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_FILES = ["open", "high", "low", "close", "volume"]


def load_calendar() -> list[str]:
    with (QLIB_ROOT / "calendars" / "day.txt").open(encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def convert_one(code: str, calendar: list[str]) -> tuple[str, int]:
    """Convert one stock's qlib features → AlphaLab parquet.

    qlib `.day.bin` 格式：
      [start_idx_as_float32, value_day0, value_day1, ...]
      start_idx 标明数据从全局 calendar 的哪个位置开始（int 但存成 float）。
      之后 N 个值是该股从 calendar[start_idx] 起每天的数据。
      股票未上市的天数在数据中就是 NaN（被 AlphaLab 的 mask=0 当作停牌处理）。
    """
    if code.startswith("SH"):
        vt = code[2:] + ".SSE"
        src_dir = QLIB_ROOT / "features" / ("sh" + code[2:])
    elif code.startswith("SZ"):
        vt = code[2:] + ".SZSE"
        src_dir = QLIB_ROOT / "features" / ("sz" + code[2:])
    else:
        return ("bad-code", 0)

    out = DAILY_DIR / f"{vt.split('.')[0]}.parquet"
    if out.exists():
        return ("skip", 0)

    if not src_dir.exists():
        return ("no-src", 0)

    arrays: dict[str, np.ndarray] = {}
    for feat in FEATURE_FILES:
        p = src_dir / f"{feat}.day.bin"
        if not p.exists():
            return ("missing-feat", 0)
        arr = np.frombuffer(p.read_bytes(), dtype=np.float32)
        if len(arr) < 2:
            return (f"too-short:{len(arr)}", 0)
        start_idx = int(arr[0])      # qlib encodes start index as float32
        if start_idx < 0 or start_idx >= len(calendar):
            return (f"bad-start:{start_idx}", 0)
        if len(arr) - 1 + start_idx > len(calendar):
            return (f"overflow:{start_idx}+{len(arr)-1}>{len(calendar)}", 0)
        arrays[feat] = (start_idx, arr[1:])

    # Pick the union date range across all features (some files may have shorter history)
    start_idx = max(s for s, _ in arrays.values())
    end_idx = min(s + len(a) for s, a in arrays.values())

    n = end_idx - start_idx
    if n <= 0:
        return ("no-overlap", 0)

    # Slice each feature to [start_idx, end_idx)
    sliced: dict[str, np.ndarray] = {}
    for feat, (s, a) in arrays.items():
        sliced[feat] = a[s - start_idx : s - start_idx + n]

    df = pl.DataFrame({
        "datetime": [datetime.strptime(d, "%Y-%m-%d") for d in calendar[start_idx:end_idx]],
        "open":     sliced["open"],
        "high":     sliced["high"],
        "low":      sliced["low"],
        "close":    sliced["close"],
        "volume":   sliced["volume"],
        "turnover": (sliced["close"] * sliced["volume"]).astype(np.float64),
        "open_interest": np.zeros(n, dtype=np.float32),
    })
    df.write_parquet(out)
    return ("ok", n)


def main() -> None:
    calendar = load_calendar()
    print(f"Calendar: {len(calendar)} trading days, {calendar[0]} → {calendar[-1]}")

    instruments = (QLIB_ROOT / "instruments" / "csi300.txt").read_text(encoding="utf-8")
    codes = [line.split("\t")[0].strip() for line in instruments.splitlines() if line.strip()]
    print(f"=== Convert {len(codes)} CSI300 stocks (qlib binary → AlphaLab parquet) ===")

    t0 = time.time()
    counts: dict[str, int] = {}
    for i, code in enumerate(codes, 1):
        status, _ = convert_one(code, calendar)
        counts[status] = counts.get(status, 0) + 1
        if i % 50 == 0 or i == len(codes):
            elapsed = time.time() - t0
            eta = elapsed / i * (len(codes) - i)
            print(f"  [{i}/{len(codes)}] {counts}  elapsed={elapsed:.0f}s eta={eta:.0f}s", flush=True)

    print(f"=== Convert done in {time.time() - t0:.0f}s ===")

    # Component shelve: one snapshot date with all available symbols
    available = sorted(p.stem for p in DAILY_DIR.glob("*.parquet"))
    print(f"Building component shelve with {len(available)} symbols")
    import dbm
    with dbm.open(str(COMP_DIR / "000300.SSE"), "c") as db:
        # shelve normally uses pickled keys, but dbm needs bytes
        db[b"2008-01-01"] = ",".join(available).encode("utf-8")
    print("Component shelve saved")


if __name__ == "__main__":
    main()
