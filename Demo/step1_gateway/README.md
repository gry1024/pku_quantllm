# Step 1 ｜ CtpGateway —— 把 CTP API 包成"通道对象"

> 对应 `Demo/learn.md` Step 1。

## 实现了什么

```text
Demo/step1_gateway/
├── event_engine.py   # 占位版 EventEngine（同步分发，仅够跑通数据流）
├── ctp_gateway.py    # CtpGateway 类 + 内部 _TdApi / _MdApi
└── main.py           # 入口：注册 handler → gateway.connect() → 行情/资金/持仓打印
```

| 文件 | 关键内容 |
|---|---|
| `event_engine.py` | `Event`（type+data）和 `EventEngine`（register / put）；同步分发，先凑合用 |
| `ctp_gateway.py` | `CtpGateway` 对外暴露 5 个动作（connect / close / subscribe / send_order / cancel_order / query_*）；内部仍用 `SimpleTdApi / SimpleMdApi` 但回调里不再 `print`，改成 `gateway.emit(type, data)` |
| `main.py` | 实例化 `EventEngine + CtpGateway`；把 `on_tick / on_account / on_position / on_log` 4 个 handler 注册到 EventEngine；主循环每 5 秒查一次资金/持仓 |

## 理解了什么

- **Gateway = 一条通道的封装**：CTP / IB / 老虎 / 币安都是 Gateway 类，遵守同一套接口约定
- **Gateway 不关心"上层怎么用"**：它只负责 **CTP 协议 ↔ 通用事件** 的双向翻译
- **回调线程 ≠ 主线程**：CTP 回调在 vnpy_ctp 的 C++ 内部线程触发，handler 不应该假设运行在主线程
- **emit 模式替代 print**：回调里只 emit 事件，不做任何业务逻辑；业务逻辑由订阅者处理
- **状态机下沉到 Gateway**：`login_status / settlement_confirmed` 这些守卫位放在 Gateway 内部，上层不必操心

## 有什么用

任何上层（UI / 策略 / 记录器 / 风控）都能订阅同一份事件流；上层不直接碰 CTP API 也能完成所有操作。这是把"通道"和"业务"解耦的第一步，之后接多 Gateway / 多策略 / 多 UI 都靠这个抽象。

## 运行

```bash
cd ~/quantllm/Demo/step1_gateway && python main.py
```

期望：TD / MD 登录成功 → 行情每 ~5s 一行（`最新 / 买一 / 卖一`）→ 资金/持仓每 5s 一次。`/dev/mem` 三连警告无害。