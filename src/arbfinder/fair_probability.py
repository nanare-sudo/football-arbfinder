"""
Schaetzung der FAIREN Wahrscheinlichkeit — als austauschbares Modell.

Value Betting vergleicht die beste verfuegbare Quote gegen eine geschaetzte
faire Wahrscheinlichkeit. Diese Schaetzung ist der eigentliche Hebel (und das
eigentliche Risiko): ist sie schlecht, ist auch das Signal schlecht. Damit man
sie spaeter verbessern kann, OHNE die Strategie anzufassen, ist sie hinter einer
abstrakten Basisklasse gekapselt.

EHRLICHKEIT: Value Betting traegt ECHTES Risiko (kein Hedge wie bei Arbitrage).
Die erste Implementierung hier, ``ConsensusDevigModel``, ist nur ein GROBER
Schaetzer: sie nimmt an, der Markt sei im Mittel fair (Vig herausgerechnet) —
was er nicht immer ist. Ohne historische Quoten UND Ergebnisse misst der
Backtest auch hier nur Detektion, nicht Profitabilitaet.

Zirkularitaet vermeiden (wichtig!): Wenn die faire Wahrscheinlichkeit fuer eine
Quote von Bookie X beurteilt wird, darf X NICHT im Konsens stecken, gegen den
verglichen wird. Deshalb kann ``estimate`` einen Bookie ausschliessen
(Leave-one-out). Gibt es danach keinen unabhaengigen, vollstaendigen Bookie
mehr, liefert das Modell ``None`` -> kein Signal.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Mapping
import math

# Ausgang -> {Buchmacher -> Dezimalquote}
OutcomeOdds = Mapping[str, Mapping[str, float]]


class FairProbabilityModel(ABC):
    """Abstraktes Modell: schaetzt die faire Wahrscheinlichkeitsverteilung."""

    name: str = "base"

    @abstractmethod
    def estimate(
        self, odds: OutcomeOdds, *, exclude_bookie: str | Iterable[str] | None = None
    ) -> dict[str, float] | None:
        """Faire Wahrscheinlichkeit je Ausgang (Summe ~ 1) oder ``None``.

        Args:
            odds: Ausgang -> {Buchmacher -> Quote} des Marktes.
            exclude_bookie: ein Buchmacher ODER eine Menge von Buchmachern, die
                NICHT in den Konsens eingehen duerfen (Leave-one-out gegen
                Zirkularitaet — inkl. aller Anbieter, die die beurteilte Quote
                ebenfalls bieten).

        Returns:
            Verteilung ueber die Ausgaenge (Summe 1) oder ``None``, wenn kein
            belastbarer Konsens gebildet werden kann.
        """
        raise NotImplementedError


class ConsensusDevigModel(FairProbabilityModel):
    """Konsens-Devig: je vollstaendigem Bookie Vig herausrechnen, dann mitteln.

    Vorgehen:
    1. Outcome-Universum = alle Ausgaenge mit mindestens einer Quote.
    2. Pro Bookie, der ALLE diese Ausgaenge quotet: implizite
       Wahrscheinlichkeiten (1/Quote) bilden und auf Summe 1 normalisieren
       (= Vig herausrechnen -> "devigte" Sicht dieses Bookies).
    3. Ueber alle (nicht ausgeschlossenen) vollstaendigen Bookies mitteln.

    Unvollstaendige Bookies (quoten nicht alle Ausgaenge) gehen NICHT ein, weil
    ihre Normalisierung sonst verzerrt waere. Bleiben nach Ausschluss/Filterung
    weniger als ``min_books`` Bookies, gibt es keinen unabhaengigen Konsens
    -> ``None``.

    Hinweis zu ``min_books`` (Default 2): Nach Leave-one-out muessen MINDESTENS
    so viele unabhaengige, vollstaendige Bookies uebrig bleiben. Ein "Konsens"
    aus einem EINZIGEN Bookie ist keiner — gegen dessen blosse Vig-Struktur zu
    vergleichen erzeugt Rauschen, kein Signal. ``min_books=1`` daher nur bewusst
    setzen, wenn duenne Ein-Quellen-Signale ausdruecklich gewuenscht sind (sie
    wuerden von der Validierung ohnehin geparkt).
    """

    name = "consensus_devig"

    def __init__(self, min_books: int = 2) -> None:
        if min_books < 1:
            raise ValueError("min_books muss >= 1 sein (Konsens braucht >=1 Quelle).")
        self.min_books = min_books

    def estimate(
        self, odds: OutcomeOdds, *, exclude_bookie: str | Iterable[str] | None = None
    ) -> dict[str, float] | None:
        outcomes = [o for o, books in odds.items() if books]
        if len(outcomes) < 2:
            return None

        if exclude_bookie is None:
            excluded: set[str] = set()
        elif isinstance(exclude_bookie, str):
            excluded = {exclude_bookie}
        else:
            excluded = set(exclude_bookie)

        bookies: set[str] = set()
        for o in outcomes:
            bookies.update(odds[o].keys())
        bookies -= excluded

        distributions: list[dict[str, float]] = []
        for b in bookies:
            implied: dict[str, float] = {}
            complete = True
            for o in outcomes:
                price = odds[o].get(b)
                if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                    complete = False           # None/NaN/inf/<=0 -> Bookie unbrauchbar
                    break
                implied[o] = 1.0 / float(price)
            if not complete:                       # Bookie quotet nicht alle Ausgaenge
                continue
            s = sum(implied.values())
            if s <= 0:
                continue
            distributions.append({o: implied[o] / s for o in outcomes})  # Vig raus

        if len(distributions) < self.min_books:    # kein (unabhaengiger) Konsens
            return None

        n = len(distributions)
        fair = {o: sum(d[o] for d in distributions) / n for o in outcomes}
        total = sum(fair.values())
        if total <= 0:
            return None
        return {o: v / total for o, v in fair.items()}   # numerisch auf Summe 1
