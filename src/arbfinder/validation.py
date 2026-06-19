"""
validation.py — Schutz vor Backtest-Selbstbetrug, OHNE gute Strategien
vorschnell zu verwerfen.

Hintergrund (Lopez de Prado, "Advances in Financial Machine Learning"):
Wer genug Varianten testet, findet immer eine, die in-sample gut aussieht.
Das ist nicht zwingend echt. ABER: eine zu harte Diskontierung wirft auch
echte Edges weg. Deshalb hier KEIN hartes Fallbeil, sondern ein DREISTUFIGES
Urteil:

    "confirmed"  -> in-sample Signal UND out-of-sample robust  -> behalten
    "parked"     -> vielversprechend, aber zu wenig Belege      -> mehr Daten holen
    "rejected"   -> auch in-sample kein Signal                  -> raus

Nur 'rejected' fliegt sofort. 'parked' bleibt am Leben, bis die Datenlage
entscheidet. So ist der Einwand "wir verwerfen Gutes zu schnell" entschaerft.

WICHTIGE KONTEXT-UNTERSCHEIDUNG:
- Reine ARBITRAGE ist eine mathematische Tatsache (Marge < 1 oder nicht).
  Da gibt es kein Overfitting -> requires_validation=False, immer "confirmed",
  sofern ein echtes Arb vorliegt.
- PRAEDIKTIVE Strategien (Value Betting, "verschwindet die Arb vor dem Setzen?")
  koennen Zufall fuer echt halten -> hier greift die dreistufige Pruefung.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import math


@dataclass
class Verdict:
    status: str                      # "confirmed" | "parked" | "rejected"
    reason: str
    in_sample_edge: float
    out_of_sample_edge: float | None
    n_trials: int
    deflated_edge: float | None      # informativ, NICHT als alleiniges Fallbeil
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def deflate(edge: float, n_trials: int, edge_std: float | None = None) -> float:
    """
    Weicher Abschlag fuer Mehrfachtests: je mehr Varianten getestet, desto
    skeptischer. BEWUSST mild gehalten (log statt linear), damit er gute
    Strategien nicht plattmacht. Nur informativ, nicht allein entscheidend.

    Heuristik: edge wird um einen Term reduziert, der mit log(n_trials) und
    der Streuung der Versuche waechst. Bei n_trials<=1 keine Korrektur.
    """
    if n_trials <= 1:
        return edge
    penalty_scale = edge_std if (edge_std and edge_std > 0) else max(abs(edge) * 0.25, 0.1)
    penalty = penalty_scale * math.sqrt(math.log(n_trials))
    return edge - penalty


def judge(
    *,
    in_sample_edge: float,
    out_of_sample_edge: float | None,
    n_trials: int,
    requires_validation: bool = True,
    edge_std: float | None = None,
    min_in_sample: float = 0.0,
    min_out_of_sample: float = 0.0,
    min_samples: int = 0,
    n_samples: int = 0,
) -> Verdict:
    """
    Faellt das dreistufige Urteil.

    - requires_validation=False (z.B. reine Arbitrage): kein Overfitting moeglich.
      Bei positivem in-sample Edge -> "confirmed", sonst "rejected".
    - Sonst: braucht zusaetzlich ein robustes Out-of-Sample-Ergebnis.
      Fehlen Daten (zu wenig Samples ODER kein OOS-Wert) -> "parked", nicht raus.
    """
    deflated = deflate(in_sample_edge, n_trials, edge_std) if requires_validation else None

    # Fall 1: nichts mal in-sample -> raus.
    if in_sample_edge <= min_in_sample:
        return Verdict("rejected", "kein Signal in-sample", in_sample_edge,
                       out_of_sample_edge, n_trials, deflated)

    # Fall 2: Arbitrage o.ae. ohne Vorhersage -> sofort bestaetigt.
    if not requires_validation:
        return Verdict("confirmed", "mathematische Tatsache, kein Overfitting-Risiko",
                       in_sample_edge, out_of_sample_edge, n_trials, deflated)

    # Fall 3: zu wenig Belege fuer ein endgueltiges Urteil -> parken, nicht verwerfen.
    if n_samples < min_samples or out_of_sample_edge is None:
        return Verdict("parked", "vielversprechend, aber zu wenig Daten fuer OOS-Urteil",
                       in_sample_edge, out_of_sample_edge, n_trials, deflated,
                       details={"n_samples": n_samples, "min_samples": min_samples})

    # Fall 4: out-of-sample haelt es nicht -> wahrscheinlich Overfit -> parken
    # (NICHT hart verwerfen: koennte an OOS-Fenster/Datenmenge liegen).
    if out_of_sample_edge <= min_out_of_sample:
        return Verdict("parked", "in-sample stark, out-of-sample schwach -> erst mehr Daten",
                       in_sample_edge, out_of_sample_edge, n_trials, deflated)

    # Fall 5: in- UND out-of-sample tragen -> bestaetigt.
    return Verdict("confirmed", "robust in- und out-of-sample", in_sample_edge,
                   out_of_sample_edge, n_trials, deflated)


# --- Fuer den Tag, an dem eine LERNENDE Strategie kommt -------------------
def purged_split(n: int, k: int = 5, embargo: float = 0.01) -> list[tuple[list[int], list[int]]]:
    """
    Purged K-Fold Split (Lopez de Prado) fuer Zeitreihen: entfernt Trainings-
    Indizes rund um das Testfenster, um Leakage zu vermeiden. embargo blockt
    zusaetzlich einen kleinen Anteil direkt NACH dem Testfenster.

    Erst noetig, wenn eine Strategie aus historischen Quoten ein Modell lernt
    (z.B. faire Wahrscheinlichkeit fuer Value Betting). Fuer den reinen
    Arbitrage-Scanner NICHT gebraucht.
    """
    idx = list(range(n))
    fold = n // k
    emb = int(n * embargo)
    splits = []
    for i in range(k):
        start, end = i * fold, (n if i == k - 1 else (i + 1) * fold)
        test = idx[start:end]
        purge_lo, purge_hi = start, min(n, end + emb)
        train = idx[:purge_lo] + idx[purge_hi:]
        splits.append((train, test))
    return splits
