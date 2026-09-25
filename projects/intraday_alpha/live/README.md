# intraday_alpha / live — CTP 实盘启动

`ctp_runner.py` 是个 headless 主循环：读 `.env`（与 `research/` 同级的 `../.env`）→ 起 `EventEngine` + `MainEngine` → 注册 `CtpGateway` + `CtaStrategyApp` → 连 SIMNOW → 加 `IntradayAlphaStrategy` → 主循环。

`strategy/intraday_alpha_strategy.py` 实现 `IntradayAlphaStrategy(CtaTemplate)`：每根 1-min bar 读 `live_signal.json`，按阈值决定目标仓位，发限价单。

---

## 1. 准备凭证（一次性）

```bash
cd projects/intraday_alpha
cp .env.example .env
# 编辑 .env 填入：
#   CTP_USERID / CTP_PASSWORD / CTP_BROKERID
#   CTP_TD_ADDRESS / CTP_MD_ADDRESS
#   CTP_APPID / CTP_AUTH_CODE
#   CTP_ENV (默认 "实盘")
# 可选：INTRADAY_VT_SYMBOL (默认 au2612.SHFE)
#       INTRADAY_SIGNAL_PATH (默认 ../research/lab/intraday/signal/intraday_live_signal.json)
```

`.env` 在 `.gitignore` 里，绝对不会进仓库。**账号信息只在本地 `.env` 维护，不允许硬编码到任何源文件里。**

---

## 2. 起模型 + 起 live（两步）

```bash
# 步骤 A：训练 + 写 live_signal.json（不连行情网关）
cd projects/intraday_alpha/research
python run.py --predict-only

# 步骤 B：起 live（连 SIMNOW、按 1-min bar 下单）
cd ../live
python ctp_runner.py
```

`ctp_runner.py` 启动后：

1. 把 `live/strategy/` 加进 sys.path，让 `class_name="IntradayAlphaStrategy"` 能被反射到
2. 读 `../.env`，缺任一 `CTP_*` 直接 `RuntimeError` 退出
3. 起 `EventEngine` + `MainEngine`，注册 `CtpGateway`、`CtaStrategyApp`
4. 按 `INTRADAY_VT_SYMBOL` 连 SIMNOW 实时前置
5. 等 12s 让连接握手 + 合约下载完成
6. `init_engine()` → `add_strategy(class_name="IntradayAlphaStrategy", …)`
7. `init_strategy().result()` 阻塞等 warm-up bar 加载完
8. `start_strategy` → 主循环每 5s 醒一次
9. `Ctrl+C` / `SIGTERM`：先 `stop_strategy`（撤单），再 `main_engine.close()`

---

## 3. SIMNOW 实时前置

| 角色 | 地址 |
|---|---|
| 交易 | `tcp://182.254.243.31:30001` |
| 行情 | `tcp://182.254.243.31:30011` |
| 经纪商 | `9999` |
| AppID | `simnow_client_test` |
| 授权码 | `0000000000000000` |
| 柜台环境 | `实盘` |

`实盘` 前置非交易时段拒绝连接（日盘收盘后~夜盘开盘前）；要全天候测试用 7×24 前置（在 `.env.example` 里已注释）。

---

## 4. 部署提示

### 4.1 三阶段验证

1. **未签名 smoke**：`ctp_runner.py` 启动后看
   ```
   [init] connecting CTP gateway…
   [init] waiting 12s for connection + contract download…
   [init] initializing CTA engine…
   [init] adding strategy intraday_alpha_01 for au2612.SHFE…
   [live] strategy started — entering main loop (Ctrl+C to stop)
   ```
   等 1 根 bar 到达 → `Ctrl+C` 干净退出（无订单）
2. **纸面成交**：把 `research/intraday_alpha/config.json` 里 `live.price_add_ticks` 改成 `20`（远超 tick 价位，保证不成交但链路是通的）→ 跑 1 个交易日，观察 `.vntrader/cta_strategy_intraday_alpha_01.json` 每根 bar 都在更新
3. **实盘成交**：恢复 `price_add_ticks=2`，并把 `signal_threshold_*` 调到能稳定产出 ≥1 笔/日的水平

### 4.2 云上守护

```bash
nohup python ctp_runner.py > runs.log 2>&1 &
```

崩了用 `systemd` / `pm2` 自动拉起。vnpy 默认日志目录 `<cwd>/.vntrader/`。

---

## 5. 调试

| 故障 | 第一时间检查 |
|---|---|
| `ModuleNotFoundError: No module named 'vnpy_ctastrategy'` | `pip install -e raw/vnpy_ctastrategy` |
| `RuntimeError: 缺少必需的 CTP 环境变量: ...` | `cat ../.env` 存在？7 个键都填了？ |
| `add_strategy` 抛 `KeyError` / `class_name` 找不到 | `strategy/` 目录里有没有 `intraday_alpha_strategy.py`？类名是不是 `IntradayAlphaStrategy`？ |
| `OSError: cannot load thosttraderapi_se` | 装 openctp-ctp 或设置 `LD_LIBRARY_PATH` 指向 dll/so 目录 |
| SIMNOW 报"未授权" | `.env` 里 `CTP_APPID` / `CTP_AUTH_CODE` 与 SIMNOW 后台签发的一致？ |
| 信号 JSON 读不到 | 路径默认是 `../research/lab/intraday/signal/intraday_live_signal.json`；先 `cd research && python run.py --predict-only` 写一次 |
| 周末连不上 SIMNOW `实盘` 前置 | 正常行为；改用 7×24 前置（`.env.example` 注释里有） |
| 进程启动后 5 秒内连续异常 | 多半是 vnpy 找不到 .so 或 DLL 版本不兼容，看 `<cwd>/.vntrader/logs/error_*.log` |