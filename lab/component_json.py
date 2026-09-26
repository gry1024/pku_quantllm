"""JSON-based replacement for vnpy.alpha.lab.AlphaLab's shelve component storage.

Why: Python 3.13 on Windows has no working dbm backend (`dbm.open` /
`shelve.open` fail with "unable to open database file"), so the bundled
`AlphaLab.save_component_data` / `load_component_data` cannot write the
``lab/csi300/component/<index_symbol>`` file.

Format: one JSON file per index_symbol under the same dir; keys are
ISO date strings, values are the list of vt_symbols.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _component_path(component_dir: Path, index_symbol: str) -> Path:
    return component_dir / f"{index_symbol}.json"


def save_component_data(component_dir: Path | str, index_symbol: str,
                        index_components: dict[datetime, list[str]]) -> None:
    component_dir = Path(component_dir)
    component_dir.mkdir(parents=True, exist_ok=True)
    payload = {dt.strftime("%Y-%m-%d"): list(syms) for dt, syms in index_components.items()}
    _component_path(component_dir, index_symbol).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_component_data(component_dir: Path | str, index_symbol: str,
                        start: datetime | str, end: datetime | str) -> dict[datetime, list[str]]:
    component_dir = Path(component_dir)
    p = _component_path(component_dir, index_symbol)
    if not p.exists():
        return {}
    start = start if isinstance(start, datetime) else datetime.fromisoformat(start)
    end = end if isinstance(end, datetime) else datetime.fromisoformat(end)
    payload = json.loads(p.read_text(encoding="utf-8"))
    return {
        datetime.fromisoformat(d): syms
        for d, syms in payload.items()
        if start <= datetime.fromisoformat(d) <= end
    }


def patch_lab(lab) -> None:
    """Monkey-patch a vnpy.alpha.lab.AlphaLab instance to use JSON storage."""
    lab._component_json_dir = lab.component_path
    lab.save_component_data = lambda index_symbol, components: save_component_data(
        lab._component_json_dir, index_symbol, components)
    lab.load_component_data = lambda index_symbol, start, end: load_component_data(
        lab._component_json_dir, index_symbol, start, end)