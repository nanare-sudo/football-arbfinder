"""
Strategie-Interface. JEDE neue Idee (Arbitrage, Value-Betting, ...) wird eine
Strategy. So sind sie austauschbar und im Backtest direkt vergleichbar.

Eine Strategy bekommt einen Markt-Snapshot und gibt Signale zurueck.
Der Backtest misst die Signale gegen den bekannten Ausgang (falls vorhanden).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Signal:
    """Ein Vorschlag der Strategie fuer EIN Event."""
    event_id: str
    event_name: str
    market: str
    kind: str                       # "arbitrage" | "value" | ...
    edge_pct: float                 # erwarteter Vorteil in % (Backtest-Metrik)
    stakes: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, market_snapshot: dict[str, Any]) -> list[Signal]:
        """Bekommt EIN normalisiertes Event (mit Quoten je Bookie) -> Signale."""
        raise NotImplementedError
