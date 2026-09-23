# Step 3 ｜ MainEngine —— Gateway 容器 + 高层交易 API

> 对应 `Demo/learn.md` Step 3。

## 实现了什么

```text
Demo/step3_main/
├── event_engine.py   # 复用 Step 2（不变）
├── ctp_gateway.py    # 复用 Step 2（不变，证明 Gateway 与 MainEngine 解耦）
├── main_engine.py    # ← 新增 MainEngine
└── main.py           # ← 通过 me.send_order("au2612.SHFE", ...) 演示端到端下单
```

| 文件 | 关键内容 |
|---|---|
| `main_engine.py` | `MainEngine` 持有 `EventEngine + dict[name, Gateway]`；提供 `add_gateway / get_gateway / connect / close / subscribe / send_order / cancel_order / query_account / query_position / write_log`；`vt_symbol = "合约.交易所"` 解析内置；默认 gateway_name = "CTP" |
| `main.py` | `me = MainEngine(ee)` → `me.add_gateway("CTP", CtpGateway(ee, SETTING))` → `me.connect("CTP")` → `me.subscribe("CTP", ["au2612.SHFE"])`；5 秒后自动 `me.send_order("au2612.SHFE", "0", "0", last+1.0, 1)`；新增 `on_order / on_trade` 两个 handler |

## 理解了什么

- **MainEngine = Gateway 容器 + 高层 API**：上层永远 `me.xxx(...)`，不直接 `import CtpGateway`
- **vt_symbol = "合约.交易所"**：vnpy 内部的全局唯一 ID 约定（如 `au2612.SHFE`、`rb2501.DCE`），把"合约"和"它在哪个交易所"绑成一个字符串，便于跨 Gateway 路由
- **Gateway 名字作为路由 key**：当前只有 "CTP"，但加 IB / 老虎 / 币安时只要 `me.add_gateway("IB", ...)`，调用方代码不变
- **MainEngine 不持有业务状态**：`login_status / settlement_confirmed / 持仓` 都在 Gateway 和策略里，MainEngine 只是路由 + 翻译
- **Gateway 代码 0 行变动**：从 Step 2 到 Step 3，Gateway 一个字都没改 —— 这是 Gateway / EventEngine / MainEngine 三层解耦的直接回报

## 有什么用

- **多 Gateway 一行接入**：以后想同时跑 CTP + IB，调用方代码完全不变，只是 `me.add_gateway("IB", IbGateway(...))` 多注册一个
- **策略不再 import Gateway**：策略层只调 `me.send_order(...)`，换底层通道（CTP → openctp → 仿真盘）不影响策略
- **统一的 LOG 入口**：`me.write_log("xxx")` 经过 EventEngine，方便以后接 GUI 日志面板 / 文件落盘
- **端到端下单流程闭环**：本步骤通过 main.py 的 demo 自动下单 + on_order / on_trade handler，**验证了 send_order 真的能穿过 MainEngine → Gateway → CTP → 柜台 → 回来推送**

## 运行

```bash
cd ~/quantllm/Demo/step3_main && python main.py
```

**关键验证**（按时间顺序看）：

| 时机 | 期望日志 |
|---|---|
| 启动后 1-3 秒 | `已注册网关: CTP` → `网关 CTP 已发起连接` → TD/MD 登录成功 |
| 行情到达 | `[thread=EventEngine] TICK au2612 ...` |
| 5 秒后首 tick 后 | `>>> demo 自动买开 1 手 @ xxx ref=1` |
| 紧接着 | `[thread=EventEngine] ORDER au2612 买 ref=1 状态=3 ...` |
| simnow 撮合后 | `[thread=EventEngine] TRADE au2612 买 1手 @ xxx ...` |
| 5 秒一次 | `ACCOUNT ...` + `POSITION ...` |

## Step 2 → Step 3 变化点

| 项 | Step 2 | Step 3 |
|---|---|---|
| 上层 API | 直接用 `gateway.send_order(...)` | 用 `me.send_order("au2612.SHFE", ...)` |
| 合约标识 | 裸字符串 `"au2612"` + `"SHFE"` 两个参数 | vt_symbol `"au2612.SHFE"` 一个字符串 |
| Gateway 管理 | main.py 自己持有 gateway 实例 | MainEngine 统一管理，多 Gateway 友好 |
| LOG 入口 | `print` / `gateway.emit(EVENT_LOG, ...)` | `me.write_log(msg)` 统一 |
| Gateway 改动 | — | **0 行** |
| EventEngine 改动 | — | **0 行** |

## 设计上的"还没做"

- 没有 `register_engine / get_engine`：CTA / 风控 / 数据 这些**子引擎**还没登场（Step 5 才接 CtaEngine）
- 没有 `vt_orderid` 全局化：`send_order` 返回的还是 Gateway 内部的 `OrderRef`，没拼成 `FrontID_SessionID_OrderRef` —— 这是 Step 3 简化版，cancel_order 需要手拼 vt_orderid
- 没接 `CtaStrategy`：策略层尚未存在，所有"业务"还在 main.py 里 —— Step 4 解决