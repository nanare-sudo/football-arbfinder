"""
Ergebnis-Anbindung — traegt zu aufgezeichneten Snapshots nachtraeglich den
tatsaechlichen Ausgang ('result') ein, sobald ein Spiel vorbei ist.

Erst MIT Ergebnissen kann der Backtest Profitabilitaet statt nur Detektion
messen (siehe CLAUDE.md/context.md). Diese Daten sind der eigentliche Engpass.

Aufbau:
* ``ResultSource`` (Interface) + ``TheOddsApiScores`` (eine Implementierung ueber
  den offiziellen scores-Endpoint). Die Quelle ist AUSTAUSCHBAR — wo die API
  keine Ergebnisse liefert, kann eine andere Quelle eingehaengt werden.
* ``attach_results`` matcht Snapshot-Event <-> Ergebnis ueber die
  normalize-Event-Identitaet (TEAMS UND Anstosszeit, nicht rohe Namen) und setzt
  den Ausgang als denjenigen Ausgangs-Schluessel, der in den Snapshot-Quoten
  tatsaechlich vorkommt (damit _simulate_pnl ihn findet).

EHRLICHE LUECKE: Der scores-Endpoint deckt nicht jede Sportart/jeden Tarif ab und
reicht nur begrenzt in die Vergangenheit (``daysFrom``). Ergebnisse muessen daher
zeitnah nachgezogen werden; fehlt Abdeckung, bleibt 'result' offen (kein
erfundener Ausgang). Leitplanke: lizenzierte API only, kein Scraping.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
import json
import logging
import os

from arbfinder.models import Event
from arbfinder.normalize import canonical_team, same_event
from arbfinder.providers.base import ProviderError, coerce_float, first_present, parse_datetime, split_teams

logger = logging.getLogger("arbfinder.results")

_BASE_URL = "https://api.the-odds-api.com/v4"
_DRAW_NAMES = {"draw", "tie", "x", "remis", "unentschieden"}


@dataclass
class EventResult:
    """Tatsaechlicher Ausgang eines Events (anbieterunabhaengig)."""

    home: str
    away: str
    start_time: datetime
    winner: str | None    # Team-Name oder "Draw"; None = (noch) nicht final/unbekannt


class ResultSource(ABC):
    """Austauschbare Quelle fuer Spielergebnisse."""

    name: str = "base"

    @abstractmethod
    def results(self) -> list[EventResult]:
        """Liefert bekannte Ergebnisse (final UND noch offene)."""
        raise NotImplementedError


def _winner_from_scores(home: str, away: str, hs: float | None, as_: float | None) -> str | None:
    if hs is None or as_ is None:
        return None
    if hs > as_:
        return home
    if as_ > hs:
        return away
    return "Draw"


def parse_scores(raw: list[dict[str, Any]]) -> list[EventResult]:
    """Mappt die dokumentierte scores-Antwort defensiv auf ``EventResult``.

    Pure Funktion (ohne Netzwerk) -> testbar. Nicht abgeschlossene Spiele liefern
    ``winner=None`` (kein erfundener Ausgang).
    """
    out: list[EventResult] = []
    for ev in raw:
        try:
            home = ev["home_team"]
            away = ev["away_team"]
            start = parse_datetime(ev["commence_time"])
        except (KeyError, ValueError) as exc:
            logger.warning("Score-Event uebersprungen (Pflichtfeld fehlt): %s", exc)
            continue
        if not ev.get("completed"):
            out.append(EventResult(str(home), str(away), start, None))
            continue
        scores = {}
        for s in ev.get("scores") or []:
            name = s.get("name")
            if name is not None:
                scores[str(name)] = coerce_float(s.get("score"))
        winner = _winner_from_scores(str(home), str(away), scores.get(str(home)), scores.get(str(away)))
        out.append(EventResult(str(home), str(away), start, winner))
    return out


class TheOddsApiScores(ResultSource):
    """Ergebnis-Quelle ueber den offiziellen The-Odds-API scores-Endpoint."""

    name = "theoddsapi_scores"

    def __init__(
        self,
        sport: str,
        *,
        api_key: str | None = None,
        days_from: int = 3,
        base_url: str = _BASE_URL,
    ) -> None:
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        self.sport = sport
        self.days_from = days_from
        self.base_url = base_url.rstrip("/")

    def results(self) -> list[EventResult]:
        if not self.api_key:
            raise ProviderError(
                "Kein API-Key. Setze ODDS_API_KEY oder uebergib api_key=... "
                "— Ergebnisse nur ueber die lizenzierte API."
            )
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "Paket 'requests' fehlt. Installiere mit: pip install arbfinder[live]"
            ) from exc
        url = f"{self.base_url}/sports/{self.sport}/scores"
        params = {"apiKey": self.api_key, "daysFrom": self.days_from}
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return parse_scores(resp.json())


# --------------------------------------------------------------------------- #
# Matching & Nachtragen
# --------------------------------------------------------------------------- #
def _row_event(row: dict[str, Any]) -> Event | None:
    """Baut aus einer Snapshot-Zeile ein Event (nur fuer den Identitaets-Match)."""
    try:
        home = first_present(row, ("home", "home_team"), default=None)
        away = first_present(row, ("away", "away_team"), default=None)
        if home is None or away is None:
            home, away = split_teams(str(first_present(row, ("event_name", "name", "match"))))
        start = parse_datetime(first_present(row, ("commence_time", "start_time", "kickoff")))
        return Event(event_id=str(row.get("event_id", "")), home=str(home), away=str(away),
                     start_time=start)
    except (KeyError, ValueError):
        return None


def _map_winner_to_outcome(winner: str, odds: dict[str, Any], known: Iterable[str] | None) -> str | None:
    """Bildet den Sieger auf den Ausgangs-Schluessel ab, der in ``odds`` vorkommt."""
    cw = canonical_team(winner, known)
    for key in odds:                                  # Team-Ausgang
        if canonical_team(key, known) == cw:
            return key
    if winner.strip().lower() in _DRAW_NAMES:         # Remis-Ausgang
        for key in odds:
            if key.strip().lower() in _DRAW_NAMES:
                return key
    return None


def attach_results(
    data_path: str | Path,
    source: ResultSource,
    *,
    known: Iterable[str] | None = None,
    time_tolerance_minutes: float = 90.0,
) -> int:
    """Traegt Ergebnisse in eine aufgezeichnete JSONL-Datei nach.

    Matcht ueber die Event-Identitaet (Teams UND Anstosszeit, ``normalize``),
    NICHT ueber rohe Namen. Setzt nur Zeilen OHNE bisheriges 'result' und nur,
    wenn ein finaler Ausgang vorliegt UND sich einem Snapshot-Ausgang zuordnen
    laesst. Kommentar-/Leerzeilen und bereits gesetzte Ergebnisse bleiben
    unangetastet. Gibt die Zahl aktualisierter Zeilen zurueck.
    """
    path = Path(data_path)
    final = [(r, _result_event(r)) for r in source.results() if r.winner]
    updated = 0
    out_lines: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            out_lines.append(line)
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Zeile uebersprungen (kein gueltiges JSON): %s", stripped[:60])
            out_lines.append(line)
            continue

        if not row.get("result"):
            outcome = _match(row, final, known, time_tolerance_minutes)
            if outcome:
                row["result"] = outcome
                updated += 1
        out_lines.append(json.dumps(row))

    path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    logger.info("Ergebnisse nachgetragen: %d Zeilen aktualisiert.", updated)
    return updated


def _result_event(r: EventResult) -> Event:
    return Event(event_id="", home=r.home, away=r.away, start_time=r.start_time)


def _match(row, final, known, tol) -> str | None:
    snap = _row_event(row)
    if snap is None:
        return None
    for r, res_ev in final:
        if same_event(snap, res_ev, known, time_tolerance_minutes=tol):
            return _map_winner_to_outcome(r.winner, row.get("odds", {}), known)
    return None
