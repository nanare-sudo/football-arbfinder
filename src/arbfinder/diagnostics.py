"""
Diagnose des bestehenden Value-Backtests — ist die +PnL Signal oder Rauschen?

Dies ist KEINE neue Strategie und KEIN lernendes Modell. Es wertet den
vorhandenen Lauf (z.B. football-data, 3 Saisons) ehrlich aus, BEVOR man ueber
ein lernendes Modell nachdenkt. Drei harte Realitaets-Checks plus realistisches
Bankroll-Management mit 100 EUR Startkapital.

WARUM BANKROLL ALLES AENDERT:
Der bisherige "+2061" PnL setzte implizit unbegrenztes Kapital voraus (flat
100 EUR je Wette, egal wie der Kontostand steht). Mit 100 EUR Startkapital ist
das unecht: man kann nicht 678x100 EUR umsetzen. Hier ist das Konto die bindende
Grenze — nie mehr setzen als vorhanden; erreicht das Kapital 0, ist Schluss
(RUIN). Berichtet wird daher Endkapital aus 100 EUR, ROI auf den Umsatz,
maximaler Drawdown und Ruin — nie nur absolute PnL.

EHRLICHE EINORDNUNG (gilt durchgehend):
- IN-SAMPLE: Konsens-Devig lernt nichts und rechnet den Konsens aus demselben
  Snapshot, den es bewertet. Es gibt hier kein echtes Out-of-Sample; ein
  Walk-Forward-Holdout hat nichts zurueckzuhalten. "confirmed" aus dem letzten
  Lauf heisst faktisch nur "in-sample positiv".
- Kosten (Steuern/Gebuehren) und Buchmacher-LIMITS sind NICHT modelliert; gerade
  die grosszuegigen Ausreisser-Bookies limitieren Gewinner zuerst.
- Schlussquoten sind die SCHAERFSTEN Quoten. Den Schluss-Konsens zu schlagen ist
  eine starke Behauptung — eher Verdacht auf Rauschen als auf echten Edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
import logging

from arbfinder import backtest
from arbfinder.providers.base import parse_datetime
from arbfinder.strategies import get

logger = logging.getLogger("arbfinder.diagnostics")

_ODDS_BUCKETS = ("<2.0", "2.0-3.5", "3.5-6.0", ">6.0")


def _season(dt: datetime) -> str:
    """Fussball-Saison-Label aus einem Datum (Aug-Mai), z.B. '2023/24'."""
    y = dt.year
    return f"{y}/{str(y + 1)[2:]}" if dt.month >= 7 else f"{y - 1}/{str(y)[2:]}"


def _odds_bucket(odd: float) -> str:
    if odd < 2.0:
        return "<2.0"
    if odd < 3.5:
        return "2.0-3.5"
    if odd < 6.0:
        return "3.5-6.0"
    return ">6.0"


@dataclass
class BetRecord:
    """Eine genommene Value-Wette (aus dem bestehenden Lauf rekonstruiert)."""

    commence_time: datetime
    season: str
    event_name: str
    outcome: str
    bookie: str
    odd: float
    fair_prob: float
    won: bool


@dataclass
class PlacedBet:
    record: BetRecord
    stake: float
    pnl: float            # realisierter Gewinn/Verlust (nach Preis-Abschlag)


@dataclass
class SimResult:
    """Ergebnis einer Bankroll-Simulation fuer EINE Einsatzregel + Abschlag."""

    rule: str
    haircut_pct: float
    start_capital: float
    end_capital: float
    turnover: float
    roi_pct: float                  # Gesamt-PnL / Umsatz
    max_drawdown_pct: float
    n_bets: int
    n_wins: int
    hit_rate_pct: float
    ruined: bool
    ruin_bet_index: int | None
    ruin_date: str | None
    curve: list[float] = field(default_factory=list)
    placed: list[PlacedBet] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "haircut_pct": self.haircut_pct,
            "start_capital": round(self.start_capital, 2),
            "end_capital": round(self.end_capital, 2),
            "turnover": round(self.turnover, 2),
            "roi_pct": round(self.roi_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "n_bets": self.n_bets,
            "n_wins": self.n_wins,
            "hit_rate_pct": round(self.hit_rate_pct, 2),
            "ruined": self.ruined,
            "ruin_bet_index": self.ruin_bet_index,
            "ruin_date": self.ruin_date,
        }


# --------------------------------------------------------------------------- #
# Schritt 1: Bankroll-Management (Konto als bindende Grenze)
# --------------------------------------------------------------------------- #
def simulate(
    bets: list[BetRecord],
    *,
    rule: str,
    start_capital: float = 100.0,
    haircut_pct: float = 0.0,
    flat_pct: float = 1.0,
    kelly_fraction: float = 0.25,
    kelly_cap: float = 0.1,
) -> SimResult:
    """Simuliert die Wetten chronologisch mit dem Konto als bindender Grenze.

    rule="flat": fester Einsatz = ``flat_pct`` % des STARTkapitals, konstant.
    rule="kelly": gekappte fraktionale Kelly-Groesse als Anteil des AKTUELLEN
        Kontostands (compoundet). f = clip(kelly_fraction * edge/(odd-1), 0, kelly_cap).

    ``haircut_pct`` zieht von jeder genommenen Quote ab (Ausfuehrbarkeit): man
    setzt auf den geglaubten Edge, bekommt aber nur die abgeschlagene Quote.
    Erreicht das Kapital 0 -> RUIN, der Lauf stoppt (Index/Datum festgehalten).
    """
    if rule not in ("flat", "kelly"):
        raise ValueError(f"unbekannte Regel '{rule}' (flat|kelly)")

    cap = start_capital
    flat_stake = start_capital * flat_pct / 100.0
    hc = haircut_pct / 100.0
    turnover = 0.0
    peak = start_capital
    max_dd = 0.0
    n_wins = 0
    placed: list[PlacedBet] = []
    curve: list[float] = []
    ruined = False
    ruin_idx: int | None = None
    ruin_date: str | None = None

    for i, b in enumerate(bets):
        if cap <= 1e-9:                              # Konto leer -> RUIN, Stopp
            ruined = True
            ruin_idx = i
            ruin_date = b.commence_time.isoformat()
            break

        if rule == "flat":
            desired = flat_stake
        else:
            edge = b.fair_prob * b.odd - 1.0        # erwarteter Vorteil je Einsatz
            denom = b.odd - 1.0
            f = (kelly_fraction * edge / denom) if denom > 0 else 0.0
            f = max(0.0, min(kelly_cap, f))
            desired = cap * f

        stake = min(desired, cap)                   # NIE mehr setzen als vorhanden
        if stake <= 1e-12:
            curve.append(cap)
            continue

        eff_odd = b.odd * (1.0 - hc)                # Preis-Abschlag auf die Auszahlung
        if b.won:
            pnl = stake * (eff_odd - 1.0)
            n_wins += 1
        else:
            pnl = -stake
        cap += pnl
        turnover += stake
        placed.append(PlacedBet(b, stake, pnl))

        peak = max(peak, cap)
        if peak > 0:
            max_dd = max(max_dd, (peak - cap) / peak * 100.0)
        curve.append(cap)

    total_pnl = cap - start_capital
    roi = (total_pnl / turnover * 100.0) if turnover > 0 else 0.0
    n_bets = len(placed)
    hit = (n_wins / n_bets * 100.0) if n_bets else 0.0
    return SimResult(rule, haircut_pct, start_capital, cap, turnover, roi, max_dd,
                     n_bets, n_wins, hit, ruined, ruin_idx, ruin_date, curve, placed)


# --------------------------------------------------------------------------- #
# Wetten aus dem bestehenden Lauf rekonstruieren
# --------------------------------------------------------------------------- #
def collect_bets(
    snapshots_path: str,
    *,
    strategy_name: str = "value",
    **strategy_kwargs: Any,
) -> list[BetRecord]:
    """Rekonstruiert die genommenen Wetten (chronologisch) aus den Snapshots.

    Laeuft die bestehende Strategie ueber die Daten; je Signal werden Ausgang,
    bester Bookie+Quote und faire Wahrscheinlichkeit aus ``meta`` gelesen.
    Nur SETTLED Signale (mit 'result') zaehlen — nur die haben ein Win/Loss.
    """
    rows = backtest.load_snapshots(snapshots_path)
    strat = get(strategy_name)
    for k, v in strategy_kwargs.items():
        setattr(strat, k, v)

    bets: list[BetRecord] = []
    unsettled = 0
    for ev in rows:
        result = ev.get("result")
        for sig in strat.evaluate(ev):
            outcome = sig.meta.get("outcome")
            best = sig.meta.get("best") or [None, None]
            bookie, odd = (best + [None, None])[:2]
            fair = sig.meta.get("fair_prob")
            if outcome is None or odd is None or fair is None:
                continue
            if not result:
                unsettled += 1
                continue
            ct = parse_datetime(ev.get("commence_time") or ev.get("start_time") or ev.get("ts"))
            bets.append(BetRecord(
                commence_time=ct, season=_season(ct),
                event_name=str(ev.get("event_name", "")), outcome=str(outcome),
                bookie=str(bookie), odd=float(odd), fair_prob=float(fair),
                won=(result == outcome),
            ))
    bets.sort(key=lambda b: b.commence_time)
    if unsettled:
        logger.info("%d unsettled Signale (ohne 'result') ausgelassen.", unsettled)
    return bets


# --------------------------------------------------------------------------- #
# Schritt 3 (Checks): Konzentration & Quoten-Buckets
# --------------------------------------------------------------------------- #
def concentration(placed: list[PlacedBet], key: Callable[[BetRecord], str]) -> dict[str, dict]:
    """PnL/ROI/Anzahl je Gruppe (Bookie, Saison oder Quoten-Bucket)."""
    groups: dict[str, dict] = {}
    for pb in placed:
        g = groups.setdefault(key(pb.record), {"pnl": 0.0, "turnover": 0.0, "n": 0, "wins": 0})
        g["pnl"] += pb.pnl
        g["turnover"] += pb.stake
        g["n"] += 1
        g["wins"] += int(pb.record.won)
    total = sum(g["pnl"] for g in groups.values())
    out: dict[str, dict] = {}
    for name, g in groups.items():
        out[name] = {
            "pnl": round(g["pnl"], 2),
            "roi_pct": round(g["pnl"] / g["turnover"] * 100.0, 3) if g["turnover"] > 0 else 0.0,
            "n": g["n"],
            "hit_rate_pct": round(g["wins"] / g["n"] * 100.0, 2) if g["n"] else 0.0,
            "pnl_share_pct": round(g["pnl"] / total * 100.0, 1) if total > 0 else None,
        }
    return out


def _max_share(groups: dict[str, dict]) -> tuple[str | None, float | None]:
    """Groesster PnL-Anteil einer einzelnen Gruppe (zur Konzentrations-Warnung)."""
    best_name, best_share = None, None
    for name, g in groups.items():
        s = g.get("pnl_share_pct")
        if s is not None and (best_share is None or s > best_share):
            best_name, best_share = name, s
    return best_name, best_share


# --------------------------------------------------------------------------- #
# Gesamt-Diagnose
# --------------------------------------------------------------------------- #
def diagnose(
    snapshots_path: str,
    *,
    strategy_name: str = "value",
    start_capital: float = 100.0,
    flat_pct: float = 1.0,
    kelly_fraction: float = 0.25,
    kelly_cap: float = 0.1,
    haircuts: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0),
) -> dict[str, Any]:
    """Fuehrt Bankroll-Sim + drei Stress-Checks aus und liefert einen Report-Dict."""
    bets = collect_bets(snapshots_path, strategy_name=strategy_name)
    rules = ("flat", "kelly")

    def _sim(rule: str, hc: float) -> SimResult:
        return simulate(bets, rule=rule, start_capital=start_capital, haircut_pct=hc,
                        flat_pct=flat_pct, kelly_fraction=kelly_fraction, kelly_cap=kelly_cap)

    base = {rule: _sim(rule, 0.0) for rule in rules}

    report: dict[str, Any] = {
        "strategy": strategy_name,
        "n_bets": len(bets),
        "start_capital": start_capital,
        "rules": {rule: base[rule].summary() for rule in rules},
        "bankroll_curve": {rule: [round(c, 2) for c in base[rule].curve] for rule in rules},
        "haircut_sweep": {},
        "concentration_by_bookie": {},
        "concentration_by_season": {},
        "concentration_by_odds_bucket": {},
    }

    for rule in rules:
        sweep = []
        for hc in haircuts:
            s = _sim(rule, hc)
            sweep.append({"haircut_pct": hc, "end_capital": round(s.end_capital, 2),
                          "roi_pct": round(s.roi_pct, 3), "ruined": s.ruined})
        report["haircut_sweep"][rule] = sweep
        report["concentration_by_bookie"][rule] = concentration(base[rule].placed, lambda r: r.bookie)
        report["concentration_by_season"][rule] = concentration(base[rule].placed, lambda r: r.season)
        report["concentration_by_odds_bucket"][rule] = concentration(base[rule].placed, lambda r: _odds_bucket(r.odd))

    report["assessment"] = _assess(report)
    return report


def _assess(report: dict[str, Any]) -> dict[str, Any]:
    """Nuechterne Einschaetzung: ueberlebt der Edge die Checks?"""
    start = report["start_capital"]
    reasons: list[str] = []

    # 1) Preis-Abschlag: kleinster getesteter Abschlag, der flat ins Minus kippt.
    #    Jeder Abschlag im getesteten Bereich (<=3%) ist realistische Slippage auf
    #    Schlussquoten -> kippt es darin, ist der Edge fragil.
    flat_sweep = report["haircut_sweep"]["flat"]
    kip = next((row["haircut_pct"] for row in sorted(flat_sweep, key=lambda r: r["haircut_pct"])
                if row["end_capital"] < start), None)
    fragile = kip is not None and kip <= 3.0
    if kip is None:
        reasons.append("Preis-Abschlag: ueberlebt alle getesteten Abschlaege (robust).")
    else:
        reasons.append(f"Preis-Abschlag: kippt bei {kip:.0f}% ins Minus -> fragil "
                       f"(2-3% Slippage auf Schlussquoten sind realistisch).")

    # 2) Konzentration Bookie & Saison (flat). >60% PnL aus EINER Quelle = Artefakt.
    bk, bk_share = _max_share(report["concentration_by_bookie"]["flat"])
    se, se_share = _max_share(report["concentration_by_season"]["flat"])
    concentrated = (bk_share is not None and bk_share > 60.0) or (se_share is not None and se_share > 60.0)
    if bk_share is not None:
        reasons.append(f"Konzentration: groesster Bookie-Anteil {bk}={bk_share:.0f}% des PnL; "
                       f"groesste Saison {se}={se_share:.0f}%."
                       + (" -> Artefakt-Verdacht (eine Quelle traegt fast alles)." if concentrated else ""))

    # 3) Quoten-Bias / Devig: zwei Symptome — (a) Gewinn fast nur aus Aussenseitern,
    #    oder (b) das Modell setzt massenhaft auf extreme Aussenseiter, die VERLIEREN
    #    (klassische Devig-/Favorite-Longshot-Verzerrung).
    buckets = report["concentration_by_odds_bucket"]["flat"]
    longshot_gain_share = sum((buckets.get(b, {}).get("pnl_share_pct") or 0.0) for b in ("3.5-6.0", ">6.0"))
    extreme = buckets.get(">6.0", {})
    total_n = report["n_bets"] or 1
    extreme_bet_share = (extreme.get("n", 0) / total_n) * 100.0
    extreme_loses = (extreme.get("pnl", 0.0) or 0.0) < 0
    devig_bias = (longshot_gain_share > 60.0) or (extreme_bet_share > 40.0 and extreme_loses)
    reasons.append(
        f"Quoten-Bias: {extreme_bet_share:.0f}% aller Wetten liegen bei Quote >6.0 "
        f"(PnL dort {extreme.get('pnl', 0.0):+.0f}, ROI {extreme.get('roi_pct', 0.0):+.1f}%); "
        f"Gewinn-Anteil Aussenseiter >=3.5: {longshot_gain_share:.0f}%."
        + (" -> Devig-/Favorite-Longshot-Bias: viele verlierende Aussenseiter-Wetten." if devig_bias else "")
    )

    survives = (not fragile) and (not concentrated) and (not devig_bias) \
        and report["rules"]["flat"]["end_capital"] >= start

    caveats = [
        "IN-SAMPLE: Konsens-Devig lernt nichts; kein echtes Out-of-Sample. 'confirmed' = nur in-sample positiv.",
        "Kosten/Steuern/Gebuehren und Buchmacher-Limits NICHT modelliert (Gewinner werden zuerst limitiert).",
        "Schlussquoten sind die schaerfsten Quoten — den Schluss-Konsens zu schlagen ist eher Rausch-Verdacht.",
    ]
    if survives:
        verdict = ("Der Edge ueberlebt die getesteten Realitaets-Checks in-sample. Das ist NOTWENDIG, "
                   "aber NICHT hinreichend: es bleibt in-sample, ohne Kosten/Limits. Ein lernendes Modell "
                   "waere erst nach echtem Out-of-Sample mit Kosten zu rechtfertigen.")
        recommendation = "Vorsichtig weiterpruefen (Out-of-Sample-Daten, Kosten/Limits) — noch KEIN lernendes Modell."
    else:
        verdict = ("Der Edge scheitert bereits an der Realitaet (Preis-Abschlag / Konzentration / Quoten-Bias). "
                   "Die +PnL ist mit hoher Wahrscheinlichkeit Artefakt/Rauschen, nicht echter Vorteil.")
        recommendation = "KEIN lernendes Modell bauen — der Edge scheitert schon vor jeder Modellierung."

    return {"survives": survives, "verdict": verdict, "recommendation": recommendation,
            "reasons": reasons, "caveats": caveats}


def format_report(report: dict[str, Any]) -> str:
    """Menschenlesbarer Diagnose-Bericht (fuer die CLI)."""
    L: list[str] = []
    L.append(f"=== Value-Diagnose: {report['n_bets']} Wetten, Start {report['start_capital']:.0f} EUR ===")
    for rule in ("flat", "kelly"):
        s = report["rules"][rule]
        ruin = f", RUIN bei Wette {s['ruin_bet_index']} ({s['ruin_date']})" if s["ruined"] else ""
        L.append(f"[{rule:5}] Endkapital {s['end_capital']:.2f} EUR | ROI(Umsatz) {s['roi_pct']:+.2f}% | "
                 f"maxDD {s['max_drawdown_pct']:.1f}% | Trefferquote {s['hit_rate_pct']:.1f}% | "
                 f"Umsatz {s['turnover']:.0f}{ruin}")
    L.append("")
    L.append("Preis-Abschlag (Ausfuehrbarkeit) — Endkapital je Abschlag:")
    for rule in ("flat", "kelly"):
        cells = "  ".join(f"{r['haircut_pct']:.0f}%:{r['end_capital']:.1f}" for r in report["haircut_sweep"][rule])
        L.append(f"  {rule:5}  {cells}")
    L.append("")
    L.append("Konzentration nach Saison (flat) [PnL | Anteil | n | ROI]:")
    for name, g in sorted(report["concentration_by_season"]["flat"].items()):
        L.append(f"  {name:8} {g['pnl']:+8.2f} | {g['pnl_share_pct']}% | n={g['n']} | ROI {g['roi_pct']:+.1f}%")
    L.append("Konzentration nach Bookmaker (flat, Top 5):")
    top = sorted(report["concentration_by_bookie"]["flat"].items(), key=lambda kv: -(kv[1]["pnl"]))[:5]
    for name, g in top:
        L.append(f"  {name:8} {g['pnl']:+8.2f} | {g['pnl_share_pct']}% | n={g['n']} | ROI {g['roi_pct']:+.1f}%")
    L.append("Konzentration nach Quoten-Bucket (flat) [PnL | Anteil | n | ROI]:")
    for name in _ODDS_BUCKETS:
        g = report["concentration_by_odds_bucket"]["flat"].get(name)
        if g:
            L.append(f"  {name:8} {g['pnl']:+8.2f} | {g['pnl_share_pct']}% | n={g['n']} | ROI {g['roi_pct']:+.1f}%")
    L.append("")
    a = report["assessment"]
    L.append("--- Checks ---")
    for r in a["reasons"]:
        L.append(f"  - {r}")
    L.append("--- Ehrliche Einordnung ---")
    for c in a["caveats"]:
        L.append(f"  ! {c}")
    L.append("")
    L.append(f"FAZIT ({'ueberlebt' if a['survives'] else 'scheitert'}): {a['verdict']}")
    L.append(f"EMPFEHLUNG: {a['recommendation']}")
    return "\n".join(L)
