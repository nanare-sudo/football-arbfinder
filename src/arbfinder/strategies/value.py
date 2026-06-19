"""
Value-Betting-Strategie — die erste PRAEDIKTIVE Strategie (requires_validation=True).

Idee: Setze auf einen EINZELNEN Ausgang, wenn die beste verfuegbare Quote eine
hoehere Auszahlung verspricht, als die geschaetzte FAIRE Wahrscheinlichkeit
rechtfertigt:

    edge_pct = (beste_quote * faire_wahrscheinlichkeit - 1) * 100

EHRLICHKEIT (anders als Arbitrage!): Value Betting traegt ECHTES Risiko. Es gibt
KEINEN Hedge — man setzt auf eine einzelne Quote und kann verlieren. Alles haengt
an der Qualitaet des fairen-Wahrscheinlichkeits-Modells (siehe fair_probability).
Deshalb ist diese Strategie ``requires_validation=True``: ihre Signale muessen
durch validation.judge (dreistufig: confirmed/parked/rejected) — anders als reine
Arbitrage, die eine mathematische Tatsache ist.

Zirkularitaet: die faire Wahrscheinlichkeit fuer den besten Quote-Anbieter wird
per Leave-one-out OHNE genau diesen Anbieter geschaetzt (fair_probability).

Muster wie strategies/arbitrage.py: pro Markttyp getrennt (ein Snapshot = ein
Markt), Vollstaendigkeit respektieren, Signale mit edge_pct zurueckgeben. Nur
ERKENNEN/MELDEN, nie setzen.
"""
from __future__ import annotations

from typing import Any

from arbfinder.fair_probability import ConsensusDevigModel, FairProbabilityModel
from arbfinder.strategies.base import Signal, Strategy
from arbfinder.strategies.registry import register


@register
class ValueStrategy(Strategy):
    name = "value"
    # Praediktiv -> braucht Out-of-Sample-Validierung (Default True bleibt stehen).
    requires_validation = True

    def __init__(
        self,
        min_edge_pct: float = 2.0,
        stake: float = 100.0,
        model: FairProbabilityModel | None = None,
    ) -> None:
        self.min_edge_pct = min_edge_pct
        self.stake = stake
        self.model: FairProbabilityModel = model or ConsensusDevigModel()

    def evaluate(self, ev: dict[str, Any]) -> list[Signal]:
        market = ev.get("market", "h2h")
        expected = int(ev.get("expected_outcomes", 0) or 0)
        odds: dict[str, dict[str, float]] = ev.get("odds", {})

        # Vollstaendigkeit wie in arbitrage.py: nur Ausgaenge mit echter Quote.
        outcomes = [o for o, books in odds.items() if books]
        if expected and len(outcomes) < expected:
            return []          # bewusst verworfen (detector zaehlt das)
        if len(outcomes) < 2:
            return []

        signals: list[Signal] = []
        for outcome in outcomes:
            books = odds[outcome]
            best_bookie, best_odd = max(books.items(), key=lambda kv: float(kv[1]))
            best_odd = float(best_odd)

            # Leave-one-out: fairen Konsens OHNE den Anbieter der besten Quote.
            fair = self.model.estimate(odds, exclude_bookie=best_bookie)
            if not fair or outcome not in fair:
                continue       # kein unabhaengiger Konsens -> kein Signal

            p = fair[outcome]
            edge_pct = (best_odd * p - 1.0) * 100.0
            if edge_pct < self.min_edge_pct:
                continue

            signals.append(Signal(
                event_id=str(ev.get("event_id", "")),
                event_name=str(ev.get("event_name", "")),
                market=market,
                kind="value",
                edge_pct=edge_pct,
                stakes={outcome: self.stake},   # EIN Ausgang, kein Hedge
                meta={
                    "outcome": outcome,
                    "fair_prob": round(p, 4),
                    "best": [best_bookie, best_odd],
                    "model": getattr(self.model, "name", "?"),
                },
            ))
        return signals
