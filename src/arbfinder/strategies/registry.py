"""Mini-Registry, damit der Agent neue Strategien per Name findet und vergleicht."""
from __future__ import annotations
from arbfinder.strategies.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(f"Unbekannte Strategie '{name}'. Bekannt: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def all_strategies() -> list[str]:
    return sorted(_REGISTRY)
