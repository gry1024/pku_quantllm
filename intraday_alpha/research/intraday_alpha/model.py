"""Custom LightGBM model for 5-minute forward-return prediction.

Inherits :class:`vnpy.alpha.model.AlphaModel` and replaces ``vnpy.alpha.model.models.lgb_model.LgbModel``'s
default ``mse`` objective with ``huber`` (robust to minute-bar outliers), and
raises ``min_data_in_leaf`` / ``num_leaves`` to suit minute-scale data.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import lightgbm as lgb

from vnpy.alpha import AlphaDataset, AlphaModel, Segment


class IntradayLgbModel(AlphaModel):
    """LightGBM regressor with intraday-appropriate hyperparameters."""

    def __init__(
        self,
        objective: str = "huber",
        alpha: float = 0.9,
        learning_rate: float = 0.05,
        num_leaves: int = 63,
        min_data_in_leaf: int = 200,
        feature_fraction: float = 0.7,
        bagging_fraction: float = 0.8,
        bagging_freq: int = 5,
        lambda_l2: float = 0.1,
        num_boost_round: int = 2000,
        early_stopping_rounds: int = 100,
        log_evaluation_period: int = 50,
        seed: int | None = 42,
    ) -> None:
        """Constructor — store hyperparams and lazily create the underlying booster."""
        # ``huber`` requires ``alpha``; ``regression_l1`` / ``mse`` ignore it.
        self.params: dict = {
            "objective":        objective,
            "alpha":            alpha,
            "learning_rate":    learning_rate,
            "num_leaves":       num_leaves,
            "min_data_in_leaf": min_data_in_leaf,
            "feature_fraction": feature_fraction,
            "bagging_fraction": bagging_fraction,
            "bagging_freq":     bagging_freq,
            "lambda_l2":        lambda_l2,
            "seed":             seed,
            # Force single-thread determinism — minute-scale data is small enough
            # and a live runner must not saturate the box.
            "num_threads":      1,
            "verbose":          -1,
        }
        self.num_boost_round: int       = num_boost_round
        self.early_stopping_rounds: int = early_stopping_rounds
        self.log_evaluation_period: int = log_evaluation_period

        self.model: lgb.Booster | None = None
        self.best_iteration: int = 0

    # -------------------------------------------------------------------------
    # AlphaModel API
    # -------------------------------------------------------------------------
    def fit(self, dataset: AlphaDataset) -> None:
        """Train on TRAIN segment, validate on VALID."""
        ds: list[lgb.Dataset] = self._prepare_data(dataset)

        self.model = lgb.train(
            self.params,
            ds[0],
            num_boost_round=self.num_boost_round,
            valid_sets=ds,
            valid_names=["train", "valid"],
            callbacks=[
                lgb.early_stopping(self.early_stopping_rounds),
                lgb.log_evaluation(self.log_evaluation_period),
            ],
        )
        self.best_iteration = int(self.model.best_iteration or 0)

    def predict(self, dataset: AlphaDataset, segment: Segment) -> np.ndarray:
        """Predict the 5-min forward return for the requested segment."""
        if self.model is None:
            raise ValueError("model is not fitted yet!")

        df: pl.DataFrame = dataset.fetch_infer(segment).sort(["datetime", "vt_symbol"])
        data: np.ndarray = df.select(df.columns[2:-1]).to_numpy()
        return np.asarray(self.model.predict(data))

    def detail(self) -> dict[str, Any]:
        """Return feature importance (gain) + best iteration, no matplotlib."""
        if self.model is None:
            return {}
        importance: np.ndarray = self.model.feature_importance(importance_type="gain")
        feature_names: list[str] = self.model.feature_name()
        top: list[tuple[str, float]] = sorted(
            zip(feature_names, importance.tolist()),
            key=lambda kv: kv[1],
            reverse=True,
        )[:30]
        return {
            "best_iteration":     self.best_iteration,
            "feature_importance": [{"feature": n, "gain": g} for n, g in top],
        }

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _prepare_data(self, dataset: AlphaDataset) -> list[lgb.Dataset]:
        """Build ``[train, valid]`` LightGBM datasets (TRAIN + VALID segments)."""
        ds: list[lgb.Dataset] = []

        for segment in [Segment.TRAIN, Segment.VALID]:
            df: pl.DataFrame = dataset.fetch_learn(segment).sort(["datetime", "vt_symbol"])
            data: np.ndarray = df.select(df.columns[2:-1]).to_numpy()
            label: np.ndarray = np.asarray(df["label"])
            ds.append(lgb.Dataset(data, label=label))

        return ds