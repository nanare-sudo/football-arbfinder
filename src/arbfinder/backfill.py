"""
Historischer Backfill ueber The Odds API (fuer die Arbitrage-Detektion).

Statt wochenlang vorwaerts zu sammeln, holt dieser Modul historische Quoten-
Snapshots ueber einen Zeitraum und haengt sie ins bestehende JSONL-Format an
(ts = echte Snapshot-Zeit, commence_time, odds je Bookie, expected_outcomes) —
sofort backtestbar mit der Arbitrage-Strategie.

>>> KOSTEN-WARNUNG <<<
    Der historische Endpoint kostet ~10x Credits pro Call. Deshalb sind Zeitraum
    UND Intervall PFLICHT (keine Defaults), die geschaetzten Kosten werden VORHER
    laut geloggt, und eine Sicherheits-Obergrenze (``max_snapshots``) bricht ab,
    bevor versehentlich das ganze Kontingent verbrennt. Bricht das Kontingent
    waehrenddessen auf 0, stoppt der Backfill. API-Key nur aus ODDS_API_KEY,
    nie in Fehlermeldungen/URLs (Redaction-Guard im Provider greift auch hier).

>>> EHRLICHE EINORDNUNG <<<
    5-10-Minuten-Snapshots bedeuten "zu diesem Zeitpunkt EXISTIERTE eine Arb",
    NICHT "die Arb war lange genug fuer eine reale Platzierung ausfuehrbar". Der
    historische Arbitrage-Backtest liefert also eine OBERGRENZE gefundener Arbs;
    die real setzbaren sind nur ein Teil davon. Nicht als "so viel haetten wir
    verdient" lesen — das misst Detektion (Arb existierte), nicht Ausfuehrbarkeit.
    Ausserdem braucht ein aussagekraeftiger Arb-Backtest das volle Bookie-Set
    (z.B. Pinnacle), das nur hoehere API-Plaene liefern — sonst fehlen Arbs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import logging

from arbfinder.providers.theoddsapi import TheOddsApiProvider
from arbfinder.recorder import _event_rows, _iso_utc, append_jsonl

logger = logging.getLogger("arbfinder.backfill")

# Historischer Endpoint kostet ~10x Credits pro Call.
HISTORICAL_CREDIT_MULTIPLIER = 10
# Standard-Obergrenze gegen versehentliches Verbrennen des Kontingents.
DEFAULT_MAX_SNAPSHOTS = 100


@dataclass
class BackfillStats:
    """Zusammenfassung eines Backfill-Laufs."""

    snapshots: int             # erfolgreich geholte Snapshots
    rows: int                  # geschriebene Zeilen
    skipped: int               # fehlgeschlagene Snapshots (skip-and-log)
    estimated_credits: int     # vorab geschaetzte Kosten
    credits_remaining: str | None
    credits_used: str | None


def _timestamps(start: datetime, end: datetime, interval_minutes: float) -> list[datetime]:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes muss > 0 sein.")
    if end < start:
        raise ValueError("end muss >= start sein.")
    step = timedelta(minutes=interval_minutes)
    out, t = [], start
    while t <= end:
        out.append(t)
        t += step
    return out


def backfill(
    provider: TheOddsApiProvider,
    *,
    start: datetime,
    end: datetime,
    interval_minutes: float,
    out_path: str | Path,
    max_snapshots: int | None = DEFAULT_MAX_SNAPSHOTS,
) -> BackfillStats:
    """Zieht historische Snapshots ueber [start, end] und haengt sie an ``out_path``.

    ``start``, ``end`` und ``interval_minutes`` sind PFLICHT (keyword-only, keine
    Defaults) — bewusst, damit niemand versehentlich einen riesigen Zeitraum
    abfragt. Die geschaetzten Credits werden vor dem ersten Call geloggt; uebersteigt
    der Lauf ``max_snapshots``, bricht er VOR jedem Call mit klarer Meldung ab
    (bewusst ``max_snapshots`` erhoehen, um es wirklich zu tun). Append-only,
    robust gegen einzelne fehlerhafte Antworten (skip-and-log).
    """
    stamps = _timestamps(start, end, interval_minutes)
    n_markets = max(1, len([m for m in provider.markets.split(",") if m]))
    n_regions = max(1, len([r for r in provider.regions.split(",") if r]))
    est = len(stamps) * HISTORICAL_CREDIT_MULTIPLIER * n_markets * n_regions

    logger.warning(
        "Backfill: %d Snapshots (%s bis %s, alle %.0f min) — geschaetzte Kosten ~%d Credits "
        "(historisch = %dx pro Call).",
        len(stamps), _iso_utc(start), _iso_utc(end), interval_minutes, est,
        HISTORICAL_CREDIT_MULTIPLIER,
    )
    if max_snapshots is not None and len(stamps) > max_snapshots:
        raise ValueError(
            f"Backfill umfasst {len(stamps)} Snapshots > max_snapshots={max_snapshots} "
            f"(~{est} Credits). Erhoehe max_snapshots bewusst, um das wirklich zu tun."
        )

    snapshots = rows = skipped = 0
    for ts in stamps:
        try:
            events, snap_ts = provider.fetch_historical(ts)
        except Exception as exc:  # noqa: BLE001 - skip-and-log, Backfill darf nicht sterben
            skipped += 1
            logger.warning("Snapshot %s uebersprungen: %s", _iso_utc(ts), exc)
            continue
        snapshots += 1

        batch: list[dict] = []
        queried_at = snap_ts or ts            # echte Snapshot-Zeit der API bevorzugen
        for ev in events:
            try:
                batch.extend(_event_rows(ev, queried_at))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Event uebersprungen (Serialisierung): %s", exc)
        rows += append_jsonl(out_path, batch)

        remaining = (provider.last_quota or {}).get("remaining")
        if remaining is not None and str(remaining) == "0":
            logger.warning("API-Kontingent erschoepft (remaining=0) — Backfill bricht ab.")
            break

    quota = provider.last_quota or {}
    logger.info(
        "Backfill fertig: %d Snapshots, %d Zeilen, %d uebersprungen | Kontingent %s",
        snapshots, rows, skipped, quota or "unbekannt",
    )
    return BackfillStats(snapshots, rows, skipped, est,
                         quota.get("remaining"), quota.get("used"))
