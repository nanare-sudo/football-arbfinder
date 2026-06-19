"""
Provider-Interface + defensive Parse-Helfer.

Ein Provider kapselt EINE Datenquelle (Mock-Datei, kommerzielle Odds-API, ...)
und liefert immer dieselben ``Event``-Objekte (siehe models.py). So haengt der
Rest der Pipeline nicht am Roh-Format einer einzelnen Quelle.

Die Helfer hier setzen die Lessons aus CLAUDE.md um:
* Defensiv parsen — mehrere moegliche Feldnamen durchprobieren, nie auf genau
  einen Schluessel vertrauen.
* KEINE stillen Platzhalter: fehlt eine PFLICHT-Angabe (z.B. Anstosszeit),
  wird die Zeile NICHT mit erfundenen Werten gefuellt, sondern als fehlerhaft
  gemeldet (der Aufrufer entscheidet: ueberspringen + zaehlen).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json
import logging
import math

from arbfinder.models import Event


class OddsProvider(ABC):
    """Abstrakte Quelle normalisierter Events.

    "Normalisiert" heisst hier: ins typisierte ``Event``/``Market``-Modell
    ueberfuehrt (anbieterunabhaengig). Das Zusammenfuehren gleicher Events ueber
    Anbieter/Teamschreibweisen hinweg macht ``normalize.merge_events`` —
    NICHT der Provider.
    """

    name: str = "base"

    @abstractmethod
    def fetch_events(self) -> list[Event]:
        """Holt die aktuellen Events der Quelle als ``Event``-Liste."""
        raise NotImplementedError


class ProviderError(RuntimeError):
    """Provider konnte nicht liefern (Konfig fehlt, Quelle kaputt, ...)."""


# --------------------------------------------------------------------------- #
# Defensive Parse-Helfer (von konkreten Providern genutzt)
# --------------------------------------------------------------------------- #

# Reihenfolge = Praeferenz; der erste vorhandene, nicht-leere Schluessel gewinnt.
_MISSING = object()


def first_present(row: dict[str, Any], keys: Iterable[str], default: Any = _MISSING) -> Any:
    """Erster vorhandener, nicht-None Wert unter mehreren moeglichen Schluesseln.

    Wirft ``KeyError``, wenn keiner da ist und kein ``default`` gesetzt wurde —
    so faellt ein fehlendes Pflichtfeld auf, statt still falsch zu sein.
    """
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    if default is _MISSING:
        raise KeyError(f"keiner der Schluessel {list(keys)} in Zeile vorhanden")
    return default


def coerce_float(value: Any) -> float | None:
    """Robuste Float-Konvertierung; None bei nicht konvertierbarem ODER nicht-endlichem Wert.

    NaN/inf werden bewusst zu None: sonst rutschen sie durch die ``> 0``-Filter
    der Provider (``nan <= 0`` und ``inf <= 0`` sind beide False) und erzeugen
    spaeter stille Phantom-Signale (verbotener Platzhalter, siehe CLAUDE.md).
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_datetime(value: Any) -> datetime:
    """Parst ISO-8601-Strings ('...Z' erlaubt) oder Epoch-Sekunden zu datetime.

    Naive Eingaben werden als UTC interpretiert. Wirft ``ValueError`` bei
    Unparsbarem — Anstosszeit ist Pflicht und darf nicht erfunden werden.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"unparsbares Datum: {value!r}") from exc
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"unparsbares Datum: {value!r}")


# Trenner zwischen Heim- und Auswaertsteam in 'event_name'-Strings.
_TEAM_SEPARATORS = (" v ", " vs ", " vs. ", " - ", " @ ", " x ")


def split_teams(event_name: str) -> tuple[str, str]:
    """Zerlegt 'Heim v Auswaerts' robust in (home, away).

    Probiert mehrere uebliche Trenner. Wirft ``ValueError``, wenn kein Trenner
    gefunden wird (lieber auffallen als raten).
    """
    name = event_name.strip()
    low = name.lower()
    for sep in _TEAM_SEPARATORS:
        idx = low.find(sep)
        if idx != -1:
            home = name[:idx].strip()
            away = name[idx + len(sep):].strip()
            if home and away:
                return home, away
    raise ValueError(f"kann Teams nicht aus {event_name!r} trennen")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Liest eine .jsonl-Datei; ignoriert Leerzeilen und '//'-Kommentare.

    Defekte JSON-Zeilen werden geloggt und uebersprungen (defensiv parsen) statt
    den ganzen Lauf abzubrechen — und NICHT durch erfundene Daten ersetzt.
    """
    logger = logging.getLogger("arbfinder.providers")
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Zeile %d uebersprungen (kein gueltiges JSON): %s", i, exc)
    return rows
