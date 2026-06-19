"""
Detektions-Pipeline: vom Provider bis zu gemeldeten Signalen.

Ablauf (siehe CLAUDE.md, Schritt 4):

    Provider abfragen
      -> normalisieren & Duplikate zusammenfuehren (normalize.merge_events)
      -> PRO MARKTTYP getrennt auswerten (Event.to_snapshots: ein Snapshot je
         Markt; beste Quote je Ausgang waehlt die Strategie via find_arbitrage)
      -> Vollstaendigkeit pruefen: unvollstaendige Maerkte werden GEZAEHLT
         (skipped_incomplete), nicht still geschluckt -> Phantom-Arb-Schutz
      -> Strategie laufen lassen
      -> nach Mindest-Profit filtern (uebernimmt die Strategie selbst)

Die Zaehler (geprueft / verworfen / Signale) werden geloggt und im Ergebnis
zurueckgegeben — Datenqualitaet ist eine eigene Metrik, kein Rauschen.

Dies MELDET nur. Es platziert NIE Wetten (harte Leitplanke).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
import logging

from arbfinder.models import Event, count_priced_outcomes
from arbfinder.normalize import merge_events
from arbfinder.providers.base import OddsProvider
from arbfinder.strategies import Signal, get

logger = logging.getLogger("arbfinder.detector")


@dataclass
class DetectionResult:
    """Ergebnis eines Detektionslaufs inkl. Datenqualitaets-Zaehler."""

    provider: str
    strategy: str
    events_in: int            # Roh-Events vom Provider
    events_merged: int        # nach Normalisierung/Zusammenfuehrung
    markets_checked: int      # ausgewertete Markt-Snapshots
    skipped_incomplete: int   # Maerkte wegen unvollstaendiger Abdeckung verworfen
    skipped_no_market: int    # Maerkte mit < 2 Ausgaengen (unbrauchbar)
    signals: list[Signal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signals"] = [asdict(s) for s in self.signals]
        d["n_signals"] = len(self.signals)
        return d


def detect(
    provider: OddsProvider,
    *,
    strategy_name: str = "arbitrage",
    min_profit_pct: float = 0.0,
    known: Iterable[str] | None = None,
    time_tolerance_minutes: float = 90.0,
    **strategy_kwargs: Any,
) -> DetectionResult:
    """Fuehrt die komplette Pipeline aus und liefert Signale + Zaehler."""
    raw: list[Event] = provider.fetch_events()
    merged = merge_events(raw, known, time_tolerance_minutes=time_tolerance_minutes)

    strat = get(strategy_name)
    if hasattr(strat, "min_profit_pct"):
        strat.min_profit_pct = min_profit_pct
    for k, v in strategy_kwargs.items():
        setattr(strat, k, v)

    signals: list[Signal] = []
    checked = skipped_incomplete = skipped_no_market = 0

    for ev in merged:
        for snap in ev.to_snapshots():            # ein Snapshot je Markttyp
            checked += 1
            odds = snap.get("odds", {})
            present = count_priced_outcomes(odds)
            expected = int(snap.get("expected_outcomes", 0) or 0)

            # Vollstaendigkeit: fehlt ein erwarteter Ausgang -> Phantom-Arb-Gefahr.
            if expected and present < expected:
                skipped_incomplete += 1
                logger.debug(
                    "verworfen (unvollstaendig): %s [%s] %d/%d Ausgaenge",
                    snap.get("event_name"), snap.get("market"), present, expected,
                )
                continue
            # Ohne mind. 2 Ausgaenge ist keine Arbitrage moeglich.
            if present < 2:
                skipped_no_market += 1
                continue

            signals.extend(strat.evaluate(snap))

    result = DetectionResult(
        provider=provider.name,
        strategy=strategy_name,
        events_in=len(raw),
        events_merged=len(merged),
        markets_checked=checked,
        skipped_incomplete=skipped_incomplete,
        skipped_no_market=skipped_no_market,
        signals=signals,
    )
    logger.info(
        "provider=%s strategy=%s events_in=%d merged=%d checked=%d "
        "skipped_incomplete=%d skipped_no_market=%d signals=%d",
        result.provider, result.strategy, result.events_in, result.events_merged,
        result.markets_checked, result.skipped_incomplete, result.skipped_no_market,
        len(result.signals),
    )
    return result
