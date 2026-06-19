"""Mathe-Kern (fertig, in jedem Repo identisch)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    outcome: str
    bookmaker: str
    decimal_odds: float

    @property
    def implied_prob(self) -> float:
        return 1.0 / self.decimal_odds


@dataclass(frozen=True)
class ArbOpportunity:
    event: str
    legs: list[Quote]
    margin: float
    profit_pct: float

    @property
    def is_arbitrage(self) -> bool:
        return self.margin < 1.0


def find_arbitrage(event: str, quotes: list[Quote]) -> ArbOpportunity:
    best: dict[str, Quote] = {}
    for q in quotes:
        cur = best.get(q.outcome)
        if cur is None or q.decimal_odds > cur.decimal_odds:
            best[q.outcome] = q
    legs = list(best.values())
    margin = sum(q.implied_prob for q in legs)
    profit_pct = (1.0 / margin - 1.0) * 100.0 if margin > 0 else 0.0
    return ArbOpportunity(event, legs, margin, profit_pct)


def allocate_stakes(opp: ArbOpportunity, total: float) -> dict[str, float]:
    return {l.outcome: round(total * l.implied_prob / opp.margin, 2) for l in opp.legs}
