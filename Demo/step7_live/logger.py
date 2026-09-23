"""Step 7: logger —— 用 loguru 把日志写到 logs/ 目录。

loguru 比 stdlib logging 强的地方：
- 一行 add() 同时写文件 + 控制台 + 按天滚动 + 保留 30 天
- 自动带文件名/行号/异常 traceback
- 比 logging.getLogger() 简单 10 倍

设计：
- logs/run_YYYY-MM-DD.log     —— 主日志（含所有 write_log + 异常）
- logs/trade_YYYY-MM-DD.log   —— 仅成交（trader_recorder 单独 add 一个 sink）

为什么主日志还要单独一份 trade 日志？
- 主日志几十 MB/天，回看慢
- trade 日志只几 KB，专门审计成交
"""

from datetime import datetime
from pathlib import Path

from loguru import logger


_LOGS_DIR = Path(__file__).parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)


def setup_logger() -> "loguru.Logger":
    """初始化全局 logger，返回 logger 对象。"""
    # 1) 移除默认的 stderr sink（loguru 默认会打一份到 stderr，我们用更干净的格式）
    logger.remove()

    # 2) 控制台输出 —— 简洁彩色
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss.SSS}</green> | "
               "<level>{level:<7}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
        level="INFO",
        colorize=False,             # 我们自己 sink 接管，不再二次着色
    )

    # 3) 主日志文件 —— 按天滚动，保留 30 天
    main_log = _LOGS_DIR / f"run_{datetime.now():%Y-%m-%d}.log"
    logger.add(
        sink=main_log,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
               "{name}:{function}:{line} - {message}",
        level="INFO",
        rotation="00:00",          # 每天 0 点切新文件
        retention="30 days",       # 保留 30 天
        encoding="utf-8",
        enqueue=True,              # 多线程安全（EventEngine 也在写日志）
    )

    # 4) 异常日志单独 —— DEBUG 级别以上，含 traceback
    err_log = _LOGS_DIR / f"error_{datetime.now():%Y-%m-%d}.log"
    logger.add(
        sink=err_log,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | "
               "{name}:{function}:{line} - {message}\n{exception}",
        level="WARNING",
        rotation="00:00",
        retention="90 days",       # 异常多保留一些
        encoding="utf-8",
        enqueue=True,
    )

    logger.info(f"=== 日志系统初始化完成 === 主日志={main_log.name} 异常日志={err_log.name}")
    return logger