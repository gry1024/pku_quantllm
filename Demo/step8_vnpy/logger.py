"""Step 8: loguru 日志系统。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger


_LOGS_DIR = Path(__file__).parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)


def setup_logger():
    """初始化全局 logger，返回 logger 对象。"""
    logger.remove()

    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss.SSS}</green> | "
               "<level>{level:<7}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
        level="INFO",
        colorize=False,
    )

    main_log = _LOGS_DIR / f"run_{datetime.now():%Y-%m-%d}.log"
    logger.add(
        sink=main_log,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
               "{name}:{function}:{line} - {message}",
        level="INFO",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )

    err_log = _LOGS_DIR / f"error_{datetime.now():%Y-%m-%d}.log"
    logger.add(
        sink=err_log,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
               "{name}:{function}:{line} - {message}\n{exception}",
        level="WARNING",
        rotation="00:00",
        retention="90 days",
        encoding="utf-8",
        enqueue=True,
    )

    logger.info(
        f"=== 日志系统初始化完成 === 主日志={main_log.name} 异常日志={err_log.name}"
    )
    return logger