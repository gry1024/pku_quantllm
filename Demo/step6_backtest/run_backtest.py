"""Step 6: run_backtest —— 把 DoubleMaStrategy 塞进 vnpy BacktestingEngine。

完整流程（vnpy 标准用法）：
    1. BacktestingEngine()
    2. set_parameters(合约, 周期, 起止, 费率/滑点/合约乘数/最小变动/初始资金)
    3. add_strategy(策略类, 参数)
    4. load_data()            —— 从 vnpy 数据库拉历史 bar
    5. run_backtesting()      —— 按时间顺序回放 bar → strategy.on_bar()
    6. calculate_result()     —— 算逐日 PnL → DataFrame
    7. calculate_statistics() —— 输出夏普/最大回撤/收益回撤比 等
    8. show_chart()           —— plotly 出资金曲线 + 回撤阴影，存成 HTML

关键参数解释（任何一个不对，结果就骗人）：
    rate          —— 手续费率（au 实际约万分之 0.5；这里用 0.0005 即万 0.5）
    slippage      —— 滑点（au 主力日内一般 0.02 元够；这里用 0.02）
    size          —— 合约乘数（au 是 1000g/手）
    pricetick     —— 最小价格变动（au 是 0.02 元）
    capital       —— 初始资金（随便给，不影响相对收益）

撮合逻辑：
    BAR 模式下，"下一根 K 线开盘价成交"——order 挂出后，
    若该 bar 区间覆盖挂单价 → 用 bar.open_price 成交。
    注意：是**下一根**bar，不是当前 bar，所以有 1 根延迟。

用法：
    # 默认用 feed_data.py 喂的数据（au2606.SHFE，1m）
    python run_backtest.py

    # 自定义合约/周期/策略参数
    python run_backtest.py --symbol AU2606 --interval 1m --fast 5 --slow 20
"""

import argparse
import os
from datetime import datetime, timedelta

from vnpy.trader.constant import Interval
from vnpy_ctastrategy.backtesting import BacktestingEngine

from double_ma_strategy import DoubleMaStrategy


# 默认值（与 feed_data.py 保持一致）
DEFAULT_SYMBOL = "AU2606"
DEFAULT_INTERVAL = "1m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL,
                        help=f"合约代码，默认 {DEFAULT_SYMBOL}")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL,
                        choices=["1m", "5m", "15m", "30m", "60m", "1d"],
                        help=f"K 线周期，默认 {DEFAULT_INTERVAL}")
    parser.add_argument("--fast", type=int, default=5, help="快均线窗口")
    parser.add_argument("--slow", type=int, default=20, help="慢均线窗口")
    parser.add_argument("--days", type=int, default=10,
                        help="回测覆盖天数（拉今天往前 days 天）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 把 AKShare 大写合约 → vnpy 内部小写 "合约.交易所"
    vt_symbol = f"{args.symbol.lower()}.SHFE"
    vn_interval = Interval.DAILY if args.interval == "1d" else Interval.MINUTE

    engine = BacktestingEngine()

    # ---- 1) 设置回测参数 ----
    end = datetime.now().replace(hour=23, minute=59, second=59)
    start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0)

    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=vn_interval,
        start=start,
        end=end,
        rate=0.0005,            # au 手续费 万分之 0.5
        slippage=0.02,          # au 主力滑点 0.02 元
        size=1000,              # au 合约乘数 1000g/手
        pricetick=0.02,         # au 最小变动 0.02 元
        capital=1_000_000,      # 初始 100 万
    )
    print(f"[run_backtest] 合约={vt_symbol} 周期={args.interval} 区间={start} ~ {end}")
    print(f"[run_backtest] 费率={engine.rate} 滑点={engine.slippage} "
          f"size={engine.size} pricetick={engine.pricetick} 资金={engine.capital}")

    # ---- 2) 加策略 ----
    engine.add_strategy(DoubleMaStrategy,
                        {"fast_window": args.fast, "slow_window": args.slow})
    print(f"[run_backtest] 策略={DoubleMaStrategy.__name__} "
          f"参数={engine.strategy.get_parameters()}")

    # ---- 3) 加载历史数据 ----
    engine.load_data()
    print(f"[run_backtest] 历史数据载入完成，共 {len(engine.history_data)} 根 bar")
    if len(engine.history_data) < args.slow + 1:
        print(f"[run_backtest] ⚠️ 数据太少（{len(engine.history_data)} < {args.slow + 1}）"
              f"，策略要 {args.slow + 1} 根 bar 才出信号。请：")
        print(f"  - 重跑 feed_data.py 拉更多数据")
        print(f"  - 或 --slow {len(engine.history_data) - 2} 调小窗口")
        return

    # ---- 4) 跑回放 ----
    engine.run_backtesting()

    # ---- 4.5) 把策略 write_log 的内容（金叉/死叉等）打到终端 ----
    if engine.logs:
        print("\n========== 策略日志（write_log）==========")
        for line in engine.logs:
            print(line)
        print("==========================================\n")

    # ---- 5) 算每日 PnL ----
    df = engine.calculate_result()
    print(f"[run_backtest] 逐日 PnL DataFrame：{len(df)} 行")
    if not df.empty:
        print(df.tail().to_string())

    # ---- 6) 算统计指标 ----
    stats = engine.calculate_statistics()
    print("\n========== 回测绩效统计 ==========")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:<30s} = {v:>14.4f}")
        else:
            print(f"  {k:<30s} = {v}")
    print("=================================\n")

    # ---- 7) 出 plotly 资金曲线 ----
    out_html = os.path.abspath("backtest_result.html")
    fig = engine.show_chart()
    fig.write_html(out_html)
    print(f"[run_backtest] 资金曲线已保存到 {out_html}")
    print(f"[run_backtest] 成交笔数={engine.trade_count}")


if __name__ == "__main__":
    main()