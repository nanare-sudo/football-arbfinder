"""
Referenz-Strategie (FERTIG) — dient dem Agenten als VORBILD zum Nachbauen.

Sie zeigt das Muster, nach dem neue Strategien gebaut werden:
1. Quoten je Ausgang ueber Bookies sammeln (beste nehmen)
2. Vollstaendigkeit pruefen (alle erwarteten Ausgaenge da?)
3. Metrik berechnen (hier: Arbitrage-Marge)
4. Signal mit edge_pct zurueckgeben
"""
from __future__ import annotations
from typing import Any
from arbfinder.arbitrage import Quote, find_arbitrage, allocate_stakes
from arbfinder.strategies.base import Strategy, Signal
from arbfinder.strategies.registry import register


@register
class ArbitrageStrategy(Strategy):
    name = "arbitrage"
    # Reine Arbitrage: Marge < 1 ist eine mathematische Tatsache, kein
    # Overfitting -> keine Out-of-Sample-Validierung noetig (siehe validation.py).
    requires_validation = False

    def __init__(self, min_profit_pct: float = 0.0, stake: float = 1000.0) -> None:
        self.min_profit_pct = min_profit_pct
        self.stake = stake

    def evaluate(self, ev: dict[str, Any]) -> list[Signal]:
        market = ev.get("market", "h2h")
        expected = int(ev.get("expected_outcomes", 0))
        quotes: list[Quote] = []
        for sel, books in ev.get("odds", {}).items():
            for bookie, price in books.items():
                quotes.append(Quote(sel, bookie, float(price)))

        # Vollstaendigkeit: ohne alle Ausgaenge ist die Marge wertlos.
        outcomes = {q.outcome for q in quotes}
        if expected and len(outcomes) < expected:
            return []  # bewusst verworfen (detector zaehlt das spaeter)

        if len(outcomes) < 2:
            return []

        opp = find_arbitrage(ev.get("event_name", ev.get("event_id", "?")), quotes)
        if not opp.is_arbitrage or opp.profit_pct < self.min_profit_pct:
            return []

        return [Signal(
            event_id=str(ev.get("event_id", "")),
            event_name=str(ev.get("event_name", "")),
            market=market,
            kind="arbitrage",
            edge_pct=opp.profit_pct,
            stakes=allocate_stakes(opp, self.stake),
            meta={"margin": round(opp.margin, 4),
                  "legs": {l.outcome: [l.bookmaker, l.decimal_odds] for l in opp.legs}},
        )]
