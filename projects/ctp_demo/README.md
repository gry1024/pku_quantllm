# ctp_demo — 最小化 CTP 交易 DEMO

从底层 Python 交易 API（`vnpy_ctp.api` 的 MdApi/TdApi）开始，不经过 vnpy 的
MainEngine/EventEngine/CtpGateway，直接演示 CTP 柜台交互的最原始形态。

## 功能
1. **柜台连接登录**：交易前置（认证→登录）+ 行情前置（登录），纯异步回调状态机
2. **订阅合约行情**：登录成功后自动 `subscribeMarketData`
3. **打印行情推送**：`onRtnDepthMarketData` 逐条打印最新价/买卖一档/成交量/持仓量

## 运行
```bash
pip install vnpy_ctp   # 已安装 6.7.11.x 即可

# 第一次跑前：复制凭证模板并填入（凭证仅在本地 .env，不入 git）
cp .env.example .env
# 编辑 .env 填入 SIMNOW 账号；本 demo 不持有任何默认账号

python demo.py         # Ctrl+C 退出
```

## 要点
- CTP 所有请求异步：req 只负责发出，结果在 onRsp/onRtn 回调（CTP 内部线程）返回
- 交易线握手顺序：`onFrontConnected` → 认证（AppID/AuthCode）→ 登录；SimNow 必须认证
- 订阅时序：connect 后只登记列表，`onRspUserLogin` 回报里统一订阅（断线重连自动恢复）
- 本 demo 不订阅私有/公有流（`subscribePrivateTopic/PublicTopic`）——那是需要接收
  委托/成交推送的正式网关才需要的概念，见 ctp_breakout_demo
- 继承 MdApi/TdApi 时 `__init__` 必须调用 `super().__init__()`（C++ 扩展要求）
- 本版本绑定的 `subscribeMarketData` 一次只接受一个合约字符串
- 流文件目录 `flow/`（运行时生成，已 gitignore）
- 订阅合约须为当前挂牌月份，摘牌合约订阅**无任何错误回报**，只是收不到行情
- SimNow 第一套（"实盘"）服务器非交易时段拒绝连接（日盘收盘后~夜盘开盘前）

## 设计笔记
见 wiki：`wiki/code/ctp-demo-notes.md`
