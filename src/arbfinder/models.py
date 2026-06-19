"""
Anbieterunabhaengige Datenmodelle.

Jeder Provider (Mock, The-Odds-API, ...) liefert dieselben ``Event``-Objekte,
egal wie seine Roh-JSON aussieht. So haengt der Rest der Pipeline
(normalize -> detector -> strategy) NICHT am Format einer einzelnen API.

Wichtige Designentscheidungen (aus CLAUDE.md / context.md):

* ``Event.start_time`` ist PFLICHT. Event-Identitaet = Teams UND Anstosszeit;
  ohne Anstosszeit koennen wir zwei Spiele derselben Teams an verschiedenen
  Tagen nicht auseinanderhalten (siehe normalize.py).
* ``Market`` traegt seine ``expected_outcomes`` (2-Wege Tennis, 3-Wege Fussball
  mit Remis, ...). Damit kann jede Stufe Vollstaendigkeit pruefen und
  Phantom-Arbs vermeiden, OHNE den Markttyp zu erraten.
* Maerkte werden NIE vermischt: h2h, totals und spreads sind getrennte
  ``Market``-Objekte. ``to_snapshots()`` erzeugt pro Markt genau einen
  Snapshot im Format, das ``Strategy.evaluate`` / ``backtest`` erwarten.

Die Bruecke zur fertigen Strategie/Backtest ist ``Event.to_snapshots()``: es
liefert genau das Dict, das ``arbfinder.strategies.base.Strategy.evaluate``
konsumiert (Schluessel: event_id, event_name, market, expected_outcomes, odds,
result). So bleibt der fertige Kern unangetastet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Eine Quotenkarte: Ausgang -> {Buchmacher -> Dezimalquote}.
OddsMap = dict[str, dict[str, float]]


@dataclass
class Market:
    """Ein einzelner Wettmarkt eines Events (z.B. h2h, totals, spreads).

    Attributes:
        market_type: Markttyp-Schluessel ("h2h", "totals", "spreads", ...).
            Maerkte mit unterschiedlichem ``market_type`` duerfen NIE vermischt
            werden (sonst entstehen unsinnige "Arbs" ueber inkompatible Wetten).
        odds: Ausgang -> {Buchmacher -> Dezimalquote}.
        expected_outcomes: Anzahl Ausgaenge, die ein VOLLSTAENDIGER Markt hat
            (2 fuer 2-Wege, 3 fuer 1X2, ...). 0 = unbekannt.
    """

    market_type: str
    odds: OddsMap = field(default_factory=dict)
    expected_outcomes: int = 0

    @property
    def outcomes(self) -> set[str]:
        """Alle benannten Ausgaenge (unabhaengig davon, ob Quoten vorliegen)."""
        return set(self.odds)

    @property
    def present_outcomes(self) -> int:
        """Anzahl Ausgaenge, fuer die mindestens eine echte Quote vorliegt."""
        return sum(1 for books in self.odds.values() if books)

    @property
    def is_complete(self) -> bool:
        """True, wenn jeder erwartete Ausgang mindestens eine Quote hat.

        Bei unbekannter Erwartung (``expected_outcomes <= 0``) koennen wir
        Unvollstaendigkeit NICHT beweisen und geben True zurueck — der Detector
        verwirft solche Maerkte ggf. separat (zu wenige Ausgaenge), zaehlt sie
        aber nicht faelschlich als "incomplete".
        """
        if self.expected_outcomes <= 0:
            return True
        return self.present_outcomes >= self.expected_outcomes

    def best_per_outcome(self) -> dict[str, tuple[str, float]]:
        """Beste (hoechste) Quote je Ausgang: Ausgang -> (Buchmacher, Quote).

        Ungueltige/leere Quoten (<= 0, None) werden ignoriert.
        """
        best: dict[str, tuple[str, float]] = {}
        for outcome, books in self.odds.items():
            valid = {b: float(p) for b, p in books.items() if p is not None and float(p) > 0}
            if not valid:
                continue
            bk = max(valid, key=lambda b: valid[b])
            best[outcome] = (bk, valid[bk])
        return best


@dataclass
class Event:
    """Ein Sportereignis mit einem oder mehreren Maerkten.

    Attributes:
        event_id: Stabile ID des Anbieters (kann ueber Anbieter hinweg
            abweichen — deshalb ist die echte Identitaet Teams + Anstosszeit,
            siehe normalize.event_identity).
        home, away: Teamnamen, wie vom Anbieter geliefert (noch NICHT
            normalisiert; das macht normalize.py).
        start_time: Anstosszeit. PFLICHT (Teil der Event-Identitaet).
        sport, league: optionaler Kontext.
        markets: Liste getrennter Maerkte.
        result: tatsaechlicher Ausgang, falls bekannt (fuer PnL-Backtests).
        snapshot_ts: Zeitpunkt der Quoten-Aufzeichnung (nicht die Anstosszeit!).
    """

    event_id: str
    home: str
    away: str
    start_time: datetime
    sport: str = ""
    league: str = ""
    markets: list[Market] = field(default_factory=list)
    result: str | None = None
    snapshot_ts: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start_time, datetime):
            raise TypeError(
                "Event.start_time ist Pflicht und muss ein datetime sein "
                f"(bekommen: {type(self.start_time).__name__})."
            )
        if not str(self.home).strip() or not str(self.away).strip():
            raise ValueError("Event braucht nicht-leere home- und away-Teamnamen.")

    @property
    def name(self) -> str:
        """Lesbarer Eventname, z.B. 'Man City v Arsenal'."""
        return f"{self.home} v {self.away}"

    @property
    def market_types(self) -> list[str]:
        """Alle vorhandenen Markttypen (Reihenfolge wie eingefuegt)."""
        return [m.market_type for m in self.markets]

    def get_market(self, market_type: str) -> Market | None:
        """Liefert den Markt eines Typs oder None — NIE blind markets[0]."""
        for m in self.markets:
            if m.market_type == market_type:
                return m
        return None

    def to_snapshots(self) -> list[dict[str, Any]]:
        """Erzeugt pro Markt einen Snapshot im Strategy/Backtest-Format.

        Das ist die Bruecke zum fertigen Kern: jedes Dict hat genau die
        Schluessel, die ``Strategy.evaluate`` und ``backtest.run`` erwarten.
        Ein Event mit mehreren Maerkten ergibt mehrere Snapshots — so bleiben
        Markttypen sauber getrennt.
        """
        snaps: list[dict[str, Any]] = []
        for m in self.markets:
            snaps.append(
                {
                    "ts": self.snapshot_ts.isoformat() if self.snapshot_ts else None,
                    "event_id": self.event_id,
                    "event_name": self.name,
                    "start_time": self.start_time.isoformat(),
                    "market": m.market_type,
                    "expected_outcomes": m.expected_outcomes,
                    "odds": {o: dict(books) for o, books in m.odds.items()},
                    "result": self.result,
                }
            )
        return snaps
