"""Datenquellen (Provider). Mock laeuft offline; weitere brauchen Lizenz."""
from __future__ import annotations

from arbfinder.providers.base import OddsProvider, ProviderError
from arbfinder.providers.mock import MockProvider
from arbfinder.providers.theoddsapi import TheOddsApiProvider

__all__ = ["OddsProvider", "ProviderError", "MockProvider", "TheOddsApiProvider", "get_provider"]

_PROVIDERS: dict[str, type[OddsProvider]] = {
    MockProvider.name: MockProvider,
    TheOddsApiProvider.name: TheOddsApiProvider,
}


def get_provider(name: str, **kwargs: object) -> OddsProvider:
    """Findet einen Provider per Name (analog zur Strategie-Registry)."""
    if name not in _PROVIDERS:
        raise KeyError(f"Unbekannter Provider '{name}'. Bekannt: {sorted(_PROVIDERS)}")
    return _PROVIDERS[name](**kwargs)  # type: ignore[arg-type]
