"""跳空留存候选与既有主推族的日均截面 Spearman 相关（train/valid 段，协议第 5 条）。

产出 report.md 正交性表格的数字；只读 train+valid，不触及 test/lockbox。
注意：Windows 下 prepare_data 用 spawn 进程池，主逻辑必须放在 main() 里保护。
"""
import copy
import sys
from functools import partial
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval_factors import (
    load_base_dataset,
    load_config,
    load_filters,
    process_cs_norm,
    process_drop_na,
)

FACTORS = {
    "gap_diff4_bare": "ts_sum((open / ts_delay(close, 1) - 1) - (close / open - 1), 4)",
    "gap_diff4_turn": "ts_sum((open / ts_delay(close, 1) - 1) - (close / open - 1), 4) * cs_rank(turnover)",
    "pv_anti_bare": "-ts_corr(close / ts_delay(close, 1) - 1, volume / ts_delay(volume, 1) - 1, 5)",
    "pv_anti_triple": "-ts_corr(close / ts_delay(close, 1) - 1, volume / ts_delay(volume, 1) - 1, 5) * cs_rank(turnover) * ts_mean(high / low - 1, 5)",
    "rev_triple": "-(close / ts_delay(close, 4) - 1) * cs_rank(turnover) * ts_mean(high / low - 1, 5) * ((close > ts_mean(close, 20)) > 0)",
}

SEGMENTS = {"train": ("2008-01-01", "2016-12-31"), "valid": ("2017-01-01", "2018-12-31")}
PAIRS = [
    ("gap_diff4_turn", "pv_anti_bare"),
    ("gap_diff4_turn", "pv_anti_triple"),
    ("gap_diff4_turn", "rev_triple"),
    ("gap_diff4_bare", "pv_anti_bare"),
]


def main() -> None:
    config = load_config()
    base = load_base_dataset(config)
    dataset = copy.deepcopy(base)
    for name, expr in FACTORS.items():
        dataset.add_feature(name=name, expression=expr)
    dataset.set_label(config["label"])
    dataset.add_processor("learn", partial(process_drop_na, names=["label"]))
    dataset.add_processor("learn", partial(process_cs_norm, names=["label"], method="zscore"))
    dataset.prepare_data(load_filters(config), max_workers=config["workers"])

    cols = ["datetime", "vt_symbol"] + list(FACTORS)
    pdf = dataset.result_df.select(cols).to_pandas()

    for seg, (s, e) in SEGMENTS.items():
        m = (pdf["datetime"] >= pd.Timestamp(s)) & (pdf["datetime"] <= pd.Timestamp(e))
        segdf = pdf.loc[m]
        print(f"== {seg}（{segdf['datetime'].nunique()} 天）日均截面 Spearman ==")
        for a, b in PAIRS:
            c = segdf.groupby("datetime").apply(lambda g: g[a].corr(g[b], method="spearman")).mean()
            print(f"  corr({a}, {b}) = {c:+.3f}")


if __name__ == "__main__":
    main()
