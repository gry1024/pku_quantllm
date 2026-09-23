"""Step 1 占位 EventEngine —— 仅 list of handlers，先把"数据流过 EventEngine"这件事跑通。

Step 2 会替换为真·EventEngine（队列 + 线程 + 异步分发），现在能跑就行。
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable


@dataclass
class Event:
    """通用事件：type + data。"""
    type: str
    data: dict


class EventEngine:
    """最小占位：register / put，在调用线程同步分发。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def register(self, type_: str, handler: Callable) -> None:
        """注册一个 type 上的 handler（同一 type 可注册多个）。"""
        self._handlers[type_].append(handler)

    def put(self, event: Event) -> None:
        """发出事件：同步调用所有注册的 handler。"""
        for handler in self._handlers.get(event.type, []):
            handler(event)


# 预定义事件类型常量
EVENT_TICK = "eTick"
EVENT_ORDER = "eOrder"
EVENT_TRADE = "eTrade"
EVENT_ACCOUNT = "eAccount"
EVENT_POSITION = "ePosition"
EVENT_LOG = "eLog"