"""Step 2: 真·EventEngine —— 队列 + 线程 + 异步分发。

与 Step 1 占位版的区别：
  - 同步分发 → 异步分发（handler 跑在 EventEngine 自己的线程上）
  - put() 必须线程安全（被 Gateway 的 C++ 内部回调线程调用）
  - handler 即使 sleep(10)，Gateway 也不会被卡住

调用模型：
  Gateway 回调线程 --put--> _queue --get--> EventEngine 线程 --dispatch--> handlers
"""

import queue
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable


@dataclass
class Event:
    """通用事件：type + data。"""
    type: str
    data: dict


class EventEngine:
    """异步事件总线。"""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._thread: threading.Thread | None = None
        self._active: bool = False

    # ============== 生命周期 ==============
    def start(self) -> None:
        """启动分发线程。重复调用无效。"""
        if self._active:
            return
        self._active = True
        self._thread = threading.Thread(
            target=self._run, name="EventEngine", daemon=True
        )
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        """停止分发线程。

        drain=True  ：等队列里现有事件全部派发完再退出（推荐，用于优雅退出）
        drain=False ：立刻退出，可能丢事件（用于崩溃前的快速收尾）
        """
        self._active = False
        if drain:
            # 塞个 sentinel 让 _run 拿到后跳出循环；sentinel 之前的剩余事件会先派发
            self._queue.put(_STOP_SENTINEL)
            if self._thread is not None:
                self._thread.join(timeout=2.0)

    # ============== 注册 / 注销 ==============
    def register(self, type_: str, handler: Callable) -> None:
        """注册一个 type 上的 handler（同一 type 可注册多个）。"""
        self._handlers[type_].append(handler)

    def unregister(self, type_: str, handler: Callable) -> None:
        """注销 handler；找不到也忽略。"""
        try:
            self._handlers[type_].remove(handler)
        except ValueError:
            pass

    # ============== 投递 ==============
    def put(self, event: Event) -> None:
        """把事件塞进队列（非阻塞）。可被任意线程调用。"""
        self._queue.put_nowait(event)

    # ============== 内部 ==============
    def _run(self) -> None:
        """分发线程主循环。"""
        while self._active:
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if event is _STOP_SENTINEL:
                break
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        """把一个事件分发给所有已注册 handler；handler 异常不能挂掉分发线程。"""
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                print(f"[EventEngine] handler 异常: {type(e).__name__}: {e}")


# 内部 sentinel：让 stop(drain=True) 能干净退出
_STOP_SENTINEL = object()


# 预定义事件类型常量
EVENT_TICK = "eTick"
EVENT_ORDER = "eOrder"
EVENT_TRADE = "eTrade"
EVENT_ACCOUNT = "eAccount"
EVENT_POSITION = "ePosition"
EVENT_LOG = "eLog"