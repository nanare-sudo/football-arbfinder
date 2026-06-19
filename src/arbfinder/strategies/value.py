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
import math

from arbfinder.fair_probability import (
    ConsensusDevigModel,
    FairProbabilityModel,
    PinnacleAnchorModel,
)
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
            # Nur endliche, positive Quoten sind verwertbar (NaN/inf/<=0 raus) —
            # sonst entstuenden stille Phantom-Signale (siehe CLAUDE.md).
            candidates = {
                bk: float(p) for bk, p in odds[outcome].items()
                if p is not None and math.isfinite(float(p)) and float(p) > 0
            }
            if not candidates:
                continue
            best_odd = max(candidates.values())
            best_bookie = max(candidates, key=lambda bk: candidates[bk])

            # Leave-one-out: fairen Konsens OHNE JEDEN Anbieter, der die beurteilte
            # (beste) Quote bietet — sonst steckt der beurteilte Preis im Konsens.
            tied = {bk for bk, p in candidates.items() if p == best_odd}
            fair = self.model.estimate(odds, exclude_bookie=tied)
            if not fair or outcome not in fair:
                continue       # kein unabhaengiger Konsens -> kein Signal

            p = fair[outcome]
            edge_pct = (best_odd * p - 1.0) * 100.0
            if not math.isfinite(edge_pct) or edge_pct < self.min_edge_pct:
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


@register
class PinnacleValueStrategy(Strategy):
    """Value gegen einen SCHARFEN Pinnacle-Anker (statt Konsens vieler Bookies).

    Signal, wenn die Bet-Quelle (z.B. Max-Eroeffnung) eine hoehere Quote bietet
    als die faire Pinnacle-Quote rechtfertigt:

        edge_pct = (bet_quote * faire_pinnacle_wkt - 1) * 100

    Der Anker ist eine SEPARATE Quelle (Pinnacle), nicht die Bet-Quelle -> kein
    Leave-one-out noetig. Die Pinnacle-SCHLUSSquote (PSC) wird ins Signal-meta
    gelegt, damit nachgelagert Closing Line Value (CLV) berechnet werden kann.
    Praediktiv -> requires_validation=True.
    """

    name = "pinnacle_value"
    requires_validation = True

    def __init__(
        self,
        min_edge_pct: float = 2.0,
        stake: float = 100.0,
        bet_source: str = "Max",
        anchor: str = "open",
        model: FairProbabilityModel | None = None,
        close_key: str = "PSC",
    ) -> None:
        self.min_edge_pct = min_edge_pct
        self.stake = stake
        self.bet_source = bet_source
        self.close_key = close_key
        self.model: FairProbabilityModel = model or PinnacleAnchorModel(anchor=anchor)

    def evaluate(self, ev: dict[str, Any]) -> list[Signal]:
        market = ev.get("market", "h2h")
        expected = int(ev.get("expected_outcomes", 0) or 0)
        odds: dict[str, dict[str, float]] = ev.get("odds", {})
        outcomes = [o for o, books in odds.items() if books]
        if (expected and len(outcomes) < expected) or len(outcomes) < 2:
            return []

        fair = self.model.estimate(odds)          # scharfer Pinnacle-Anker (devigt)
        if not fair:
            return []

        signals: list[Signal] = []
        for outcome in outcomes:
            books = odds[outcome]
            bet = books.get(self.bet_source)
            p = fair.get(outcome)
            if bet is None or float(bet) <= 0 or p is None:
                continue
            bet = float(bet)
            edge_pct = (bet * p - 1.0) * 100.0
            if not math.isfinite(edge_pct) or edge_pct < self.min_edge_pct:
                continue
            close = books.get(self.close_key)
            signals.append(Signal(
                event_id=str(ev.get("event_id", "")),
                event_name=str(ev.get("event_name", "")),
                market=market,
                kind="value",
                edge_pct=edge_pct,
                stakes={outcome: self.stake},     # EIN Ausgang, kein Hedge
                meta={
                    "outcome": outcome,
                    "fair_prob": round(p, 4),
                    "best": [self.bet_source, bet],
                    "model": getattr(self.model, "name", "?"),
                    "clv_close": float(close) if close is not None else None,
                },
            ))
        return signals
