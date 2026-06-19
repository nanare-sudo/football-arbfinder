"""
Loader fuer football-data.co.uk CSV-Downloads (kostenlos, offiziell angeboten).

Diese CSVs enthalten je Spiel Schlussquoten MEHRERER Buchmacher PLUS das
tatsaechliche Ergebnis (FTR) — also genau das, was die Value-Strategie braucht,
um SOFORT etwas Profit-Nahes (nicht nur Detektion) zu messen.

LEITPLANKE: Das hier ist KEIN Scraper. Es liest die offiziell zum Download
angebotenen CSV-DATEIEN (Pfad als Parameter). Die Website wird nicht abgefragt.

Spalten-Mapping (defensiv, generisch):
* Teams: HomeTeam/AwayTeam (oder Home/Away), Ergebnis: FTR (H/D/A).
* Buchmacher-Quoten kommen als 3er-Triple ``<Code>H/<Code>D/<Code>A`` (z.B.
  B365H/B365D/B365A). Wir erkennen Triples generisch (statt eine feste Bookie-
  Liste zu pflegen, die sich ueber die Jahre aendert).
* SCHLUSSQUOTEN bevorzugt: existieren Closing-Spalten (Code endet auf 'C', z.B.
  B365CH), werden NUR diese genutzt; sonst die Pre-Match-Spalten.
* Aggregat-Spalten (Max/Avg/BbMx/BbAv) sind KEINE Buchmacher und fliegen raus —
  sonst wuerde der Konsens-Devig sich selbst mitteln.

EHRLICH: Schlussquoten + Ergebnis erlauben einen echten Value-Backtest, aber die
Aussagekraft haengt an der Qualitaet des fairen-Wahrscheinlichkeits-Modells
(Konsens-Devig ist nur ein grober Schaetzer). Fehlen Zeit-Spalten, ist die
Anstosszeit nur datumsgenau (UK-Lokalzeit wird als UTC interpretiert) — fuer einen
in sich geschlossenen Datensatz mit gesetztem 'result' unkritisch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import csv
import json
import logging

from arbfinder.models import Event, Market
from arbfinder.normalize import merge_events
from arbfinder.providers.base import OddsProvider, coerce_float, first_present
from arbfinder.recorder import _event_rows

logger = logging.getLogger("arbfinder.providers.footballdata")

# Aggregat-Spalten (kein einzelner Buchmacher) — vom Konsens ausschliessen.
_AGGREGATE_BASES = {"Max", "Avg", "BbMx", "BbAv"}
_RESULT_MAP = {"H": "home", "D": "Draw", "A": "away"}   # FTR -> Ausgang (home/away spaeter ersetzt)


def _bookie_triples(fieldnames: Iterable[str]) -> dict[str, tuple[str, str, str]]:
    """Erkennt Buchmacher-Quoten-Triples; bevorzugt Closing, schliesst Aggregate aus.

    Returns: {Bookie-Name -> (H-Spalte, D-Spalte, A-Spalte)}.

    Closing-Spalten tragen ein zusaetzliches 'C' (z.B. B365CH). ACHTUNG: manche
    Bookie-Codes enden selbst auf 'C' (VC = VC Bet, mit VCH/VCD/VCA pre-match und
    VCCH/... closing). Deshalb gilt ein '...C'-Prefix nur dann als Closing, wenn
    sein Basis-Prefix OHNE 'C' ebenfalls existiert — sonst ist 'C' Teil des
    Bookie-Codes.
    """
    fields = set(fieldnames)
    candidates: dict[str, tuple[str, str, str]] = {}
    for f in fields:
        if len(f) >= 2 and f.endswith("H"):
            prefix = f[:-1]
            if (prefix + "D") in fields and (prefix + "A") in fields:
                candidates[prefix] = (f, prefix + "D", prefix + "A")

    def is_closing(p: str) -> bool:
        return p.endswith("C") and p[:-1] in candidates      # nur echtes Closing

    triples: dict[str, tuple[str, str, str]] = {}
    for prefix, cols in candidates.items():
        if is_closing(prefix):
            continue                                          # via Basis-Bookie behandelt
        if prefix in _AGGREGATE_BASES or not prefix:
            continue
        closing_cols = candidates.get(prefix + "C")           # Schlussquoten bevorzugen
        triples[prefix] = closing_cols if (closing_cols and is_closing(prefix + "C")) else cols
    return triples


def _parse_kickoff(date_str: str, time_str: str) -> datetime:
    """Parst football-data Datum (+ optionale Zeit) zu einem UTC-datetime."""
    d = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(date_str.strip(), fmt)
            break
        except ValueError:
            continue
    if d is None:
        raise ValueError(f"unparsbares Datum: {date_str!r}")
    if time_str and time_str.strip():
        try:
            t = datetime.strptime(time_str.strip(), "%H:%M")
            d = d.replace(hour=t.hour, minute=t.minute)
        except ValueError:
            pass            # Zeit unparsbar -> datumsgenau (00:00), bewusst kein Raten
    return d.replace(tzinfo=timezone.utc)


def _row_to_event(row: dict[str, Any], triples: dict[str, tuple[str, str, str]]) -> Event:
    """Mappt eine CSV-Zeile auf ein Event mit gesetztem 'result' (oder None)."""
    home = first_present(row, ("HomeTeam", "Home"), default=None)
    away = first_present(row, ("AwayTeam", "Away"), default=None)
    if not home or not str(home).strip() or not away or not str(away).strip():
        raise ValueError("Heim-/Auswaertsteam fehlt")
    home, away = str(home).strip(), str(away).strip()

    date_str = str(first_present(row, ("Date",)))
    start = _parse_kickoff(date_str, str(first_present(row, ("Time",), default="")))

    odds: dict[str, dict[str, float]] = {home: {}, "Draw": {}, away: {}}
    for bookie, (hc, dc, ac) in triples.items():
        for outcome, col in ((home, hc), ("Draw", dc), (away, ac)):
            price = coerce_float(row.get(col))
            if price is not None and price > 0:
                odds[outcome][bookie] = price
    odds = {o: books for o, books in odds.items() if books}   # leere Ausgaenge weglassen

    ftr = str(first_present(row, ("FTR", "Res"), default="") or "").strip().upper()
    slot = _RESULT_MAP.get(ftr)
    result = {"home": home, "Draw": "Draw", "away": away}.get(slot) if slot else None

    return Event(
        event_id=f"fd:{home}:{away}:{date_str}",
        home=home, away=away, start_time=start,
        sport="soccer", league=str(row.get("Div", "") or ""),
        markets=[Market("h2h", odds, 3)], result=result,
    )


class FootballDataProvider(OddsProvider):
    """Liest Events (mit Ergebnis) aus einer football-data.co.uk CSV-DATEI."""

    name = "footballdata"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_events(self) -> list[Event]:
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            triples = _bookie_triples(reader.fieldnames or [])
            events: list[Event] = []
            skipped = 0
            for i, row in enumerate(reader):
                try:
                    events.append(_row_to_event(row, triples))
                except (KeyError, ValueError) as exc:
                    skipped += 1
                    logger.warning("Zeile %d uebersprungen: %s", i, exc)
        if skipped:
            logger.warning("%d/%d Zeilen uebersprungen (fehlende Pflichtfelder).",
                           skipped, skipped + len(events))
        return events


def _classify_triples(fieldnames: Iterable[str]) -> tuple[dict[str, tuple[str, str, str]],
                                                          dict[str, tuple[str, str, str]]]:
    """Trennt Quoten-Triples in EROEFFNUNG und SCHLUSS (Closing).

    Returns: (opening{code->cols}, closing{base_code->cols}). Ein '...C'-Prefix
    gilt nur als Closing, wenn sein Basis-Prefix ohne 'C' existiert (so bleibt
    VC = VC Bet eine Eroeffnungsquelle, waehrend PSC = Pinnacle-Schluss ist).
    """
    fields = set(fieldnames)
    candidates: dict[str, tuple[str, str, str]] = {}
    for f in fields:
        if len(f) >= 2 and f.endswith("H"):
            prefix = f[:-1]
            if (prefix + "D") in fields and (prefix + "A") in fields:
                candidates[prefix] = (f, prefix + "D", prefix + "A")

    def is_closing(p: str) -> bool:
        return p.endswith("C") and p[:-1] in candidates

    opening = {p: c for p, c in candidates.items() if not is_closing(p)}
    closing = {p[:-1]: c for p, c in candidates.items() if is_closing(p)}
    return opening, closing


# Pinnacle-Schluss-Quoten je Ausgang werden unter diesem Schluessel abgelegt.
PINNACLE_CLOSE_KEY = "PSC"
PINNACLE_OPEN_KEY = "PS"


def _row_to_pinnacle_event(
    row: dict[str, Any],
    opening: dict[str, tuple[str, str, str]],
    ps_close: tuple[str, str, str] | None,
) -> Event:
    """Mappt eine CSV-Zeile auf ein Event mit ALLEN Eroeffnungsquellen + Pinnacle-Schluss.

    odds[Ausgang] = {<Bookie-Code>: Eroeffnungsquote, ..., 'PSC': Pinnacle-Schluss}.
    Die Bet-Quelle (z.B. 'Max') ist einer der Eroeffnungs-Codes; PINNACLE_OPEN_KEY
    ('PS') ist der scharfe Anker; 'PSC' dient dem Closing Line Value.
    """
    home = first_present(row, ("HomeTeam", "Home"), default=None)
    away = first_present(row, ("AwayTeam", "Away"), default=None)
    if not home or not str(home).strip() or not away or not str(away).strip():
        raise ValueError("Heim-/Auswaertsteam fehlt")
    home, away = str(home).strip(), str(away).strip()
    date_str = str(first_present(row, ("Date",)))
    start = _parse_kickoff(date_str, str(first_present(row, ("Time",), default="")))

    outcomes = (home, "Draw", away)
    odds: dict[str, dict[str, float]] = {o: {} for o in outcomes}
    for code, cols in opening.items():
        for o, col in zip(outcomes, cols):
            price = coerce_float(row.get(col))
            if price is not None and price > 0:
                odds[o][code] = price
    if ps_close is not None:
        for o, col in zip(outcomes, ps_close):
            price = coerce_float(row.get(col))
            if price is not None and price > 0:
                odds[o][PINNACLE_CLOSE_KEY] = price

    ftr = str(first_present(row, ("FTR", "Res"), default="") or "").strip().upper()
    slot = _RESULT_MAP.get(ftr)
    result = {"home": home, "Draw": "Draw", "away": away}.get(slot) if slot else None

    return Event(
        event_id=f"fd:{home}:{away}:{date_str}", home=home, away=away, start_time=start,
        sport="soccer", league=str(row.get("Div", "") or ""),
        markets=[Market("h2h", odds, 3)], result=result,
    )


def load_pinnacle_events(csv_path: str | Path, *, bet_source: str = "Max") -> list[Event]:
    """Laedt E0-CSV-DATEIEN mit Pinnacle (offen+Schluss) + waehlbarer Bet-Quelle.

    Kein Scraping — nur die offiziell angebotenen CSV-Dateien. Wirft, wenn keine
    Pinnacle-Eroeffnungsquoten (PS*) vorhanden sind.
    """
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        opening, closing = _classify_triples(reader.fieldnames or [])
        if PINNACLE_OPEN_KEY not in opening:
            raise ValueError("Keine Pinnacle-Eroeffnungsquoten (PS*) in der CSV.")
        if bet_source not in opening:                    # Konfig-Fehler -> sofort, nicht je Zeile
            raise ValueError(f"Bet-Quelle {bet_source!r} fehlt (vorhanden: {sorted(opening)}).")
        ps_close = closing.get(PINNACLE_OPEN_KEY)        # PSC*-Spalten (oder None)
        events: list[Event] = []
        skipped = 0
        for i, row in enumerate(reader):
            try:
                events.append(_row_to_pinnacle_event(row, opening, ps_close))
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("Zeile %d uebersprungen: %s", i, exc)
    if skipped:
        logger.warning("%d/%d Zeilen uebersprungen.", skipped, skipped + len(events))
    return events


def to_jsonl(csv_path: str | Path, out_path: str | Path, *, known: Iterable[str] | None = None) -> int:
    """Konvertiert eine football-data CSV ueber normalize.py ins JSONL-Backtest-Format.

    Laeuft durch ``merge_events`` (Team-Identitaet -> kanonische Namen) und schreibt
    pro Markt eine Zeile (mit gesetztem 'result'). UEBERSCHREIBT out_path.
    """
    events = merge_events(FootballDataProvider(csv_path).fetch_events(), known)
    rows = [r for ev in events for r in _event_rows(ev, ev.start_time)]
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)
