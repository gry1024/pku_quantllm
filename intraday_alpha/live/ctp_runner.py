"""Headless CTP live runner for the intraday alpha pipeline.

Reads CTP credentials from environment variables (or ``.env`` via
``python-dotenv``), wires up :class:`MainEngine` + :class:`CtpGateway` +
:class:`CtaStrategyApp`, then runs :class:`strategy.intraday_alpha_strategy.IntradayAlphaStrategy`
against ``au2612.SHFE`` (default) on SIMNOW real-trading session.

Required env vars (see ``../.env.example``): ``CTP_TD_ADDRESS``, ``CTP_MD_ADDRESS``,
``CTP_USERID``, ``CTP_PASSWORD``, ``CTP_BROKERID``, ``CTP_APPID``,
``CTP_AUTH_CODE``, ``CTP_ENV`` (default ``实盘``), ``INTRADAY_VT_SYMBOL``,
``INTRADAY_SIGNAL_PATH``.

Usage::

    # From this directory:
    python ctp_runner.py        # Ctrl+C for graceful shutdown

The ``.env`` file lives one directory up (sibling of this ``live/`` dir) so a
single ``.env`` covers both the research and live runners::

    projects/intraday_alpha/
    ├── .env                    # created by `cp .env.example .env`
    ├── live/ctp_runner.py      # you are here
    └── research/run.py
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from time import sleep

# Make the strategy module discoverable by name (CtaStrategyApp looks up
# ``class_name="IntradayAlphaStrategy"`` on sys.path).
_HERE: Path = Path(__file__).resolve().parent
_STRATEGY_DIR = _HERE / "strategy"
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))

# Optional .env loading — graceful if python-dotenv isn't installed.
# ``.env`` lives at the repo root (sibling of this ``live/ctp_runner.py``'s
# parent's parent). The same root .env is also used by ctp_demo.py and
# ctp_breakout_demo.py — keep one copy of credentials.
try:
    from dotenv import load_dotenv
    load_dotenv(_HERE.parent.parent / ".env", override=False)
except ImportError:
    pass


_REQUIRED_ENV: list[str] = [
    "CTP_USERID", "CTP_PASSWORD", "CTP_BROKERID",
    "CTP_TD_ADDRESS", "CTP_MD_ADDRESS",
    "CTP_APPID", "CTP_AUTH_CODE",
]


def _require_env() -> dict[str, str]:
    """Validate that every required CTP env var is set; raise if missing."""
    cfg: dict[str, str] = {}
    missing: list[str] = []
    for key in _REQUIRED_ENV:
        value: str | None = os.environ.get(key)
        if not value:
            missing.append(key)
        else:
            cfg[key] = value
    if missing:
        raise RuntimeError(
            "缺少必需的 CTP 环境变量：" + ", ".join(missing) +
            "。请复制 .env.example 为 .env 并填入（或 export 后再启动）。"
        )
    return cfg


def _build_ctp_setting(env: dict[str, str]) -> dict[str, str]:
    """Map our env-var names to the Chinese-keyed dict CtpGateway.connect() expects."""
    return {
        "用户名":     env["CTP_USERID"],
        "密码":       env["CTP_PASSWORD"],
        "经纪商代码": env["CTP_BROKERID"],
        "交易服务器": env["CTP_TD_ADDRESS"],
        "行情服务器": env["CTP_MD_ADDRESS"],
        "产品名称":   env["CTP_APPID"],
        "授权编码":   env["CTP_AUTH_CODE"],
        "柜台环境":   os.environ.get("CTP_ENV", "实盘"),
    }


def main() -> None:
    """Wire up engines and enter the main loop."""
    # Defer vnpy imports until after env is loaded so we can fail fast.
    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine
    from vnpy_ctp import CtpGateway
    from vnpy_ctastrategy import CtaStrategyApp

    env = _require_env()
    vt_symbol: str   = os.environ.get("INTRADAY_VT_SYMBOL", "au2612.SHFE")
    # Signal file path: research/run.py writes ``./lab/intraday/signal/intraday_live_signal.json``
    # relative to its own CWD (``intraday_alpha/research/``). Live reads from
    # the same path; override via INTRADAY_SIGNAL_PATH if your lab lives elsewhere.
    default_signal_path: str = str(
        _HERE.parent / "research" / "lab" / "intraday" / "signal" / "intraday_live_signal.json"
    )
    signal_path: str = os.environ.get("INTRADAY_SIGNAL_PATH", default_signal_path)
    strategy_name: str = "intraday_alpha_01"

    event_engine = EventEngine()
    main_engine  = MainEngine(event_engine)
    main_engine.add_gateway(CtpGateway)
    cta_engine = main_engine.add_app(CtaStrategyApp)

    ctp_setting = _build_ctp_setting(env)

    print(f"[init] connecting CTP gateway…", flush=True)
    main_engine.connect(ctp_setting, "CTP")

    # Give the async handshake time to authenticate + log in + load contracts.
    print(f"[init] waiting 12s for connection + contract download…", flush=True)
    sleep(12.0)

    print(f"[init] initializing CTA engine…", flush=True)
    cta_engine.init_engine()
    sleep(2.0)

    print(f"[init] adding strategy {strategy_name} for {vt_symbol}…", flush=True)
    cta_engine.add_strategy(
        class_name="IntradayAlphaStrategy",
        strategy_name=strategy_name,
        vt_symbol=vt_symbol,
        setting={
            "fixed_size":             int(os.environ.get("INTRADAY_FIXED_SIZE", "1")),
            "signal_threshold_long":  float(os.environ.get("INTRADAY_THRESHOLD_LONG",  "0.0008")),
            "signal_threshold_short": float(os.environ.get("INTRADAY_THRESHOLD_SHORT", "0.0008")),
            "price_add_ticks":        int(os.environ.get("INTRADAY_PRICE_ADD_TICKS", "2")),
            "signal_path":            signal_path,
            "stale_signal_hours":     int(os.environ.get("INTRADAY_STALE_HOURS",     "24")),
        },
    )

    init_future = cta_engine.init_strategy(strategy_name)
    init_future.result()  # block until warm-up bars loaded
    cta_engine.start_strategy(strategy_name)

    print(f"[live] strategy started — entering main loop (Ctrl+C to stop)", flush=True)

    # Graceful shutdown via SIGINT / SIGTERM
    shutdown_requested: list[bool] = [False]

    def _request_shutdown(signum: int, frame: object) -> None:  # noqa: ARG001
        shutdown_requested[0] = True
        print(f"\n[shutdown] signal {signum} received, stopping strategy…", flush=True)

    signal.signal(signal.SIGINT,  _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    while not shutdown_requested[0]:
        sleep(5.0)

    # Stop strategy first (cancels open orders), then close engine (closes gateway).
    try:
        cta_engine.stop_strategy(strategy_name)
    except Exception as exc:  # noqa: BLE001
        print(f"[shutdown] stop_strategy error: {exc}", flush=True)

    main_engine.close()
    print("[shutdown] done.", flush=True)


if __name__ == "__main__":
    main()