"""
Pinnacle-Anker + Closing Line Value (CLV) — die verteidigbare Value-Analyse.

Der Konsens-Devig-Anker ist als Artefakt entlarvt (Vorzeichenwechsel ueber
Saisons). Hier ankern wir an Pinnacle (dem schaerfsten Buchmacher) und machen
**CLV die PRIMAERE Metrik**: vergleiche die genommene Bet-Quote mit der
Pinnacle-SCHLUSSquote desselben Ausgangs.

    CLV pro Wette = (genommene_quote / pinnacle_schlussquote) - 1   (in %)

Warum CLV primaer ist: Wer konsistent eine bessere Quote als die scharfe
Schlusslinie bekommt, hat echten Vorteil. CLV braucht KEINE Spielergebnisse und
ist viel weniger verrauscht als PnL. Negatives/null CLV trotz positivem
In-Sample-PnL => der PnL war Glueck, kein Edge. PnL (Bankroll, Drawdown, Ruin,
Stress-Checks) bleibt SEKUNDAERE Bestaetigung.

EHRLICHE EINORDNUNG (gilt durchgehend):
- Die EPL ist einer der EFFIZIENTESTEN Maerkte — der haerteste Ort fuer Value.
  Ein hier ueberlebender Edge ist echt; ein hier DUENNER Edge schliesst die
  Methode auf weniger liquiden Ligen NICHT aus.
- Pinnacle-SCHLUSS ist extrem effizient; ihn zu schlagen (positives CLV) ist
  eine starke, aber haltbare Behauptung.
- Kosten (Steuern/Gebuehren) und Buchmacher-LIMITS sind NICHT modelliert.
- IN-SAMPLE bleibt in-sample: der Pinnacle-Anker lernt nichts; CLV ist der
  ehrlichste verfuegbare Vorlaufindikator, kein Out-of-Sample-Beweis.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any, Callable, Iterable
import logging
import math

from arbfinder.diagnostics import BetRecord, SimResult, _odds_bucket, _season, simulate
from arbfinder.fair_probability import ConsensusDevigModel, PinnacleAnchorModel
from arbfinder.providers.footballdata import (
    PINNACLE_CLOSE_KEY,
    load_pinnacle_events,
)

logger = logging.getLogger("arbfinder.pinnacle")

# Aggregat-Spalten gehoeren NICHT in den unabhaengigen Konsens-Pool.
_CONSENSUS_EXCLUDE = {"Max", "Avg", "BbMx", "BbAv", PINNACLE_CLOSE_KEY}
# CLV-Verteilungs-Buckets (in %).
_CLV_BINS = ("<-5", "-5..-2", "-2..0", "0..2", "2..5", ">5")


@dataclass
class PinnBet(BetRecord):
    """Value-Wette mit Closing Line Value gegen die Pinnacle-Schlussquote."""

    clv_pct: float | None = None
    pinnacle_close: float | None = None


def _r(x: Any, n: int = 3) -> float | None:
    """Rundet; gibt None statt NaN/inf (JSON-tauglich)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, n) if math.isfinite(f) else None


def _clv_bucket(c: float) -> str:
    if c < -5:
        return "<-5"
    if c < -2:
        return "-5..-2"
    if c < 0:
        return "-2..0"
    if c < 2:
        return "0..2"
    if c < 5:
        return "2..5"
    return ">5"


# --------------------------------------------------------------------------- #
# Wetten bauen (eine Engine, Fair-Modell austauschbar)
# --------------------------------------------------------------------------- #
def _build_bets(
    events: list,
    fair_fn: Callable[[dict], dict | None],
    *,
    bet_source: str,
    min_edge: float,
) -> list[PinnBet]:
    """Selektiert Value-Wetten: edge = bet_quote * fair - 1 >= min_edge.

    ``fair_fn(odds) -> {outcome: fair_prob} | None`` kapselt den Anker (Pinnacle
    oder Konsens). CLV = bet_quote / Pinnacle-Schluss - 1 (braucht kein Ergebnis).
    """
    bets: list[PinnBet] = []
    for ev in events:
        odds = ev.markets[0].odds if ev.markets else {}
        fair = fair_fn(odds)
        if not fair:
            continue
        season = _season(ev.start_time)
        for outcome, books in odds.items():
            if not books:
                continue
            bet = books.get(bet_source)
            p = fair.get(outcome)
            if bet is None or float(bet) <= 0 or p is None:
                continue
            bet = float(bet)
            edge_pct = (bet * p - 1.0) * 100.0
            if not math.isfinite(edge_pct) or edge_pct < min_edge:
                continue
            close = books.get(PINNACLE_CLOSE_KEY)
            clv = ((bet / float(close) - 1.0) * 100.0) if (close and float(close) > 0) else None
            won = None if ev.result is None else (ev.result == outcome)
            bets.append(PinnBet(
                commence_time=ev.start_time, season=season, event_name=ev.name,
                outcome=outcome, bookie=bet_source, odd=bet, fair_prob=float(p),
                won=won if won is not None else False,
                clv_pct=clv, pinnacle_close=float(close) if close else None,
            ))
            if won is None:                              # Markierung: nicht settled
                bets[-1].won = None  # type: ignore[assignment]
    bets.sort(key=lambda b: b.commence_time)
    return bets


def _consensus_fair_fn(bet_source: str) -> Callable[[dict], dict | None]:
    """Alter Konsens-Anker: Devig ueber die EROEFFNUNGS-Bookies (ohne Aggregate),
    Leave-one-out gegen die Bet-Quelle."""
    model = ConsensusDevigModel(min_books=2)

    def fair(odds: dict) -> dict | None:
        sub = {o: {b: p for b, p in books.items() if b not in _CONSENSUS_EXCLUDE}
               for o, books in odds.items()}
        return model.estimate(sub, exclude_bookie=bet_source)

    return fair


# --------------------------------------------------------------------------- #
# CLV-Statistik (PRIMAER)
# --------------------------------------------------------------------------- #
def clv_stats(bets: list[PinnBet]) -> dict[str, Any]:
    clvs = [b.clv_pct for b in bets if b.clv_pct is not None]
    if not clvs:
        return {"share_positive_pct": None, "mean_clv_pct": None,
                "median_clv_pct": None, "n": 0, "distribution_buckets": []}
    counts = {b: 0 for b in _CLV_BINS}
    for c in clvs:
        counts[_clv_bucket(c)] += 1
    return {
        "share_positive_pct": _r(sum(1 for c in clvs if c > 0) / len(clvs) * 100.0, 1),
        "mean_clv_pct": _r(sum(clvs) / len(clvs)),
        "median_clv_pct": _r(median(clvs)),
        "n": len(clvs),
        "distribution_buckets": [{"bucket": b, "n": counts[b]} for b in _CLV_BINS],
    }


# --------------------------------------------------------------------------- #
# Gruppen-Aufschluesselung (PnL aus dem flat-Lauf + CLV der Gruppe)
# --------------------------------------------------------------------------- #
def _grouped(placed, key: Callable[[PinnBet], str], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for pb in placed:
        g = groups.setdefault(key(pb.record), {"n": 0, "pnl": 0.0, "turnover": 0.0, "clvs": []})
        g["n"] += 1
        g["pnl"] += pb.pnl
        g["turnover"] += pb.stake
        if pb.record.clv_pct is not None:
            g["clvs"].append(pb.record.clv_pct)
    rows = []
    for name, g in sorted(groups.items()):
        rows.append({
            field: name, "n_bets": g["n"],
            "clv_mean_pct": _r(sum(g["clvs"]) / len(g["clvs"])) if g["clvs"] else None,
            "pnl": _r(g["pnl"], 2),
            "roi_pct": _r(g["pnl"] / g["turnover"] * 100.0) if g["turnover"] > 0 else 0.0,
        })
    return rows


def _sim_summary(sim: SimResult) -> dict[str, Any]:
    return {
        "start": _r(sim.start_capital, 2),
        "end_capital": _r(sim.end_capital, 2),
        "roi_turnover_pct": _r(sim.roi_pct),
        "max_drawdown_pct": _r(sim.max_drawdown_pct, 2),
        "hit_rate_pct": _r(sim.hit_rate_pct, 2),
        "ruin": {"ruined": sim.ruined, "date": sim.ruin_date, "idx": sim.ruin_bet_index},
    }


def _anchor_report(bets: list[PinnBet], *, start_capital, flat_pct, kelly_fraction,
                   kelly_cap, haircuts) -> tuple[dict, dict]:
    """Vollstaendiger Anker-Block fuer die JSON + Plot-Rohdaten."""
    settled = [b for b in bets if b.won is not None]
    flat = simulate(settled, rule="flat", start_capital=start_capital, flat_pct=flat_pct)
    kelly = simulate(settled, rule="kelly", start_capital=start_capital,
                     kelly_fraction=kelly_fraction, kelly_cap=kelly_cap)
    sweep = []
    for h in haircuts:
        s = simulate(settled, rule="flat", start_capital=start_capital, flat_pct=flat_pct,
                     haircut_pct=h)
        sweep.append({"haircut_pct": h, "end_capital": _r(s.end_capital, 2), "roi_pct": _r(s.roi_pct)})

    block = {
        "clv": clv_stats(bets),
        "pnl_flat": _sim_summary(flat),
        "pnl_kelly": _sim_summary(kelly),
        "by_season": _grouped(flat.placed, lambda r: r.season, "season"),
        "by_bookie": _grouped(flat.placed, lambda r: r.bookie, "bookie"),
        "by_odds_bucket": _grouped(flat.placed, lambda r: _odds_bucket(r.odd), "bucket"),
        "haircut_sensitivity": sweep,
    }
    plotdata = {
        "flat_curve": [_r(c, 2) for c in flat.curve],
        "kelly_curve": [_r(c, 2) for c in kelly.curve],
        "clv": [b.clv_pct for b in bets if b.clv_pct is not None],
        "season_roi": {row["season"]: row["roi_pct"] for row in block["by_season"]},
        "odds_buckets": block["by_odds_bucket"],
        "haircut": sweep,
        "clv_mean": block["clv"]["mean_clv_pct"],
    }
    return block, plotdata


# --------------------------------------------------------------------------- #
# Gesamtlauf (C + D + E1)
# --------------------------------------------------------------------------- #
def run(
    csv_paths: Iterable[str],
    *,
    bet_source: str = "Max",
    anchor: str = "open",
    min_edge: float = 2.0,
    start_capital: float = 100.0,
    flat_pct: float = 1.0,
    kelly_fraction: float = 0.25,
    kelly_cap: float = 0.1,
    haircuts: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0),
) -> tuple[dict, dict]:
    """Fuehrt Pinnacle-Anker UND Konsens-Anker auf denselben Daten aus.

    Returns (report, plotdata). ``report`` ist die vollstaendige, allein
    interpretierbare JSON-Struktur (E1); ``plotdata`` enthaelt die Rohdaten fuer
    die Plots (E2).
    """
    paths = list(csv_paths)
    events = []
    for p in paths:
        events += load_pinnacle_events(p, bet_source=bet_source)
    if not events:
        raise ValueError("Keine Events geladen (CSV leer oder ohne Pinnacle-Spalten?).")

    pinn_bets = _build_bets(events, PinnacleAnchorModel(anchor=anchor).estimate,
                            bet_source=bet_source, min_edge=min_edge)
    cons_bets = _build_bets(events, _consensus_fair_fn(bet_source),
                            bet_source=bet_source, min_edge=min_edge)

    kw = dict(start_capital=start_capital, flat_pct=flat_pct,
              kelly_fraction=kelly_fraction, kelly_cap=kelly_cap, haircuts=haircuts)
    pinn_block, pinn_plot = _anchor_report(pinn_bets, **kw)
    cons_block, cons_plot = _anchor_report(cons_bets, **kw)

    dates = [ev.start_time for ev in events]
    report = {
        "meta": {
            "n_events": len(events), "n_bets_pinnacle": len(pinn_bets),
            "n_bets_consensus": len(cons_bets),
            "date_range": [min(dates).date().isoformat(), max(dates).date().isoformat()],
            "bet_source": bet_source, "anchor": anchor, "min_edge": min_edge,
            "leagues": sorted({ev.league for ev in events if ev.league}),
        },
        "strategies": {"pinnacle_anchor": pinn_block, "consensus_anchor": cons_block},
        "head_to_head": _head_to_head(pinn_block, cons_block),
        "verdict": _verdict(pinn_block, bet_source=bet_source),
    }
    plotdata = {"pinnacle": pinn_plot, "consensus": cons_plot}
    return report, plotdata


def _signflip(by_season: list[dict]) -> bool:
    rois = [row["roi_pct"] for row in by_season if row["roi_pct"] is not None]
    return any(r > 0 for r in rois) and any(r < 0 for r in rois)


def _head_to_head(pinn: dict, cons: dict) -> dict:
    cons_flip = _signflip(cons["by_season"])
    pinn_flip = _signflip(pinn["by_season"])
    fixed = cons_flip and not pinn_flip
    p_clv = pinn["clv"]["mean_clv_pct"]
    c_clv = cons["clv"]["mean_clv_pct"]
    summary = (
        f"Pinnacle-Anker: CLV-Mittel {p_clv}% (Anteil positiv {pinn['clv']['share_positive_pct']}%); "
        f"Konsens-Anker: CLV-Mittel {c_clv}%. "
        f"Saison-Vorzeichenwechsel: Konsens={'ja' if cons_flip else 'nein'}, "
        f"Pinnacle={'ja' if pinn_flip else 'nein'} -> "
        + ("behoben." if fixed else "NICHT (eindeutig) behoben.")
    )
    return {"sign_flip_consensus": cons_flip, "sign_flip_pinnacle": pinn_flip,
            "sign_flip_fixed": fixed, "summary_text": summary}


def _verdict(pinn: dict, *, bet_source: str = "Max") -> dict:
    clv = pinn["clv"]
    mean_clv = clv["mean_clv_pct"]
    share_pos = clv["share_positive_pct"]
    flat = pinn["pnl_flat"]
    reasons: list[str] = []
    # PRIMAER: CLV. Positiv & mehrheitlich positiv = Edge-Indikator.
    clv_ok = mean_clv is not None and mean_clv > 0 and share_pos is not None and share_pos > 50.0
    reasons.append(
        f"CLV (primaer): Mittel {mean_clv}%, Anteil positiv {share_pos}% -> "
        + ("schlaegt die Pinnacle-Schlusslinie im Schnitt." if clv_ok
           else "schlaegt die scharfe Schlusslinie NICHT konsistent (Edge fraglich).")
    )
    # SEKUNDAERE Bestaetigung: PnL/Drawdown/Ruin. Widerspricht sie dem CLV, ist das ein Warnsignal.
    pnl_ok = (flat["end_capital"] is not None and flat["end_capital"] >= flat["start"]
              and not flat["ruin"]["ruined"])
    reasons.append(
        f"PnL (sekundaer): flat {flat['start']}->{flat['end_capital']} EUR, "
        f"ROI {flat['roi_turnover_pct']}%, maxDD {flat['max_drawdown_pct']}%, "
        f"Ruin={flat['ruin']['ruined']}."
    )
    if clv_ok and not pnl_ok:
        reasons.append(
            "WIDERSPRUCH: positives CLV, aber die PnL-Bestaetigung FEHLT (Verlust/Ruin). "
            "Das untergraebt das CLV-Signal — siehe Bet-Quellen-Hinweis."
        )
    if bet_source in ("Max", "Avg"):
        reasons.append(
            f"VORSICHT Bet-Quelle '{bet_source}': das ist das Markt-MAXIMUM/Mittel ueber viele "
            "Bookies. Positives CLV gegen EINEN scharfen Schluss ist dadurch teils ein "
            "LINE-SHOPPING-Artefakt, nicht zwingend Prognose-Skill — ein sauberer CLV-Test "
            "wettet eine EINZELNE Quelle (z.B. --bet-source B365)."
        )
    # Entscheidung: CLV ist primaer, MUSS aber von der PnL bestaetigt werden. Ein
    # positives CLV ohne PnL-Bestaetigung (Ruin) wird NICHT als bestandener Edge gewertet.
    survives = bool(clv_ok and pnl_ok)
    caveats = [
        "EPL ist einer der effizientesten Maerkte — der haerteste Ort fuer Value; "
        "ein hier duenner Edge schliesst weniger liquide Ligen nicht aus.",
        "Pinnacle-Schluss ist extrem effizient; positives CLV ist eine starke, aber haltbare Behauptung.",
        "Bet-Quelle 'Max' = Markt-Maximum: positives CLV ist teils Line-Shopping, kein reiner Prognose-Skill.",
        "Kosten/Steuern/Gebuehren und Buchmacher-LIMITS sind NICHT modelliert (Gewinner werden limitiert).",
        "IN-SAMPLE: der Anker lernt nichts; CLV ist Vorlaufindikator, kein Out-of-Sample-Beweis.",
    ]
    return {"survives": survives, "primary_signal": "CLV", "clv_positive": clv_ok,
            "pnl_secondary_ok": pnl_ok, "reasons": reasons, "caveats": caveats}


def make_plots(plotdata: dict, out_dir: str | Path) -> list[str]:
    """Erzeugt die sechs Plots (E2) nach ``out_dir`` und gibt die Pfade zurueck."""
    from pathlib import Path as _P

    from arbfinder import plotting

    d = _P(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    pinn, cons = plotdata["pinnacle"], plotdata["consensus"]
    paths = [
        plotting.plot_bankroll({"flat": pinn["flat_curve"], "kelly (1/4)": pinn["kelly_curve"]},
                               out_path=str(d / "bankroll.png")),
        plotting.plot_clv_histogram(pinn["clv"], mean_clv=pinn["clv_mean"],
                                    out_path=str(d / "clv_hist.png")),
        plotting.plot_clv_compare(pinn["clv"], cons["clv"], out_path=str(d / "clv_compare.png")),
        plotting.plot_season_roi(pinn["season_roi"], cons["season_roi"],
                                 out_path=str(d / "season_roi.png")),
        plotting.plot_odds_buckets(pinn["odds_buckets"], out_path=str(d / "odds_buckets.png")),
        plotting.plot_haircut(pinn["haircut"], out_path=str(d / "haircut.png")),
    ]
    return paths


def summary_text(report: dict) -> str:
    """Nuechterne 3-4-Satz-Zusammenfassung (E4)."""
    p = report["strategies"]["pinnacle_anchor"]["clv"]
    c = report["strategies"]["consensus_anchor"]["clv"]
    h2h = report["head_to_head"]
    v = report["verdict"]
    flat = report["strategies"]["pinnacle_anchor"]["pnl_flat"]
    src = report["meta"]["bet_source"]
    src_note = ("(Markt-Maximum -> CLV teils Line-Shopping-Artefakt, kein reiner Prognose-Skill)"
                if src in ("Max", "Avg") else f"(einzelne Quelle '{src}')")
    return (
        f"CLV (primaer): Pinnacle-Anker im Schnitt {p['mean_clv_pct']}% "
        f"(positiv bei {p['share_positive_pct']}% der Wetten), Konsens-Anker {c['mean_clv_pct']}% "
        f"{src_note}. "
        f"Sekundaer (PnL): flat {flat['start']}->{flat['end_capital']} EUR (Ruin={flat['ruin']['ruined']}), "
        f"Saison-Vorzeichenwechsel behoben={h2h['sign_flip_fixed']}. "
        f"Verdict: {'Edge bestaetigt (CLV positiv UND PnL traegt)' if v['survives'] else 'Edge NICHT bestaetigt — positives CLV ohne PnL-Bestaetigung'} "
        f"— in-sample, EPL hocheffizient, Kosten/Limits nicht modelliert."
    )
