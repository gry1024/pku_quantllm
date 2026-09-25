# 自动化因子挖掘 DEMO（LLM 驱动迭代）

由 ZCode 会话担任"挖掘大脑"的因子挖掘演示项目：

1. 用户给出挖掘方向
2. LLM 批量生成因子公式 → 写入 `runs/<dir>_round_XX/candidates.json`
3. 调用 `python eval_factors.py runs/<dir>_round_XX/candidates.json`（基于 vnpy.alpha 计算绩效）
4. LLM 读取 `metrics.json` 评估，决定下一轮公式（写 `runs/<dir>_round_XX/notes.md` 反思）
5. 最多 10 轮；连续 2 轮最优 Rank-IR 提升 < 5% 且无新方向时可早停
6. 终局：`python final_report.py` 对留存因子出深度绩效报告（`runs/final/performance_report.md`），
   叙事总结写 `runs/final/report.md`

已完成四方向实测（量价共振 → 反转 → 动量证伪 → 跳空，2026-09-16 ~ 09-18）。
runs/ 只保留最近两方向的产物：`final/`（跳空，当前）与 `final_mom/`（动量），
更早方向的留存情况见 `wiki/code/alpha-mining-demo-notes.md` 的"产物留存现状"节。

## 终局报告（final_report.py）

对 `runs/final/factors.json` 的每个因子输出：分段 IC/RankIR 表、十分组单调性、
多空（Q10-Q1）年化/Sharpe/回撤、多头组换手率与成本拖累估算、分年 RankIC，
以及四张图（分组累计收益 / 累积 RankIC / 多空累计 / 分年 RankIC）+ 三因子 test 段对比图。

核心结构是**数据流收敛**：每个因子只做一次分组，产出两张中间表
（日度截面 IC 序列、十分组日收益表），所有指标和图都从这两张表派生——
这正是想让学生掌握的思路：一切绩效分析都建立在"日度截面"这两张表上。

> 为什么不用 vnpy.alpha 内置的 `show_feature_performance`（alphalens 全套 tear sheet）？
> 它是交互式弹窗 API，headless 落盘要打两个 monkey-patch（pandas 3.0 月度频率
> "M"→"ME" 兼容 + GridFigure.close 保存钩子），且产出图无语义命名——教学上
> 先用可见代码算懂每个数字，再知道框架有一键版。补丁与取舍记录见
> `wiki/code/alpha-mining-demo-notes.md`。

## 约定

- 字段：`open / high / low / close / volume / turnover / vwap`
- 算子：仅用 `vnpy.alpha.dataset` 中 `ts_function` / `cs_function` 导出的算子
- 选择标准：train 段选因子，valid 段做样本外验证（报告两者）
- 指标：IC / IR / Rank-IC / Rank-IR / 方向胜率（按日截面相关）
- label：`ts_delay(close, -3) / ts_delay(close, -1) - 1`（T+1 至 T+3 收益）

## 目录

```
config.json        # 数据路径（lab_path 为绝对路径，指向 D:/pku_demo）、区间、label
eval_factors.py    # 轮次评估：批量因子 -> 分段 IC 指标（筛选用）
final_report.py    # 终局报告：留存因子 -> 指标表格 + 关键图表（唯一报告脚本）
runs/
  cache/               # 基础数据集 pickle 缓存（改区间/成分后需删除重建）
  gap_round_01..04/    # 跳空方向轮次产物（candidates + metrics + notes）
  mom_round_01..06/    # 动量方向轮次产物
  final/               # 当前方向（跳空）终局产物 + retention.md 留存决策
  final_mom/           # 上一方向（动量）终局产物归档
```

## 依赖

- `pip install "vnpy[alpha]" --index=https://pypi.vnpy.com`（带来 polars / matplotlib 等）
- alphalens-reloaded 随 vnpy[alpha] 安装（`show_feature_performance` 用），本 demo 代码不直接依赖
- 数据复用 `raw/alpha_researcher/lab/csi300`（859 只沪深300日线，只读；该目录在 D 盘
  原始工作区，`config.json` 的 `lab_path` 为绝对路径引用）
