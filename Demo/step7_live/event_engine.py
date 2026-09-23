"""Step 3 复用 Step 2 的 EventEngine（不变）。"""

import queue
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable


@dataclass
class Event:
    type: str
    data: dict


class EventEngine:
    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._thread: threading.Thread | None = None
        self._active: bool = False

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._thread = threading.Thread(
            target=self._run, name="EventEngine", daemon=True
        )
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        self._active = False
        if drain:
            self._queue.put(_STOP_SENTINEL)
            if self._thread is not None:
                self._thread.join(timeout=2.0)

    def register(self, type_: str, handler: Callable) -> None:
        self._handlers[type_].append(handler)

    def unregister(self, type_: str, handler: Callable) -> None:
        try:
            self._handlers[type_].remove(handler)
        except ValueError:
            pass

    def put(self, event: Event) -> None:
        self._queue.put_nowait(event)

    def _run(self) -> None:
        while self._active:
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if event is _STOP_SENTINEL:
                break
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                print(f"[EventEngine] handler 异常: {type(e).__name__}: {e}")


_STOP_SENTINEL = object()


EVENT_TICK = "eTick"
EVENT_ORDER = "eOrder"
EVENT_TRADE = "eTrade"
EVENT_ACCOUNT = "eAccount"
EVENT_POSITION = "ePosition"
EVENT_LOG = "eLog"