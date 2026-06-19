"""
Backtest-/Eval-Harness — DAS Werkzeug, das den Agenten autonom macht.

Es laeuft eine Strategie ueber aufgezeichnete Markt-Snapshots und berechnet
Metriken. Ohne so ein Harness ist "finde Verbesserungen" nur Raten.

Datenformat (fixtures/recorded_odds.jsonl), eine Zeile = ein Event-Snapshot:
  {
    "ts": "2026-08-15T13:00:00Z",        # Zeitpunkt des Snapshots
    "event_id": "...", "event_name": "...",
    "market": "h2h", "expected_outcomes": 3,
    "odds": {"Heim": {"BookieA": 2.05, "BookieB": 2.10}, "X": {...}, "Auswaerts": {...}},
    "result": "Heim"                      # tatsaechlicher Ausgang, falls bekannt (sonst null)
  }

Metriken:
  - signals: wie viele Signale die Strategie ausgeloest hat
  - avg_edge_pct: durchschnittlicher behaupteter Vorteil
  - realized_pnl: NUR wenn 'result' vorhanden — echte Gewinn/Verlust-Simulation.
    Hier zeigt sich, ob "edge" auch real eintritt oder nur Datenrauschen war.
  - skipped_incomplete: Events, die wegen unvollstaendiger Abdeckung verworfen
    wurden (Phantom-Arb-Schutz). Hoher Wert = Datenproblem, nicht Strategie-Problem.

EHRLICHKEIT: Auf reinen Quoten-Snapshots OHNE 'result' kann das Harness nur
DETEKTIONS-Korrektheit messen, nicht Profitabilitaet. Echte Profit-Backtests
brauchen historische Quoten UND Ergebnisse — die musst du beschaffen.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from arbfinder.models import count_priced_outcomes
from arbfinder.strategies import get
from arbfinder.validation import Verdict, judge


@dataclass
class BacktestResult:
    strategy: str
    events: int
    signals: int
    avg_edge_pct: float
    skipped_incomplete: int
    realized_pnl: float | None       # None, wenn keine Ergebnisse in den Daten
    n_with_result: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_snapshots(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            rows.append(json.loads(line))
    return rows


def _simulate_pnl(signal_stakes: dict[str, float], result: str, odds: dict) -> float:
    """Setzt die geplanten Einsaetze, schaut welcher Ausgang eintrat -> PnL."""
    total_in = sum(signal_stakes.values())
    if result not in signal_stakes:
        return -total_in  # auf den Ausgang gar nicht gesetzt -> Totalverlust der Einsaetze
    vals = [v for v in odds.get(result, {}).values() if v and v > 0]
    if not vals:
        # Auf den Ausgang gesetzt, aber im Snapshot kein bepreister Buchmacher:
        # nicht einloesbar -> als Verlust verbuchen (statt mit max() zu crashen).
        return -total_in
    payout = signal_stakes[result] * max(vals)
    return payout - total_in


def run(strategy_name: str, snapshots_path: str | Path, **kwargs) -> BacktestResult:
    strat = get(strategy_name)
    for k, v in kwargs.items():
        setattr(strat, k, v)

    rows = load_snapshots(snapshots_path)
    signals_total = 0
    edge_sum = 0.0
    skipped = 0
    pnl = 0.0
    n_result = 0

    for ev in rows:
        sigs = strat.evaluate(ev)
        if not sigs:
            # Vollstaendigkeit GENAU wie der detector zaehlen: nur Ausgaenge mit
            # echter Quote (leere Bookie-Map zaehlt nicht), damit skipped_incomplete
            # nicht stillschweigend schwaecher misst als der eigentliche Schutz.
            present = count_priced_outcomes(ev.get("odds", {}))
            if ev.get("expected_outcomes", 0) and present < ev["expected_outcomes"]:
                skipped += 1
            continue
        for s in sigs:
            signals_total += 1
            edge_sum += s.edge_pct
            if ev.get("result"):
                n_result += 1
                pnl += _simulate_pnl(s.stakes, ev["result"], ev.get("odds", {}))

    return BacktestResult(
        strategy=strategy_name,
        events=len(rows),
        signals=signals_total,
        avg_edge_pct=round(edge_sum / signals_total, 3) if signals_total else 0.0,
        skipped_incomplete=skipped,
        realized_pnl=round(pnl, 2) if n_result else None,
        n_with_result=n_result,
    )


def make_verdict(
    strategy_name: str,
    result: BacktestResult,
    *,
    n_trials: int = 1,
    out_of_sample_edge: float | None = None,
    **judge_kwargs: Any,
) -> Verdict:
    """Faellt das validation.judge-Urteil fuer einen Backtest.

    Reine Arbitrage (``requires_validation=False``) ist eine mathematische
    Tatsache -> bei positivem in-sample Edge "confirmed", sonst "rejected", OHNE
    Out-of-Sample-Pruefung. Praediktive Strategien durchlaufen die dreistufige
    Pruefung; fehlt OOS-Evidenz, lautet das Urteil "parked" (NICHT verworfen).

    ``in_sample_edge`` ist der durchschnittliche behauptete Vorteil
    (``avg_edge_pct``); ``n_trials`` zaehlt getestete Varianten (Deflationierung,
    nur informativ).
    """
    strat = get(strategy_name)
    requires_validation = getattr(strat, "requires_validation", True)
    return judge(
        in_sample_edge=result.avg_edge_pct,
        out_of_sample_edge=out_of_sample_edge,
        n_trials=n_trials,
        requires_validation=requires_validation,
        **judge_kwargs,
    )


def main(argv: list[str] | None = None) -> None:
    import argparse
    p = argparse.ArgumentParser(description="Backtest einer Strategie")
    p.add_argument("--strategy", default="arbitrage")
    p.add_argument("--data", default="fixtures/recorded_odds.jsonl")
    p.add_argument("--out", default="results/last_backtest.json")
    args = p.parse_args(argv)

    res = run(args.strategy, args.data)
    verdict = make_verdict(args.strategy, res)

    out = res.to_dict()                 # Metriken bleiben top-level (plotting!)
    out["verdict"] = verdict.to_dict()  # Urteil daneben mit reingeschrieben
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nUrteil ({args.strategy}): {verdict.status.upper()} — {verdict.reason}")


if __name__ == "__main__":
    main()
