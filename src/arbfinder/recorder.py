"""
Recorder — zeichnet echte Quoten-Snapshots ueber die Zeit auf.

Das ist der eigentliche Hebel des Projekts (siehe CLAUDE.md/context.md):
Solange keine historischen Quoten MIT spaeteren Ergebnissen vorliegen, misst der
Backtest nur Detektion, nicht Profitabilitaet. Der Recorder fragt einen Provider
(typisch ``TheOddsApiProvider``) in festem Intervall ab und haengt jeden Snapshot
als Zeile an eine JSONL-Datei an — gleiches Format wie
``fixtures/recorded_odds.jsonl`` (``ts`` = Abfragezeit, ``commence_time`` =
Anstoss, ``odds`` je Bookie, ``expected_outcomes``).

Eigenschaften:
* APPEND-ONLY: derselbe Event zu verschiedenen ``ts`` ergibt mehrere Zeilen —
  das ist gewollt (Quotenverlauf), es wird NICHT dedupliziert.
* Robust: eine fehlerhafte API-Antwort wird geloggt und uebersprungen, nicht
  gecrasht (skip-and-log). Keine erfundenen Werte.
* Kontingent: der API-Verbrauch (x-requests-remaining/used) wird geloggt; das
  Intervall ist die primaere Rate-Limit-Kontrolle.

LEITPLANKEN: lizenzierte API only, KEIN Scraping. API-Key ausschliesslich aus
``ODDS_API_KEY`` (nie committen). Es wird nur aufgezeichnet/gemeldet, NIE gesetzt.

EHRLICH zur Latenz: je API-Tier liegen Sekunden bis Minuten zwischen echter
Quotenaenderung und Abruf. Ein im Snapshot erkanntes Signal war also nicht
zwingend real setzbar — die Snapshot-Frequenz (``--interval``) gehoert
dokumentiert, wenn man spaeter Profitabilitaet bewertet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import logging

from arbfinder.models import Event
from arbfinder.providers.base import OddsProvider

logger = logging.getLogger("arbfinder.recorder")


def _event_rows(event: Event, queried_at: datetime) -> list[dict[str, Any]]:
    """Serialisiert ein Event pro Markt ins JSONL-Fixture-Format."""
    rows: list[dict[str, Any]] = []
    for m in event.markets:
        rows.append({
            "ts": queried_at.isoformat(),                  # Abfragezeit
            "commence_time": event.start_time.isoformat(),  # echter Anstoss
            "event_id": event.event_id,
            "event_name": event.name,
            "sport": event.sport,
            "league": event.league,
            "market": m.market_type,
            "expected_outcomes": m.expected_outcomes,
            "odds": {o: dict(books) for o, books in m.odds.items()},
            "result": event.result,                         # i.d.R. None (spaeter nachtragen)
        })
    return rows


@dataclass
class Recorder:
    """Fragt einen Provider ab und haengt Snapshots an eine JSONL-Datei an."""

    provider: OddsProvider
    out_path: str | Path

    def tick(self, *, now: datetime | None = None) -> int:
        """Ein Abruf: fetch -> serialisieren -> anhaengen. Gibt #Zeilen zurueck.

        Robust: schlaegt die Abfrage fehl, wird geloggt und uebersprungen
        (Rueckgabe 0) — der Lauf crasht NICHT.
        """
        now = now or datetime.now(timezone.utc)
        try:
            events = self.provider.fetch_events()
        except Exception as exc:  # noqa: BLE001 - bewusst breit: Recorder darf nie sterben
            logger.warning("Abfrage uebersprungen (fehlgeschlagen): %s", exc)
            return 0

        rows: list[dict[str, Any]] = []
        for ev in events:
            try:
                rows.extend(_event_rows(ev, now))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Event uebersprungen (Serialisierung): %s", exc)

        self._append(rows)

        quota = getattr(self.provider, "last_quota", None) or None
        if quota and str(quota.get("remaining")) == "0":
            logger.warning("API-Kontingent erschoepft (remaining=0) — Recorder pausiert sinnvoll.")
        logger.info(
            "Aufgezeichnet: %d Zeilen aus %d Events%s",
            len(rows), len(events),
            f" | API-Kontingent {quota}" if quota else "",
        )
        return len(rows)

    def _append(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        path = Path(self.out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:      # APPEND-ONLY
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def start(self, interval_minutes: float) -> None:  # pragma: no cover - blockierend
        """Startet die periodische Aufzeichnung (blockiert bis Ctrl-C)."""
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
        except ImportError as exc:
            raise RuntimeError(
                "Paket 'apscheduler' fehlt. Installiere mit: pip install arbfinder[agent]"
            ) from exc

        logger.info(
            "Recorder startet: provider=%s, alle %.1f min -> %s. Nur Aufzeichnung.",
            self.provider.name, interval_minutes, self.out_path,
        )
        sched = BlockingScheduler()
        sched.add_job(
            self.tick, trigger="interval", minutes=interval_minutes,
            id="record", next_run_time=datetime.now(timezone.utc),
        )
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Recorder gestoppt.")
            sched.shutdown(wait=False)
