"""
Mock-Provider: liest aufgezeichnete Quoten aus einer .jsonl-Datei.

Damit laeuft das gesamte System end-to-end OHNE echte API. Das Parsen ist
bewusst defensiv (mehrere moegliche Feldnamen), damit verschiedene
Aufzeichnungs-Formate funktionieren, ohne den Code zu aendern.

Eine Zeile = ein Markt-Snapshot eines Events. Mehrere Zeilen koennen zum selben
Event gehoeren (z.B. verschiedene Maerkte oder Anbieter) — das ZUSAMMENFUEHREN
uebernimmt ``normalize.merge_events`` spaeter, NICHT dieser Provider. Hier wird
nur sauber ins Modell ueberfuehrt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

from arbfinder.models import Event, Market
from arbfinder.providers.base import (
    OddsProvider,
    coerce_float,
    first_present,
    parse_datetime,
    read_jsonl,
    split_teams,
)

logger = logging.getLogger("arbfinder.providers.mock")

# Kandidaten-Feldnamen je logischem Feld (Reihenfolge = Praeferenz).
_F_EVENT_ID = ("event_id", "id", "eventId", "match_id")
_F_EVENT_NAME = ("event_name", "name", "match", "title")
_F_HOME = ("home", "home_team", "homeTeam", "team_home")
_F_AWAY = ("away", "away_team", "awayTeam", "team_away")
_F_START = ("commence_time", "start_time", "kickoff", "commenceTime", "start")
_F_SNAPSHOT = ("ts", "timestamp", "snapshot_ts", "recorded_at")
_F_MARKET = ("market", "market_type", "market_key", "marketKey")
_F_EXPECTED = ("expected_outcomes", "n_outcomes", "num_outcomes")
_F_ODDS = ("odds", "prices", "outcomes", "selections")
_F_RESULT = ("result", "winner", "outcome", "settled")
_F_SPORT = ("sport", "sport_key", "sport_title")
_F_LEAGUE = ("league", "competition", "tournament")


def _parse_odds(raw: Any) -> dict[str, dict[str, float]]:
    """Bringt diverse Quoten-Strukturen auf {Ausgang: {Buchmacher: Quote}}.

    Akzeptiert das kanonische Dict-Format direkt und die The-Odds-API-aehnliche
    Liste ``[{"name": ..., "price": ..., "bookmaker": ...}, ...]``.
    """
    out: dict[str, dict[str, float]] = {}
    if isinstance(raw, dict):
        for outcome, books in raw.items():
            if not isinstance(books, dict):
                continue
            parsed = {bk: coerce_float(p) for bk, p in books.items()}
            out[str(outcome)] = {bk: p for bk, p in parsed.items() if p is not None and p > 0}
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            outcome = first_present(item, ("name", "outcome", "selection"), default=None)
            price = coerce_float(first_present(item, ("price", "odds", "decimal"), default=None))
            bookie = str(first_present(item, ("bookmaker", "book", "source"), default="unknown"))
            if outcome is None or price is None or price <= 0:
                continue
            out.setdefault(str(outcome), {})[bookie] = price
    return out


def _row_to_event(row: dict[str, Any]) -> Event:
    """Mappt eine Roh-Zeile defensiv auf ein ``Event`` mit genau einem Markt.

    Wirft ``KeyError``/``ValueError``, wenn ein Pflichtfeld (Teams, Anstosszeit,
    Quoten) fehlt — der Aufrufer faengt das ab und ueberspringt die Zeile, statt
    falsche Daten zu erzeugen.
    """
    # Teams: explizite home/away bevorzugen, sonst aus dem Namen trennen.
    home = first_present(row, _F_HOME, default=None)
    away = first_present(row, _F_AWAY, default=None)
    if home is None or away is None:
        name = first_present(row, _F_EVENT_NAME)
        home, away = split_teams(str(name))

    start_time = parse_datetime(first_present(row, _F_START))  # Pflicht -> wirft, wenn weg

    odds = _parse_odds(first_present(row, _F_ODDS, default={}))
    if not odds:
        raise ValueError("keine verwertbaren Quoten in Zeile")

    market = Market(
        market_type=str(first_present(row, _F_MARKET, default="h2h")),
        odds=odds,
        expected_outcomes=int(first_present(row, _F_EXPECTED, default=0) or 0),
    )

    snap_raw = first_present(row, _F_SNAPSHOT, default=None)
    return Event(
        event_id=str(first_present(row, _F_EVENT_ID, default="")),
        home=str(home),
        away=str(away),
        start_time=start_time,
        sport=str(first_present(row, _F_SPORT, default="")),
        league=str(first_present(row, _F_LEAGUE, default="")),
        markets=[market],
        result=(lambda r: str(r) if r is not None else None)(
            first_present(row, _F_RESULT, default=None)
        ),
        snapshot_ts=parse_datetime(snap_raw) if snap_raw is not None else None,
    )


class MockProvider(OddsProvider):
    """Provider, der Events aus einer aufgezeichneten .jsonl-Datei liest."""

    name = "mock"

    def __init__(self, path: str | Path = "fixtures/recorded_odds.jsonl") -> None:
        self.path = Path(path)

    def fetch_events(self) -> list[Event]:
        """Parst alle Zeilen; fehlerhafte werden gezaehlt+geloggt, nicht erfunden."""
        rows = read_jsonl(self.path)
        events: list[Event] = []
        skipped = 0
        for i, row in enumerate(rows):
            try:
                events.append(_row_to_event(row))
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("Zeile %d uebersprungen (fehlerhaft): %s", i, exc)
        if skipped:
            logger.warning("%d/%d Zeilen wegen fehlender Pflichtfelder uebersprungen",
                           skipped, len(rows))
        return events
