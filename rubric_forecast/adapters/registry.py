"""Load DApp adapters by name (built-in + setuptools entry points)."""

from __future__ import annotations

import importlib.metadata
from typing import Dict, Type

from rubric_forecast.adapters.base import DAppAdapter
from rubric_forecast.adapters.lifefun import LifeFunDAppAdapter

_ENTRY_GROUP = "rubric_forecast.adapters"

_builtin: Dict[str, Type[object]] = {
    "lifefun": LifeFunDAppAdapter,
}


def list_adapter_names() -> list[str]:
    names = sorted(_builtin.keys())
    try:
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            extra = [e.name for e in eps.select(group=_ENTRY_GROUP)]
        else:
            extra = [e.name for e in eps.get(_ENTRY_GROUP, [])]  # type: ignore[union-attr]
        for n in extra:
            if n not in names:
                names.append(n)
    except Exception:
        pass
    return sorted(names)


def get_adapter(name: str) -> DAppAdapter:
    key = (name or "lifefun").strip().lower()
    if key in _builtin:
        inst = _builtin[key]()
        return inst  # type: ignore[return-value]
    try:
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            selected = list(eps.select(group=_ENTRY_GROUP, name=key))
        else:
            selected = [e for e in eps.get(_ENTRY_GROUP, []) if e.name == key]  # type: ignore[union-attr]
        if not selected:
            raise KeyError(key)
        loaded = selected[0].load()
        inst = loaded() if callable(loaded) else loaded
        return inst  # type: ignore[return-value]
    except Exception as e:
        raise ValueError(f"unknown adapter {name!r}: {e}") from e
