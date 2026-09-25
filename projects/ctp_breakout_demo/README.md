# ctp_breakout_demo — 突破价格触发下单 DEMO

在 ctp_demo 基础上扩展为完整的 **行情 → 决策 → 下单 → 回报** 闭环，仍然纯底层
API（`vnpy_ctp.api`），不经过 vnpy 框架层。

## 功能
1. **对接交易下单**：TdApi 走完下单前置流程（认证 → 登录 → **确认结算单**），然后 `reqOrderInsert`
2. **实现策略逻辑**：行情价格向上突破阈值时自动限价开多 1 手（只发一单，无撤单/仓位管理）
3. **打印交易回报**：`onRtnOrder`（委托状态流）/ `onRspOrderInsert`（场内拒单）/ `onRtnTrade`（成交）

## 运行
```bash
# 第一次跑前：复制凭证模板并填入（凭证仅在本地 .env，不入 git）
cp .env.example .env
# 编辑 .env 填入 SIMNOW 账号；本 demo 不持有任何默认账号

python demo.py     # 策略参数（合约/方向/阈值偏移/手数）直接改 demo.py 顶部常量
```
> 验证下单链路时，可把 `TRIGGER_OFFSET` 临时改为负值（如 -0.5），阈值低于现价，
> 首 tick 即触发，无需等待真实突破。

## 实测记录（2026-09-16 14:34，SimNow）
```
结算单确认成功，交易通道就绪
!! 价格突破触发：938.74 >= 938.18
>> 已发出委托：买入开仓 au2612 1手 @ 939.74 OrderRef=1
[委托] 1_947206615_1 au2612 买1手 已成交1手 状态=全部成交
[成交] au2612 买1手 @ 938.8
```

## 要点与坑
- 所有常量与状态字段带行内注释（连续块内 # 对齐）；所有类/方法有 docstring
- **结算单确认是下单前置条件**：未确认 `reqOrderInsert` 会被拒；CtpGateway 登录成功后立即自动确认
- **OrderRef 自增 + FrontID/SessionID 组合**才是完整委托号（重连后 SessionID 变化）
- **回报是全账户推送**：`onRtnOrder/onRtnTrade` 会收到该账号所有程序的委托；正式实现必须
  按 `FrontID_SessionID_OrderRef` 过滤自己的单，只按合约过滤不够（实测踩到）
- SimNow 结算单确认可能耗时 10~20 秒：demo 里策略触发早于通道就绪时通过 `ready_hook`
  回调在就绪后带最新价自动重试，不丢信号
- 本 demo 订阅私有/公有流（`subscribePrivateTopic/PublicTopic`）——需要接收委托/成交推送；
  只读行情的 demo1 则不需要
- SimNow 第一套（"实盘"）服务器非交易时段拒绝连接（日盘收盘后~夜盘开盘前）

## 设计笔记
见 wiki：`wiki/code/ctp-breakout-demo-notes.md`
