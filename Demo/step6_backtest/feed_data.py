"""Step 6: feed_data - akshare 拉真实 K 线 -> vnpy SQLite.

数据源 fallback：
  1. 新浪 1m - 3-5 个交易日 (SSL 容易 Python 3.13 报错)
  2. 新浪日线 - 全历史 (首选)
  3. 东方财富日线 - 全历史 (sina 挂了自动 fallback)
"""

import argparse
import logging
import time
from typing import Callable

import akshare as ak
import pandas as pd
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from vnpy.trader.constant import Exchange, Interval as VnInterval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData


DEFAULT_SYMBOL = "AU2606"
EXCHANGE = Exchange.SHFE


def _patch_requests() -> None:
    """给 requests 加 5 次重试 + 关证书告警，规避 Python 3.13 + urllib3 + sina 老 SSL 问题。"""
    import requests

    retry = Retry(
        total=5, backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _orig_init = requests.Session.__init__

    def _patched(self, *a, **kw):
        _orig_init(self, *a, **kw)
        self.mount("https://", adapter)
        self.mount("http://", adapter)
        self.verify = False

    requests.Session.__init__ = _patched


logging.getLogger("akshare").setLevel(logging.WARNING)


def _try_fetch(name, fn):
    try:
        df = fn()
        if df is not None and not df.empty:
            print(f"[feed_data] OK {name} 拉到 {len(df)} 行")
            return df
        print(f"[feed_data] WARN {name} 返回空")
        return None
    except Exception as e:
        print(f"[feed_data] FAIL {name}: {type(e).__name__}: {str(e)[:120]}")
        return None


def fetch_minute_bars(symbol, period="1"):
    df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_daily_bars(symbol):
    df = ak.futures_zh_daily_sina(symbol=symbol)
    date_col = "date" if "date" in df.columns else "日期"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.rename(columns={date_col: "datetime"})
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_daily_em(symbol):
    df = ak.futures_hist_em(symbol=symbol, period="daily")
    rename = {
        "时间": "datetime", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume", "持仓量": "hold",
    }
    df = df.rename(columns=rename)
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.date
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def df_to_bars(df, symbol, interval):
    bars = []
    for _, row in df.iterrows():
        dt = row["datetime"]
        if hasattr(dt, "to_pydatetime"):
            dt = dt.to_pydatetime()
        bar = BarData(
            symbol=symbol, exchange=EXCHANGE, datetime=dt, interval=interval,
            volume=float(row.get("volume", 0) or 0),
            turnover=0.0,
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            open_interest=float(row.get("hold", 0) or 0),
        )
        bars.append(bar)
    return bars


def save_to_db(bars, interval):
    db = get_database()
    if bars:
        db.delete_bar_data(bars[0].symbol, EXCHANGE, interval)
    db.save_bar_data(bars)
    return len(bars)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--interval", default="1m",
                        choices=["1m", "5m", "15m", "30m", "60m", "1d"])
    args = parser.parse_args()

    print(f"[feed_data] 合约={args.symbol} 周期={args.interval}")
    _patch_requests()

    df = None
    if args.interval == "1d":
        df = _try_fetch("sina 日线", lambda: fetch_daily_bars(args.symbol))
        if df is None:
            time.sleep(2)
            df = _try_fetch("东财日线", lambda: fetch_daily_em(args.symbol))
        vn_interval = VnInterval.DAILY
    else:
        period = args.interval[:-1]
        df = _try_fetch("sina 1m", lambda: fetch_minute_bars(args.symbol, period))
        vn_interval = VnInterval.MINUTE

    if df is None:
        print()
        print("[feed_data] 全部数据源失败，请检查：")
        print("  1) curl -I 'https://www.baidu.com' 看网络")
        print("  2) Python 3.13 太新，akshare 偶尔 SSL 出错")
        print("  3) 换日线：python feed_data.py --interval 1d")
        print("  4) 换合约：python feed_data.py --symbol AU2506")
        print("  5) 用 proxy：export https_proxy=http://127.0.0.1:7890")
        return

    print(f"[feed_data] 时间 {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"[feed_data] 价格 {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    print(f"[feed_data] 总成交 {df['volume'].sum():.0f}")

    bars = df_to_bars(df, args.symbol, vn_interval)
    n = save_to_db(bars, vn_interval)
    print(f"[feed_data] 写入 vnpy SQLite 完成 {n} 条")

    for o in get_database().get_bar_overview():
        if o.symbol == args.symbol and o.exchange == EXCHANGE and o.interval == vn_interval:
            print(f"[feed_data] 已确认 {o.symbol}.{o.exchange.value} "
                  f"[{o.interval.value}] 共 {o.count} 条 {o.start} ~ {o.end}")


if __name__ == "__main__":
    main()
