# Step 2 ｜ 真·EventEngine —— 队列 + 线程 + 异步分发

> 对应 `Demo/learn.md` Step 2。

## 实现了什么

```text
Demo/step2_event/
├── event_engine.py   # 真·EventEngine：queue.Queue + threading.Thread + 异步分发
├── ctp_gateway.py    # 复用 Step 1（API 不变，证明 Gateway 与 EventEngine 解耦）
└── main.py           # handler 多打印一行 [thread=xxx] 用于验证线程归属
```

| 文件 | 关键内容 |
|---|---|
| `event_engine.py` | `EventEngine` 新增 `start()` / `stop()`；`put()` 改为 `queue.put_nowait`（非阻塞、线程安全）；后台 daemon 线程 `_run()` 死循环 `queue.get` → `_dispatch`；handler 异常被捕获不挂分发线程 |
| `ctp_gateway.py` | **零改动** —— Gateway 只调 `engine.put()`，不关心事件如何分发；这就是 Step 1 设计的好处 |
| `main.py` | 每个 handler 第一行加 `threading.current_thread().name` 打印；启动时 `ee.start()`，退出时 `ee.stop()` |

## 理解了什么

- **回调线程 → 队列 → 分发线程**：CTP 内部线程只做 `put`，handler 永远跑在 EventEngine 这一根线程上 —— 解耦
- **`put()` 必须线程安全**：用 `queue.Queue.put_nowait`，不能用 `list.append`（后者要加锁且性能差）
- **handler 隔离**：一个 handler sleep / 卡住 / 抛异常，不会影响其他 handler，也不会影响 Gateway
- **daemon=True**：分发线程不阻止主进程退出；但生产代码应在退出前显式 `ee.stop()` 把队列 drain 干净
- **同一份 Gateway 代码可移植**：换底层 EventEngine 实现（同步版 → 异步版 → 多线程版 → 分布式版）不影响 Gateway 一行

## 有什么用

- handler 可以放心 `sleep` / 写文件 / 算指标，不会拖垮 Gateway
- 多个 handler 串行派发，**不会**因为某个 handler 慢就丢事件（事件先入队）
- 之后接 CTA 策略、GUI、风控、记录器，它们都是 handler，互不耦合
- 验证方法：每行 `[thread=EventEngine]` —— 全部在 EventEngine 线程上跑

## 运行

```bash
cd ~/quantllm/Demo/step2_event && python main.py
```

**关键验证**：所有行情/资金/持仓/日志行的 `[thread=...]` 前缀都应显示 `EventEngine`，没有 `MainThread` 也没有 vnpy_ctp 的 C++ 线程名。

## Step 1 → Step 2 变化点（对比学习）

| 项 | Step 1 | Step 2 |
|---|---|---|
| 分发 | 调用线程同步 | EventEngine 线程异步 |
| put() | list 遍历分发 | queue.Queue.put_nowait |
| 生命周期 | 无 start/stop | start() / stop(drain=True) |
| Gateway 改动 | — | **0 行** |
| handler sleep | 会阻塞 Gateway | 不阻塞 Gateway |
| handler 异常 | 没人接，挂掉 | 被捕获，不影响分发线程 |