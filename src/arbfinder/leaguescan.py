"""
Liga-Scan: Pinnacle-Anker + DEVIGTES Closing Line Value ueber WENIGER LIQUIDE Ligen.

Motivation: Die EPL (E0) ist einer der effizientesten Maerkte — der haerteste
Ort fuer Value. Diese Analyse wendet dieselbe Pinnacle-Anker-Methode auf eine
Reihe weniger liquider Ligen an und sucht eine Liga mit einem SAUBER POSITIVEN
CLV gegen die scharfe Schlusslinie.

Drei Korrekturen gegenueber dem ersten Pinnacle-Lauf (bewusst strenger/ehrlicher):

1. Bet-Quelle = EINE realistisch erreichbare Quelle (Default ``B365``), NICHT das
   Markt-Maximum ``Max`` — sonst misst man Line-Shopping, nicht Prognose-Skill.
2. Die Pinnacle-SCHLUSSlinie wird DEVIGT, bevor das CLV gemessen wird. Benchmark
   ist die FAIRE (no-vig) Schlussquote, nicht die rohe Quote mit Marge:

       fair_close_prob = devig(PSC)              (Summe ueber Ausgaenge = 1)
       CLV pro Wette   = bet_quote * fair_close_prob - 1            (in %)

   Das ist HAERTER als das rohe CLV: die rohe Schlussquote ist durch die Vig
   verkuerzt, was den Wetter schmeichelt; gegen die faire Linie zu schlagen ist
   das eigentliche Signal.
3. Fokus auf MODERATE Quoten (Default 2.0-4.0) — die Aussenseiter-Falle (Longshot-
   Bias, hohe Varianz) wird vermieden.

Selektion bleibt am EROEFFNUNGS-Anker (``PinnacleAnchorModel(anchor="open")``):
``edge = bet_quote * fair_open_prob - 1 >= min_edge``. Gegen den Schluss zu ankern
UND CLV am selben Schluss zu messen waere zirkulaer (CLV per Konstruktion ~0).

EHRLICH (gilt durchgehend):
- Positives CLV gegen die DEVIGTE Pinnacle-Schluss ist das echte Signal. PnL
  (Bankroll/Drawdown/Ruin) ist SEKUNDAER (mehr Varianz, braucht Ergebnisse).
- Weniger liquide Ligen haben oft eine duennere/spaetere Pinnacle-Abdeckung; der
  Anker kann dort schwaecher sein. Das ist eine Schwaeche, kein Vorteil.
- Kosten/Steuern/Gebuehren und Buchmacher-LIMITS sind NICHT modelliert.
- IN-SAMPLE: der Anker lernt nichts; CLV ist Vorlaufindikator, kein OOS-Beweis.
- Zeigt KEINE Liga ein robustes positives CLV, wird das klar gesagt — nichts
  wird schoengerechnet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

from arbfinder.diagnostics import _season, simulate
from arbfinder.fair_probability import PinnacleAnchorModel
# Wiederverwendung der GETEILTEN Bausteine aus pinnacle.py (eine Quelle der
# Wahrheit fuer Rundung, Sim-Zusammenfassung, ruin-unabhaengige Gruppierung und
# die CLV-Statistik — so erbt der Liga-Scan dieselben Ehrlichkeits-Eigenschaften).
from arbfinder.pinnacle import PinnBet, _grouped, _r, _sim_summary, clv_stats
from arbfinder.providers.footballdata import load_pinnacle_events

logger = logging.getLogger("arbfinder.leaguescan")

# Voreingestellte Robustheits-Kriterien (alle ueber die CLI ueberschreibbar).
DEFAULT_ROBUST = {
    "min_mean_clv_pct": 0.5,        # "deutlich >0": kleiner Mindest-Mittelwert
    "min_share_positive_pct": 55.0,
    "min_bets": 50,                 # Mindest-Stichprobe (gegen Klein-N-Rauschen)
    "min_positive_buckets": 2,      # CLV muss in >=2 Quoten-Buckets positiv sein
    "min_bucket_n": 10,             # ein Bucket zaehlt erst ab so vielen Wetten
}


# --------------------------------------------------------------------------- #
# Loader: alle CSVs eines Ordners, je Liga getrennt
# --------------------------------------------------------------------------- #
def load_events_by_league(
    csv_dir: str | Path, *, bet_source: str = "B365",
) -> tuple[dict[str, list], list[dict[str, str]], list[str]]:
    """Laedt alle ``*.csv`` aus ``csv_dir`` und gruppiert die Events je Liga (Div).

    Kein Scraping — nur die offiziell angebotenen football-data CSV-Dateien.
    Dateien ohne Pinnacle-Eroeffnung (PS*) oder ohne die Bet-Quelle werden NICHT
    still verschluckt, sondern mit Grund in der Skip-Liste vermerkt (so bleibt die
    Datenqualitaet sichtbar — vgl. ``skipped_incomplete`` in CLAUDE.md).

    Returns: ``(by_league, skipped, loaded_files)`` mit ``by_league[Liga] =
    [Event, ...]``, ``skipped = [{"file": Name, "reason": ...}, ...]`` und
    ``loaded_files`` = Namen der erfolgreich geladenen CSVs.
    """
    d = Path(csv_dir)
    if not d.is_dir():
        raise NotADirectoryError(f"--csv-dir ist kein Ordner: {csv_dir}")

    by_league: dict[str, list] = {}
    skipped: list[dict[str, str]] = []
    loaded_files: list[str] = []
    # Endung case-insensitiv (glob("*.csv") ist auf Linux case-sensitive und wuerde
    # eine 'X.CSV' SPURLOS verschlucken — gegen den "nicht still verschlucken"-Vertrag).
    csv_paths = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    for path in csv_paths:
        try:
            events = load_pinnacle_events(path, bet_source=bet_source)
        except (ValueError, OSError) as exc:        # keine PS*/Bet-Quelle, leer, ...
            skipped.append({"file": path.name, "reason": str(exc)})
            logger.warning("CSV uebersprungen %s: %s", path.name, exc)
            continue
        if not events:
            skipped.append({"file": path.name, "reason": "keine verwertbaren Zeilen"})
            continue
        loaded_files.append(path.name)
        for ev in events:
            league = ev.league or "?"
            by_league.setdefault(league, []).append(ev)
    return by_league, skipped, loaded_files


# --------------------------------------------------------------------------- #
# Quoten-Buckets innerhalb des moderaten Bereichs (Kontrolle der Verteilung)
# --------------------------------------------------------------------------- #
def _moderate_bucket(odd: float) -> str:
    """Feine Buckets im moderaten Bereich (Default-Filter 2.0-4.0)."""
    if odd < 2.5:
        return "<2.5"
    if odd < 3.0:
        return "2.5-3.0"
    if odd < 3.5:
        return "3.0-3.5"
    return "3.5+"


# --------------------------------------------------------------------------- #
# Wetten bauen: Selektion am OFFEN-Anker, CLV am DEVIGTEN Schluss
# --------------------------------------------------------------------------- #
def _build_league_bets(
    events: list, *, bet_source: str, min_edge: float, odds_min: float, odds_max: float,
) -> tuple[list[PinnBet], dict[str, int]]:
    """Selektiert Value-Wetten einer Liga und misst CLV gegen die devigte Schluss.

    - Selektion: ``edge = bet * fair_open_prob - 1 >= min_edge`` (fair_open = devig(PS)).
    - Quotenfilter: nur ``odds_min <= bet <= odds_max`` (moderate Quoten).
    - CLV (falls Schluss vorhanden): ``bet * fair_close_prob - 1`` mit
      ``fair_close_prob = devig(PSC)`` — die FAIRE, nicht die rohe Schlussquote.
    - ``PinnBet.pinnacle_close`` haelt die faire (devigte) Schluss-Benchmark-Quote.

    Returns ``(bets, diag)``. ``diag`` macht den Quotenfilter SICHTBAR (kein
    stilles Wegschneiden): wie viele Ausgaenge zwar den Edge bestehen, aber als
    Favorit (<odds_min) oder als AUSSENSEITER (>odds_max, die Longshot-Falle)
    bewusst verworfen werden.
    """
    open_model = PinnacleAnchorModel(anchor="open")     # devig(PS)
    close_model = PinnacleAnchorModel(anchor="close")   # devig(PSC)
    bets: list[PinnBet] = []
    diag = {"n_edge_pass": 0, "n_edge_in_range": 0,
            "n_edge_below_range": 0, "n_edge_above_range": 0}
    for ev in events:
        odds = ev.markets[0].odds if ev.markets else {}
        fair_open = open_model.estimate(odds)
        if not fair_open:
            continue
        fair_close = close_model.estimate(odds)         # None, wenn PSC unvollstaendig
        season = _season(ev.start_time)
        for outcome, books in odds.items():
            if not books:
                continue
            bet = books.get(bet_source)
            p_open = fair_open.get(outcome)
            if bet is None or float(bet) <= 0 or p_open is None:
                continue
            bet = float(bet)
            edge_pct = (bet * p_open - 1.0) * 100.0
            if edge_pct != edge_pct or edge_pct < min_edge:   # NaN-sicher + Schwelle
                continue
            diag["n_edge_pass"] += 1
            if bet < odds_min:                          # Favorit -> verworfen (Quotenfilter)
                diag["n_edge_below_range"] += 1
                continue
            if bet > odds_max:                          # Aussenseiter -> Longshot-Falle, verworfen
                diag["n_edge_above_range"] += 1
                continue
            diag["n_edge_in_range"] += 1
            clv = None
            fair_close_odds = None
            if fair_close is not None:
                p_close = fair_close.get(outcome)
                if p_close and p_close > 0:
                    clv = (bet * p_close - 1.0) * 100.0       # CLV vs DEVIGTE Schluss
                    fair_close_odds = 1.0 / p_close
            won = None if ev.result is None else (ev.result == outcome)
            b = PinnBet(
                commence_time=ev.start_time, season=season, event_name=ev.name,
                outcome=outcome, bookie=bet_source, odd=bet, fair_prob=float(p_open),
                won=won if won is not None else False,
                clv_pct=clv, pinnacle_close=fair_close_odds,
            )
            if won is None:
                b.won = None  # type: ignore[assignment]      # nicht settled markieren
            bets.append(b)
    bets.sort(key=lambda b: b.commence_time)
    return bets, diag


# --------------------------------------------------------------------------- #
# Pro-Liga-Block
# --------------------------------------------------------------------------- #
def _league_block(
    league: str, events: list, *, bet_source: str, min_edge: float,
    odds_min: float, odds_max: float, start_capital: float, flat_pct: float,
    kelly_fraction: float, kelly_cap: float,
) -> tuple[dict, dict]:
    bets, odds_diag = _build_league_bets(events, bet_source=bet_source, min_edge=min_edge,
                                         odds_min=odds_min, odds_max=odds_max)
    settled = [b for b in bets if b.won is not None]
    flat = simulate(settled, rule="flat", start_capital=start_capital, flat_pct=flat_pct)
    kelly = simulate(settled, rule="kelly", start_capital=start_capital,
                     kelly_fraction=kelly_fraction, kelly_cap=kelly_cap)
    clv = clv_stats(bets)
    block = {
        "league": league,
        "n_events": len(events),
        "n_bets": len(bets),
        "n_settled": len(settled),
        "n_with_clv": clv["n"],
        "clv": clv,
        "pnl_flat": _sim_summary(flat),
        "pnl_kelly": _sim_summary(kelly),
        # by_odds_bucket ist ruin-unabhaengig (flat-1-Einheit) ueber settled Wetten;
        # clv_mean_pct je Bucket ist die KONTROLLE, dass der Edge nicht aus einem
        # einzigen Bucket kommt.
        "by_odds_bucket": _grouped(settled, lambda b: _moderate_bucket(b.odd), "bucket"),
        # Sichtbarmachung des Quotenfilters (keine stille Beschneidung): wie viele
        # Edge-Treffer als Favorit bzw. AUSSENSEITER (Longshot-Falle) verworfen wurden.
        "odds_filter_diag": odds_diag,
    }
    plotdata = {
        "league": league,
        "clv": [b.clv_pct for b in bets if b.clv_pct is not None],
        "clv_mean": clv["mean_clv_pct"],
        "n_with_clv": clv["n"],
        "flat_curve": [_r(c, 2) for c in flat.curve],
        "kelly_curve": [_r(c, 2) for c in kelly.curve],
    }
    return block, plotdata


# --------------------------------------------------------------------------- #
# Robustheit + Ranking + Verdict
# --------------------------------------------------------------------------- #
def _is_robust(block: dict, c: dict) -> tuple[bool, list[str]]:
    """Robust = mean_clv deutlich >0 UND share_positive >Schwelle UND genug
    Stichprobe UND CLV in MEHREREN Quoten-Buckets positiv (nicht nur einem)."""
    clv = block["clv"]
    mean, share, n = clv["mean_clv_pct"], clv["share_positive_pct"], clv["n"]
    ok = True
    reasons: list[str] = []

    if mean is None or mean < c["min_mean_clv_pct"]:
        ok = False
        reasons.append(f"mean_clv {mean}% < {c['min_mean_clv_pct']}% (nicht deutlich >0)")
    else:
        reasons.append(f"mean_clv {mean}% >= {c['min_mean_clv_pct']}%")

    if share is None or share <= c["min_share_positive_pct"]:
        ok = False
        reasons.append(f"share_positive {share}% <= {c['min_share_positive_pct']}%")
    else:
        reasons.append(f"share_positive {share}% > {c['min_share_positive_pct']}%")

    if n < c["min_bets"]:
        ok = False
        reasons.append(f"n_with_clv {n} < {c['min_bets']} (Stichprobe zu klein)")
    else:
        reasons.append(f"n_with_clv {n} >= {c['min_bets']}")

    populated = [b for b in block["by_odds_bucket"] if b["n_bets"] >= c["min_bucket_n"]]
    positive = [b for b in populated if b["clv_mean_pct"] is not None and b["clv_mean_pct"] > 0]
    if len(populated) < 2 or len(positive) < c["min_positive_buckets"]:
        ok = False
        reasons.append(
            f"CLV positiv in {len(positive)} von {len(populated)} besetzten Buckets "
            f"(<{c['min_positive_buckets']} -> evtl. nur ein Bucket)"
        )
    else:
        reasons.append(f"CLV positiv in {len(positive)} von {len(populated)} besetzten Buckets")

    return ok, reasons


def _mean_clv(block: dict) -> float:
    """Sortier-Schluessel: mean_clv (None ganz nach unten)."""
    m = block["clv"]["mean_clv_pct"]
    return m if m is not None else float("-inf")


def _ranking(leagues: dict[str, dict]) -> list[dict]:
    """Ligen nach mean_clv_pct absteigend; n_bets bleibt sichtbar (Klein-N erkennbar)."""
    rows = []
    for lg, b in leagues.items():
        rows.append({
            "league": lg,
            "mean_clv_pct": b["clv"]["mean_clv_pct"],
            "share_positive_clv_pct": b["clv"]["share_positive_pct"],
            "n_bets": b["n_bets"],
            "n_with_clv": b["clv"]["n"],
            "robust": b["robust"],
        })
    rows.sort(key=lambda r: (r["mean_clv_pct"] is not None, r["mean_clv_pct"] or 0.0),
              reverse=True)
    return rows


def _pick_best(leagues: dict[str, dict], criteria: dict) -> tuple[str | None, str]:
    """Beste Liga + Begruendungs-Basis. Robust schlaegt alles; sonst groesstes
    mean_clv MIT Mindeststichprobe; sonst groesstes mean_clv mit Klein-N-Warnung."""
    robust = [lg for lg, b in leagues.items() if b["robust"]]
    if robust:
        return max(robust, key=lambda lg: _mean_clv(leagues[lg])), "robust"
    sized = [lg for lg, b in leagues.items() if b["clv"]["n"] >= criteria["min_bets"]]
    if sized:
        return max(sized, key=lambda lg: _mean_clv(leagues[lg])), "bestes_mean_clv_NICHT_robust"
    any_clv = [lg for lg, b in leagues.items() if b["clv"]["mean_clv_pct"] is not None]
    if any_clv:
        return max(any_clv, key=lambda lg: _mean_clv(leagues[lg])), "bestes_mean_clv_KLEINE_stichprobe"
    return None, "keine_liga_mit_clv"


_CAVEATS = [
    "Primaersignal ist CLV gegen die DEVIGTE Pinnacle-Schluss (faire no-vig Linie), "
    "nicht die rohe Schlussquote — das ist die haertere, ehrlichere Messlatte.",
    "PnL (Bankroll/Drawdown/Ruin) ist SEKUNDAER und deutlich verrauschter als CLV.",
    "Weniger liquide Ligen haben oft duennere/spaetere Pinnacle-Abdeckung; der Anker "
    "kann dort schwaecher sein (eine Schwaeche der Methode, kein Vorteil).",
    "Bet-Quelle ist eine EINZELNE Quelle (kein Line-Shopping ueber mehrere Bookies).",
    "Kosten/Steuern/Gebuehren und Buchmacher-LIMITS sind NICHT modelliert.",
    "IN-SAMPLE: der Anker lernt nichts; CLV ist Vorlaufindikator, kein OOS-Beweis.",
]


def _verdict(leagues: dict[str, dict], criteria: dict) -> dict:
    robust = sorted((lg for lg, b in leagues.items() if b["robust"]),
                    key=lambda lg: _mean_clv(leagues[lg]), reverse=True)
    best, basis = _pick_best(leagues, criteria)
    if robust:
        assert best is not None      # robust nicht leer -> _pick_best liefert eine Liga
        summary = (
            f"{len(robust)} Liga(en) mit ROBUSTEM positivem CLV gegen die devigte "
            f"Pinnacle-Schluss: {', '.join(robust)}. Beste: {best} "
            f"(mean_clv {leagues[best]['clv']['mean_clv_pct']}%, "
            f"positiv bei {leagues[best]['clv']['share_positive_pct']}% der Wetten)."
        )
    elif best is not None:
        summary = (
            "KEINE Liga zeigt ein ROBUSTES positives CLV gegen die devigte Pinnacle-"
            f"Schluss. Bestes (NICHT robustes) mean_clv: {best} "
            f"({leagues[best]['clv']['mean_clv_pct']}%, n_with_clv "
            f"{leagues[best]['clv']['n']}) — nicht ueberinterpretieren."
        )
    else:
        summary = ("Keine Liga lieferte auswertbare CLV-Wetten (zu duenne Pinnacle-"
                   "Abdeckung oder kein Treffer im Quotenfilter).")
    return {
        "primary_signal": "CLV vs devigte Pinnacle-Schluss",
        "any_robust": bool(robust),
        "robust_leagues": robust,
        "best_league": best,
        "best_basis": basis,
        "robust_criteria": criteria,
        "summary": summary,
        "caveats": _CAVEATS,
    }


# --------------------------------------------------------------------------- #
# Gesamt-Scan
# --------------------------------------------------------------------------- #
def scan(
    csv_dir: str | Path, *,
    bet_source: str = "B365",
    min_edge: float = 2.0,
    odds_min: float = 2.0,
    odds_max: float = 4.0,
    start_capital: float = 100.0,
    flat_pct: float = 1.0,
    kelly_fraction: float = 0.25,
    kelly_cap: float = 0.1,
    robust_criteria: dict | None = None,
) -> tuple[dict, dict]:
    """Scant alle Ligen-CSVs in ``csv_dir``. Returns ``(report, plotdata)``."""
    criteria = dict(DEFAULT_ROBUST)
    if robust_criteria:
        criteria.update(robust_criteria)

    by_league, skipped, loaded_files = load_events_by_league(csv_dir, bet_source=bet_source)

    leagues: dict[str, dict] = {}
    plot_by_league: dict[str, dict] = {}
    all_dates: list = []
    for lg in sorted(by_league):
        events = by_league[lg]
        all_dates += [ev.start_time for ev in events]
        block, plotdata = _league_block(
            lg, events, bet_source=bet_source, min_edge=min_edge,
            odds_min=odds_min, odds_max=odds_max, start_capital=start_capital,
            flat_pct=flat_pct, kelly_fraction=kelly_fraction, kelly_cap=kelly_cap)
        robust, robust_reasons = _is_robust(block, criteria)
        block["robust"] = robust
        block["robust_reasons"] = robust_reasons
        leagues[lg] = block
        plot_by_league[lg] = plotdata

    ranking = _ranking(leagues)
    verdict = _verdict(leagues, criteria)
    report = {
        "meta": {
            "csv_dir": str(csv_dir),
            "n_files_loaded": len(loaded_files),
            "loaded_files": loaded_files,
            "n_leagues": len(by_league),
            "n_files_skipped": len(skipped),
            "skipped_files": skipped,
            "bet_source": bet_source,
            "anchor": "open",
            "clv_benchmark": "pinnacle_close_devigged",
            "odds_filter": [odds_min, odds_max],
            "min_edge": min_edge,
            "robust_criteria": criteria,
            "leagues_scanned": sorted(by_league),
            "date_range": ([min(all_dates).date().isoformat(),
                            max(all_dates).date().isoformat()] if all_dates else None),
        },
        "leagues": leagues,
        "ranking": ranking,
        "verdict": verdict,
    }
    plotdata = {"by_league": plot_by_league, "ranking": ranking}
    return report, plotdata


# --------------------------------------------------------------------------- #
# best_league.json
# --------------------------------------------------------------------------- #
def best_league_report(report: dict) -> dict:
    """Extrahiert die beste Liga als allein interpretierbaren Block (zum Upload)."""
    v = report["verdict"]
    best = v["best_league"]
    meta = {
        "bet_source": report["meta"]["bet_source"],
        "clv_benchmark": report["meta"]["clv_benchmark"],
        "odds_filter": report["meta"]["odds_filter"],
        "min_edge": report["meta"]["min_edge"],
        "robust_criteria": report["meta"]["robust_criteria"],
        "selection_basis": v["best_basis"],
    }
    if best is None:
        return {"meta": meta, "league": None,
                "note": "Keine Liga mit auswertbarem CLV gefunden.",
                "caveats": v["caveats"]}
    block = dict(report["leagues"][best])
    block["meta"] = meta
    block["caveats"] = v["caveats"]
    if not block.get("robust"):
        block["note"] = ("ACHTUNG: Diese Liga erfuellt die Robustheits-Kriterien NICHT "
                         "(siehe robust_reasons) — beste verfuegbare, aber kein belastbarer Edge.")
    return block


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #
def make_plots(report: dict, plotdata: dict, out_dir: str | Path, *, top_n: int = 3) -> list[str]:
    """CLV-Mittel je Liga (sortiert) + fuer die Top-N Ligen je CLV-Histogramm und
    Bankroll-Kurve. Returns die erzeugten Pfade."""
    from arbfinder import plotting

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = [plotting.plot_league_clv(report["ranking"], out_path=str(d / "league_clv.png"))]

    # Top-N nach Ranking (mean_clv), nur Ligen mit auswertbarem CLV.
    top = [r["league"] for r in report["ranking"] if r["n_with_clv"] and r["n_with_clv"] > 0][:top_n]
    for lg in top:
        pl = plotdata["by_league"][lg]
        n = pl["n_with_clv"]
        safe = lg.replace("/", "_")
        paths.append(plotting.plot_clv_histogram(
            pl["clv"], mean_clv=pl["clv_mean"],
            title=f"CLV {lg} (n={n}, vs devigte Pinnacle-Schluss)",
            out_path=str(d / f"clv_hist_{safe}.png")))
        paths.append(plotting.plot_bankroll(
            {"flat": pl["flat_curve"], "kelly (1/4)": pl["kelly_curve"]},
            out_path=str(d / f"bankroll_{safe}.png")))
    return paths


def summary_text(report: dict) -> str:
    """Nuechterne 3-5-Satz-Zusammenfassung (ehrlich, keine Schoenfaerberei)."""
    v = report["verdict"]
    m = report["meta"]
    head = (f"Liga-Scan ({m['n_leagues']} Ligen, Bet-Quelle {m['bet_source']}, "
            f"Quoten {m['odds_filter'][0]}-{m['odds_filter'][1]}, "
            f"CLV gegen DEVIGTE Pinnacle-Schluss).")
    top3 = ", ".join(
        f"{r['league']} {r['mean_clv_pct']}% (n={r['n_with_clv']}{', robust' if r['robust'] else ''})"
        for r in report["ranking"][:3]) or "—"
    return (f"{head} {v['summary']} Ranking-Spitze (mean_clv): {top3}. "
            "PnL ist sekundaer; Kosten/Limits nicht modelliert; in-sample.")
