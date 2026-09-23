# Step 7 ｜ 从"能跑"到"挂不掉"：7×24 异常自愈 + supervisor 守护

> Step 5 的 DoubleMaStrategy 已经在 SimNow 上跑通，但 `main.py` 是 demo 模式——Ctrl+C 就退、CTP 断了就死、网络抖一下就崩。真要 7×24 跑交易，必须把"挂了自动爬起来"写到生产前一层。

## 为什么这一步不可跳过？

到 Step 6 结束，你有两个能力：
- Step 5：策略在 SimNow 实时 tick 上做决策
- Step 6：同一策略在真实历史数据上回测，验证过赚不赚钱

**但生产前还差一层"工程化"**：

| 失败场景 | 没做这一步 | 做了这一步 |
|---|---|---|
| CTP 断了 5 分钟 | 程序死、错过这段时间的所有行情 | run.py 主循环捕获异常 → 10s 后重连 |
| 进程被 OOM Killer 杀掉 | 永远挂在那无人值守 | supervisor 立刻拉起 run.py |
| 凌晨 4 点想换日清内存 | 必须手动登录重启 | run.py + cron / supervisor 定时重启 |
| 一笔成交没记上 | 账目对不上 | trade_recorder 订阅 EVENT_TRADE → 写 CSV |
| 日志塞爆磁盘 | 系统卡死 | loguru 按天滚动 + 保留 30 天 |
| 周末 SimNow 关了 | 一堆 ErrorID 警报刷屏 | 周末识别不报警（README 调试手册展开） |

## 这一步我们做了什么？

```text
新增 4 个文件 + 复用 step5 的 7 个：
├── ctp_setting.py        # 端口、broker、SimNow 凭证读法（不写死到 git）
├── logger.py             # loguru：主日志 + 异常日志，按天滚动
├── trade_recorder.py     # EVENT_TRADE → data/trades.csv（追加）
├── run.py                # ★ 生产入口：while-try 异常自愈主循环
├── supervisor.conf       # supervisor / systemd 配置模板
│
├── (复用 step5/)
│   ├── event_engine.py
│   ├── main_engine.py
│   ├── ctp_gateway.py
│   ├── cta_template.py
│   ├── cta_engine.py
│   ├── bar_generator.py
│   └── double_ma_strategy.py
│
└── data/  logs/  ← 运行时生成
    ├── data/trades.csv         ← 累计成交
    └── logs/run_YYYY-MM-DD.log ← 主日志
        logs/error_YYYY-MM-DD.log ← 异常日志
        logs/supervisor.out       ← supervisor stdout
        logs/supervisor.err       ← supervisor stderr
```

## 概念 1 ｜ run.py 的"while-try"是两层防护的内层

```python
def main() -> None:
    while True:                              # ← 外层循环：异常自愈
        parts = {}
        try:
            parts = bootstrap()              #   建 EventEngine + MainEngine + Ctp...
            run_until_dead(parts)            #   阻塞跑直到挂
        except KeyboardInterrupt:
            break                            #   Ctrl+C 直接退
        except Exception as e:
            logger.exception(f"❌ 主循环异常: {e}")
            shutdown(parts)                 #   关掉残余连接
            sleep(10)                       #   防"雪崩"：10 秒后再起
```

**两层防护**：
1. **进程级**（supervisor 解决）：run.py 整个进程死了 → supervisor 立刻拉起新的
2. **异常级**（run.py 内 while-try 解决）：CTP 报错、内存爆炸、handler 抛异常 → 主循环捕获 → 重建整个运行时 → 继续跑

**为什么 sleep 10 秒？** 防止"雪崩"——如果某条路径每 1 秒都触发异常，10 秒间隔能让资源释放、外部服务有时间恢复。

## 概念 2 ｜ bootstrap() 把"建一套"的事装进一个函数

```python
def bootstrap() -> dict:
    ee = EventEngine()
    ee.start()
    me = MainEngine(ee)
    gw = CtpGateway(ee, setting)             # ← 读 ~/quantllm/SIMNOW...txt
    me.add_gateway("CTP", gw)
    me.connect("CTP")
    sleep(10)                                # 等 onFrontConnected + onRspUserLogin
    me.subscribe("CTP", ["au2612.SHFE"])
    cta = CtaEngine(me, ee)
    strategy = DoubleMaStrategy(...)
    cta.add_strategy(strategy)
    cta.init_all()
    cta.start_all()
    recorder = TradeRecorder(ee)
    return {"ee": ee, "me": me, ...}
```

**为什么 sleep(10)** 而不是 `while not connected: sleep(1)`？
- SimNow 7×24 仿真环境连接通常 < 5s 完成
- 严谨做法是订阅 EVENT_LOG 等"CTP 已登录"关键字（30s 超时）
- 当前简化版是"先 sleep 10 再说"——出问题再改

**为什么用 dict 而不是 class**？ 一次性数据，没必要上 class；dict 调试方便（`print(parts.keys())`）。

## 概念 3 ｜ loguru 比 stdlib logging 强在哪

```python
from loguru import logger
logger.remove()                                     # 1) 移除默认 stderr sink
logger.add(sink=stdout, format="...", level="INFO") # 2) 控制台
logger.add("logs/run_{time}.log",
           rotation="00:00",                        # 3) 每天 0 点切新文件
           retention="30 days",                     #    老自动清
           enqueue=True)                             # 4) 跨线程安全
```

对比 stdlib logging 同样需求需要 ~30 行代码（formatter + handler + rotation + thread lock）。

**两个关键点**：
- `enqueue=True` —— 跨线程安全。EventEngine 线程在打日志，主线程也在打日志，不加锁会乱
- `rotation="00:00"` —— 按天切，比按大小切更适合交易系统（按大小切凌晨那次回放会被打断）

## 概念 4 ｜ trade_recorder 是"订阅者"，不是"轮询者"

```python
class TradeRecorder:
    def __init__(self, event_engine):
        self.event_engine = event_engine
        self._ensure_header()                      # CSV 不存在就写表头
        self.event_engine.register(EVENT_TRADE, self._on_trade)

    def _on_trade(self, event):
        d = event.data
        row = {"time": ..., "vt_symbol": ..., "price": d["Price"], ...}
        with _TRADES_CSV.open("a", ...) as f:
            csv.DictWriter(f, _FIELDS).writerow(row)
```

**为什么不每 5 秒轮询查 trade list**？
- 实时性差（最坏 5s 延迟）
- 浪费 CTP 查询额度（QryTrade 有频率限制）
- EventEngine 已经把成交回报推过来了，订阅即可

**为什么用 CSV 不用 SQLite**？
- 7×24 跑一周，trades 也就几百行
- CSV 用 excel/pandas 直接看
- 真量大再换 SQLite（learn.md "下一步" 列了）

## 概念 5 ｜ supervisor 守护进程级异常

`run.py` 解决"运行时异常"——但如果整个 Python 进程被 OOM Killer 杀掉、或者 supervisor 自己重启、或者机器重启，`run.py` 自己也死了。

`supervisor.conf`：

```ini
[program:quantllm]
command=/home/groy/miniconda3/envs/quantllm/bin/python /home/groy/quantllm/Demo/step7_live/run.py
autostart=true
autorestart=true           ; ← 退出后立刻拉起
startretries=10            ; ← 启动失败 10 次后放弃
startsecs=10               ; ← 启动 10s 没死算成功
stdout_logfile=/home/groy/quantllm/Demo/step7_live/logs/supervisor.out
stderr_logfile=/home/groy/quantllm/Demo/step7_live/logs/supervisor.err
```

**用法**：
```bash
sudo apt-get install supervisor
sudo cp supervisor.conf /etc/supervisor/conf.d/quantllm.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status quantllm
# → quantllm: RUNNING
```

**为什么不用 systemd？** supervisor 配置简单、对 Python 友好；systemd 更适合系统服务。

## 完整启动流程

```
supervisord 启动 ──→ quantllm 程序 ──→ run.py main()
                                       │
                                       ▼
                                  while True:
                                       │
                                       ▼
                              bootstrap()  ──→ EventEngine.start
                                       │
                                       ▼
                              CtpGateway.connect (TCP 长连接)
                                       │
                                       ▼
                              onRspUserLogin 回调 → state.logged_in=True
                                       │
                                       ▼
                              CtaEngine.init_all → strategy.on_init
                                       │
                                       ▼
                              CtaEngine.start_all → strategy.on_start
                                       │
                                       ▼
                              TradeRecorder 注册 EVENT_TRADE
                                       │
                                       ▼
                              run_until_dead() ──→ 每 60s 查账户
                                       │
                                       ▼
                          ┌──── 正常 ────┐
                          │  继续循环    │
                          └──────────────┘
                                       │
                          ┌──── 异常 ────┐
                          │  shutdown    │  ← 停策略 + 关网关 + 停 EE
                          │  sleep 10    │
                          │  回到顶部    │
                          └──────────────┘
```

## 跑一下

```bash
# 0) 装依赖（一次性）
pip install loguru

# 1) 确认 SimNow 凭证文件存在
ls ~/quantllm/SIMNOW模拟平台账户密码.txt
# 文件格式：第 1 行 user_id，第 2 行 password，第 3 行 appid，第 4 行 auth_code

# 2) 确认 openctp-ctp 的 .so 在 LD_LIBRARY_PATH 里
echo $LD_LIBRARY_PATH
# 没设就：export LD_LIBRARY_PATH=/path/to/openctp:$LD_LIBRARY_PATH

# 3) 前台启动（看实时日志）
cd ~/quantllm/Demo/step7_live
python run.py

# 4) 后台启动（用 supervisor）
sudo cp supervisor.conf /etc/supervisor/conf.d/quantllm.conf
sudo supervisorctl reread && sudo supervisorctl update
sudo supervisorctl status quantllm
```

按顺序在终端里找这 5 类行：

1. **日志系统初始化**：`=== 日志系统初始化完成 === 主日志=run_2024-xx-xx.log`
2. **bootstrap**：`=== bootstrap 开始 ===` → `=== bootstrap 完成 ===`
3. **CTP 连接**：`CTP 网关已 add + connect` → `策略 double_ma_au 已注册`
4. **心跳**：`[HH:MM:SS] 心跳 ok | 累计 trade 0 笔`（每 60s 一行）
5. **成交**：`data/trades.csv` 多一行；`logs/error_*.log` 没东西

## 试试自己改

### 改动 1 ｜ 启动后立刻验证 trades.csv 表头

第一次跑之前手动检查：

```bash
# 跑 1 分钟，等策略触发或心跳稳定后 Ctrl+C
python run.py
# 看到 "心跳 ok | 累计 trade 0 笔" 后 Ctrl+C

# 检查
cat data/trades.csv
# 应该看到一行 header：
#   time,vt_symbol,direction,offset,price,volume,order_ref,trade_id
```

⚠️ 如果文件不存在或没 header，看 `logs/error_*.log`。

### 改动 2 ｜ 让 run.py 在收到 SIGTERM 时优雅退出

打开 `run.py`，把 `main()` 末尾加一段：

```python
import signal
def _handle_sigterm(signum, frame):
    raise KeyboardInterrupt()       # 走 KeyboardInterrupt 分支
signal.signal(signal.SIGTERM, _handle_sigterm)
```

这样 `supervisorctl stop quantllm` 会优雅退出而不是被 SIGKILL 强杀。

### 改动 3 ｜ 启用"凌晨 4 点主动重启"清理内存

```python
# run.py 顶部
DAILY_RESTART_HOUR = 4

# run_until_dead 里加：
if datetime.now().hour == DAILY_RESTART_HOUR and now - last_query < 60:
    logger.info("每日主动重启 ...")
    raise SystemExit(0)             # 走 - 然后 bootstrap() 重建
```

跑 24h 看 `logs/run_*.log` 里"每日主动重启"出现一次。

## 调试手册（Step 7 专享）

| 故障 | 第一时间检查 |
|---|---|
| `FileNotFoundError: 找不到凭证 ~/quantllm/SIMNOW模拟平台账户密码.txt` | 文件不存在或路径错。手动 cat 1 line 看看是不是空文件 |
| `❌ CTP 连接超时` | `LD_LIBRARY_PATH` 没设；或 openctp-ctp 的 .so 版本不对（见 memory `simnow-ctp-connection.md`） |
| `⚠️ CTP 报"综合交易平台:用户口令错误"` | 凭证文件的密码错了，跟 simnow.com.cn 核对 |
| run.py 启动后 5 秒内连续异常 | 大概率是 DLL 路径问题；检查 `python -c "import vnpy_ctp"` 是否也报同样错 |
| trades.csv 没新增行 | 确认 `me.subscribe("CTP", [VT_SYMBOL])` 真订阅了；策略有没有触发金叉/死叉 |
| 进程被 OOM Killer 杀 | 看 `dmesg \| grep -i oom`；内存泄漏（loguru 不写磁盘可能堆积）；考虑 DAILY_RESTART_HOUR |
| supervisor 启动失败 `startretries=10` 用完 | `supervisorctl tail quantllm` 看 stderr；多半是 command 路径错或权限问题 |
| 周末 SimNow 关了，error 刷屏 | 正常！见 memory；建议加 SimNow 关停时间识别（learn.md 7.2 节） |

## 验收

- [ ] `supervisorctl status quantllm` → `RUNNING`
- [ ] `kill -9 <pid_of_run.py>` → 5s 内 supervisor 拉起新进程，PID 变了
- [ ] `cat data/trades.csv` 至少有 header；跑 30 分钟后即使没成交也至少有心跳日志
- [ ] `logs/run_*.log` 文件名带当天日期；超过 30 天自动清
- [ ] `tail -f logs/run_*.log` 实时看到 60s 一次的心跳
- [ ] *(可选)* 开 `veighna` GUI（`pip install veighna`），连同一 CTP 账户，主窗口直接看资金曲线 + 持仓

## 下一步预告

Step 7 把系统生产化了，但策略还是 Step 5/6 的双均线——你能 7×24 跑、但策略赚不赚钱还是得看回测。

Step 8 我们用 **vnpy 现成的 CtaStrategy app** 替换自研的 cta_engine.py + cta_template.py：
- `vnpy.app.cta_strategy.engine.CtaEngine` ← 替 → `Demo/step7_live/cta_engine.py`
- `vnpy.app.cta_strategy.template.CtaTemplate` ← 替 → `Demo/step7_live/cta_template.py`
- 策略代码 0 改动（只换 import 路径）

代价：你要开始习惯 vnpy 的 event-driven 风格（on_tick/on_bar/on_trade 全部继承自 CtaTemplate）。

收益：少写 800 行代码、多一个 vnpy 的策略管理 UI、还能用 vnpy 自己的参数优化工具。