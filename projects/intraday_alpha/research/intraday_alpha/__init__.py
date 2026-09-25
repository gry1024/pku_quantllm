"""Intraday alpha research package (mirrors vnpy.alpha layout).

Public surface:
- :class:`IntradayAlphaDataset` — minute-bar factor set (AlphaDataset subclass)
- :class:`IntradayLgbModel`     — Huber-loss LightGBM (AlphaModel subclass)
- :class:`IntradayTopStrategy`  — single-instrument on/off signal consumer (AlphaStrategy subclass)
- :func:`load_config`           — read ./config.json
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .dataset import IntradayAlphaDataset
from .model import IntradayLgbModel
from .strategy import IntradayTopStrategy


_CONFIG_PATH: Path = Path(__file__).parent / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read the pipeline config JSON."""
    p: Path = Path(path) if path else _CONFIG_PATH
    import json  # local import keeps the package importable without json hot-path
    with open(p, encoding="UTF-8") as f:
        return json.load(f)


__all__ = [
    "IntradayAlphaDataset",
    "IntradayLgbModel",
    "IntradayTopStrategy",
    "load_config",
]