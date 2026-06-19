"""
oos.py — echter Out-of-Sample-CLV-Test fuer die robusten Kandidaten-Ligen.

Das ist der ERSTE echte Holdout-Test einer praediktiven Strategie in diesem
Projekt. Die League-Scan-Kandidaten (EC, I2, F2) sahen IN-SAMPLE gut aus — aber
in-sample heisst wenig, wenn man aus 17 Ligen die besten herauspickt. Hier
trennen wir je Liga sauber:

    Training (in-sample):  Saisons 2020/21, 2021/22, 2022/23, 2023/24
    Holdout  (out-of-sample): Saison 2024/25

Gemessen wird mit DEMSELBEN sauberen Aufbau wie im League-Scan: Bet-Quelle B365
(eine einzelne, realistisch erreichbare Quelle), CLV gegen die DEVIGTE Pinnacle-
SCHLUSSlinie, moderate Quoten 2.0-4.0. Das Urteil faellt ``validation.judge``
(dreistufig, requires_validation=True): ``confirmed`` nur, wenn das Holdout-CLV
ROBUST positiv bleibt; ``parked`` bei zu wenig Holdout-Daten oder schwachem OOS;
``rejected`` nur, wenn schon in-sample kein Signal da ist.

WICHTIG (gegen Selbstbetrug UND gegen vorschnelles Verwerfen): eine EINZELNE
Holdout-Saison einer unteren Liga hat WENIGE Wetten. Ist die Holdout-Stichprobe
kleiner als ``min_samples``, MUSS das Urteil ``parked`` sein (nicht ``confirmed``)
— ein OOS-Mittel ueber eine Handvoll Wetten ist kein Beweis.

EHRLICHE EINORDNUNG (gilt durchgehend):
- +1-2 % CLV ist ein DUENNES Signal — nah an der Schwelle, an der Limits und ein
  real nicht erreichbarer weicher Preis es auffressen.
- OOS-Bestaetigung hebt einen Kandidaten von "in-sample-Artefakt" zu "Kandidat
  fuer echten Edge" — aber Kosten/Limits bleiben UNMODELLIERT, und EINE Holdout-
  Saison ist nur EIN Test, kein Beweis.
- Bleibt KEINE Liga out-of-sample positiv, wird das klar gesagt.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any
import math

from arbfinder.diagnostics import _season
from arbfinder.leaguescan import _build_league_bets, clv_stats, load_events_by_league
from arbfinder.pinnacle import _r
from arbfinder.validation import judge

# Saison-Split.
TRAIN_SEASONS: tuple[str, ...] = ("2020/21", "2021/22", "2022/23", "2023/24")
HOLDOUT_SEASON: str = "2024/25"

# Kandidaten aus dem League-Scan; SC3 ist wegen n-Boden/Ausreisser-Bucket UNSICHER.
CANDIDATES: tuple[str, ...] = ("EC", "I2", "F2")
UNCERTAIN: tuple[str, ...] = ("SC3",)

# Aus wie vielen Ligen wurden die Kandidaten gepickt? (Mehrfachtest-Deflation.)
N_TRIALS_DEFAULT = 17
# "robust positiv" out-of-sample: Mittel-CLV muss diese Schwelle ueberschreiten.
DEFAULT_MIN_OOS = 0.5
# realistische OOS-Mindeststichprobe: darunter -> parked (eine untere Liga-Saison
# liefert wenige moderate-Quoten-Value-Wetten; <30 ist zu verrauscht fuer "confirmed").
DEFAULT_MIN_SAMPLES = 30

HONEST_NOTE = (
    "+1-2 % CLV ist ein DUENNES Signal, nah an der Schwelle, an der Buchmacher-"
    "Limits und ein real nicht erreichbarer weicher Preis es auffressen. Eine OOS-"
    "Bestaetigung hebt einen Kandidaten von 'in-sample-Artefakt' zu 'Kandidat fuer "
    "echten Edge' — aber Kosten/Limits bleiben UNMODELLIERT, und EINE Holdout-Saison "
    "ist nur EIN Test, kein Beweis. CLV gegen die DEVIGTE Pinnacle-Schluss ist die "
    "ehrlichste verfuegbare Messlatte; untere Ligen haben oft duenne/spaete Pinnacle-"
    "Abdeckung, was den Anker schwaecht."
)


def split_by_season(events: list) -> tuple[list, list]:
    """Teilt Events einer Liga in (Training, Holdout) anhand der Saison."""
    train, holdout = [], []
    for ev in events:
        s = _season(ev.start_time)
        if s == HOLDOUT_SEASON:
            holdout.append(ev)
        elif s in TRAIN_SEASONS:
            train.append(ev)
        # andere Saisons (sollte es hier nicht geben) -> bewusst ignoriert
    return train, holdout


def _clv_block(events: list, *, bet_source: str, min_edge: float,
               odds_min: float, odds_max: float) -> dict[str, Any]:
    """CLV-Kennzahlen einer Event-Menge (gleicher Aufbau wie League-Scan)."""
    bets, _diag = _build_league_bets(events, bet_source=bet_source, min_edge=min_edge,
                                     odds_min=odds_min, odds_max=odds_max)
    s = clv_stats(bets)
    return {
        "n_bets": len(bets),
        "n_with_clv": s["n"],
        "mean_clv_pct": s["mean_clv_pct"],
        "median_clv_pct": s["median_clv_pct"],
        "share_positive_clv_pct": s["share_positive_pct"],
    }


def evaluate_league(
    league: str, events: list, *, uncertain: bool, bet_source: str, min_edge: float,
    odds_min: float, odds_max: float, n_trials: int, min_oos: float, min_samples: int,
) -> dict[str, Any]:
    """In-Sample vs. Out-of-Sample CLV + dreistufiges Urteil fuer EINE Liga."""
    train, holdout = split_by_season(events)
    in_s = _clv_block(train, bet_source=bet_source, min_edge=min_edge,
                      odds_min=odds_min, odds_max=odds_max)
    out_s = _clv_block(holdout, bet_source=bet_source, min_edge=min_edge,
                       odds_min=odds_min, odds_max=odds_max)

    in_mean = in_s["mean_clv_pct"]
    out_mean = out_s["mean_clv_pct"]
    oos_n = out_s["n_with_clv"]

    if in_mean is None:
        # KEINE in-sample CLV-Beobachtungen = DATENLUECKE, nicht "kein Signal".
        # Niemals als 0.0 an judge geben (das wuerde als 'rejected' gelesen) — parken,
        # bis genug Train-Daten da sind (gleiche Logik wie _missing_block).
        vd = {
            "status": "parked",
            "reason": "keine in-sample CLV-Beobachtungen — nicht beurteilbar (mehr Train-Daten noetig)",
            "in_sample_edge": None, "out_of_sample_edge": out_mean,
            "n_trials": n_trials, "deflated_edge": None,
            "details": {"n_samples": oos_n, "min_samples": min_samples},
        }
    else:
        verdict = judge(
            in_sample_edge=in_mean,
            out_of_sample_edge=out_mean,                 # None -> parked (zu wenig Daten)
            n_trials=n_trials,
            requires_validation=True,                    # PRAEDIKTIV -> echte OOS-Pruefung
            min_in_sample=0.0,
            min_out_of_sample=min_oos,
            min_samples=min_samples,
            n_samples=oos_n,
        )
        vd = verdict.to_dict()
        vd["deflated_edge"] = _r(vd["deflated_edge"])    # JSON-sauber runden

    drop = _r(in_mean - out_mean) if (in_mean is not None and out_mean is not None) else None
    return {
        "league": league,
        "uncertain": uncertain,
        "in_sample": in_s,
        "out_of_sample": out_s,
        "delta": {
            "mean_clv_pct_drop": drop,               # in - out (positiv = OOS faellt ab)
            "out_stays_positive": bool(out_mean is not None and out_mean > 0),
        },
        "verdict": vd,
    }


def _missing_block(league: str, uncertain: bool, n_trials: int) -> dict[str, Any]:
    """Liga nicht im csv_dir -> Datenproblem, NICHT 'rejected' (kein Fehlsignal)."""
    return {
        "league": league, "uncertain": uncertain,
        "error": "keine Daten fuer diese Liga im csv_dir (ggf. download_data.py nutzen)",
        "in_sample": None, "out_of_sample": None, "delta": None,
        "verdict": {
            "status": "parked",
            "reason": "keine Daten — Holdout nicht pruefbar (download_data.py nutzen)",
            "in_sample_edge": None, "out_of_sample_edge": None,
            "n_trials": n_trials, "deflated_edge": None, "details": {},
        },
    }


def run(
    csv_dir: str | Path, *,
    candidates: tuple[str, ...] = CANDIDATES,
    uncertain: tuple[str, ...] = UNCERTAIN,
    bet_source: str = "B365",
    min_edge: float = 2.0,
    odds_min: float = 2.0,
    odds_max: float = 4.0,
    n_trials: int = N_TRIALS_DEFAULT,
    min_oos: float = DEFAULT_MIN_OOS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> tuple[dict, dict]:
    """Fuehrt den OOS-CLV-Test fuer alle Kandidaten (+ unsichere) aus."""
    by_league, skipped, _loaded = load_events_by_league(csv_dir, bet_source=bet_source)

    targets: list[str] = list(candidates) + [u for u in uncertain if u not in candidates]
    leagues: dict[str, dict] = {}
    plot_by_league: dict[str, dict] = {}
    for lg in targets:
        is_unc = lg in uncertain
        events = by_league.get(lg, [])
        if not events:
            leagues[lg] = _missing_block(lg, is_unc, n_trials)
            continue
        block = evaluate_league(
            lg, events, uncertain=is_unc, bet_source=bet_source, min_edge=min_edge,
            odds_min=odds_min, odds_max=odds_max, n_trials=n_trials,
            min_oos=min_oos, min_samples=min_samples)
        leagues[lg] = block
        plot_by_league[lg] = {
            "league": lg, "uncertain": is_unc,
            "in_sample": block["in_sample"], "out_of_sample": block["out_of_sample"],
            "status": block["verdict"]["status"],
        }

    report = {
        "meta": {
            "csv_dir": str(csv_dir),
            "bet_source": bet_source,
            "clv_benchmark": "pinnacle_close_devigged",
            "odds_filter": [odds_min, odds_max],
            "min_edge": min_edge,
            "train_seasons": list(TRAIN_SEASONS),
            "holdout_season": HOLDOUT_SEASON,
            "n_trials": n_trials,
            "judge_params": {"min_in_sample": 0.0, "min_out_of_sample": min_oos,
                             "min_samples": min_samples},
            "candidates": list(candidates),
            "uncertain": list(uncertain),
            "skipped_files": skipped,
        },
        "leagues": leagues,
        "honest_note": HONEST_NOTE,
    }
    return report, {"by_league": plot_by_league}


def summary_report(report: dict) -> dict:
    """Nur die Urteile je Liga (results/oos_summary.json, zum Hochladen)."""
    verdicts = {}
    for lg, b in report["leagues"].items():
        v = b["verdict"]
        verdicts[lg] = {
            "status": v["status"],
            "reason": v["reason"],
            "uncertain": b.get("uncertain", False),
            "in_sample_mean_clv_pct": v.get("in_sample_edge"),
            "out_of_sample_mean_clv_pct": v.get("out_of_sample_edge"),
            "out_of_sample_n": (b["out_of_sample"]["n_with_clv"]
                                if b.get("out_of_sample") else 0),
        }
    statuses = [v["status"] for v in verdicts.values()]
    return {
        "meta": {k: report["meta"][k] for k in
                 ("bet_source", "clv_benchmark", "odds_filter", "train_seasons",
                  "holdout_season", "n_trials", "judge_params")},
        "verdicts": verdicts,
        "any_confirmed": "confirmed" in statuses,
        "counts": {s: statuses.count(s) for s in ("confirmed", "parked", "rejected")},
        "honest_note": report["honest_note"],
        "summary_text": summary_text(report),
    }


def summary_text(report: dict) -> str:
    """Nuechterne 3-5-Satz-Zusammenfassung (ehrlich, keine Schoenfaerberei)."""
    rows = []
    confirmed = []
    for lg, b in report["leagues"].items():
        v = b["verdict"]
        tag = " (unsicher)" if b.get("uncertain") else ""
        ins = v.get("in_sample_edge")
        out = v.get("out_of_sample_edge")
        oos_n = b["out_of_sample"]["n_with_clv"] if b.get("out_of_sample") else 0
        rows.append(f"{lg}{tag}: in {ins}% -> OOS {out}% (n={oos_n}) [{v['status']}]")
        if v["status"] == "confirmed":
            confirmed.append(lg)
    head = (f"Out-of-Sample-CLV-Test (Holdout {report['meta']['holdout_season']}, "
            f"Bet-Quelle {report['meta']['bet_source']}, CLV vs DEVIGTE Pinnacle-Schluss). ")
    if confirmed:
        verdict_line = (f"OOS-positiv bestaetigt: {', '.join(confirmed)}. "
                        "Das hebt sie von in-sample-Artefakt zu Kandidat fuer echten Edge.")
    else:
        verdict_line = ("KEINE Liga bleibt out-of-sample robust positiv (alle parked/rejected) "
                        "— der in-sample-Vorsprung haelt der Holdout-Saison nicht stand bzw. "
                        "die OOS-Stichprobe ist zu duenn.")
    return (head + verdict_line + " " + " | ".join(rows)
            + " Hinweis: +1-2 % CLV ist duenn; Kosten/Limits unmodelliert; EINE Holdout-"
              "Saison ist kein Beweis.")


def make_plots(report: dict, plotdata: dict, out_dir: str | Path) -> list[str]:
    """Je Liga ein Balkenpaar In-Sample vs. Out-of-Sample (mean CLV) + share_positive,
    plus eine Uebersicht ueber alle Ligen."""
    from arbfinder import plotting

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    rows = []
    for lg, pl in plotdata["by_league"].items():
        if not pl.get("in_sample") or not pl.get("out_of_sample"):
            continue
        safe = lg.replace("/", "_")
        paths.append(plotting.plot_oos_league(
            lg, pl["in_sample"], pl["out_of_sample"], status=pl.get("status", ""),
            uncertain=pl.get("uncertain", False),
            out_path=str(d / f"oos_{safe}.png")))
        rows.append({"league": lg, "uncertain": pl.get("uncertain", False),
                     "in_mean": pl["in_sample"]["mean_clv_pct"],
                     "out_mean": pl["out_of_sample"]["mean_clv_pct"],
                     "status": pl.get("status", "")})
    if rows:
        paths.append(plotting.plot_oos_overview(rows, out_path=str(d / "oos_overview.png")))
    return paths


# =========================================================================== #
# WALK-FORWARD: rollende Holdouts + POOLING ueber Folds + Konfidenzintervall
# =========================================================================== #
# Saison-Reihenfolge (kanonische _season-Labels). Expanding-Window.
SEASON_ORDER: tuple[str, ...] = ("2020/21", "2021/22", "2022/23", "2023/24", "2024/25")
DEFAULT_MIN_TRAIN = 3                 # Mindest-Trainingsfenster vor einem Holdout
Z95 = 1.959964                        # z fuer ein 95%-Intervall (Normal-Naeherung)
RAZOR_THIN_CI = 0.5                   # KI-Untergrenze darunter -> nur "KNAPP" abgesichert

WF_HONEST_NOTE = (
    "Konsistent positives GEPOOLTES OOS-CLV ueber mehrere unabhaengige Holdout-Saisons ist "
    "deutlich staerker als ein Einzel-Holdout — aber +1-2 % bleibt DUENN. Das 95%-KI ist eine "
    "NORMAL-Naeherung, die Wetten als unabhaengig behandelt; real sind Wetten (gleiche Partie/"
    "Spieltag/marktweite Bewegungen) KORRELIERT, die wahre Unsicherheit ist GROESSER (das KI ist "
    "also optimistisch/zu eng). Schliesst das KI die 0 NICHT aus, ist selbst ein positiver "
    "Mittelwert statistisch NICHT abgesichert. Kosten/Steuern/Limits bleiben unmodelliert; ein "
    "bestandener Walk-Forward ist ein SIGNAL, kein Gewinn-Beweis."
)


def walk_forward_folds(seasons_present: set[str], *, min_train: int = DEFAULT_MIN_TRAIN,
                       order: tuple[str, ...] = SEASON_ORDER) -> list[tuple[tuple[str, ...], str]]:
    """Expanding-Window-Folds: Holdout = jede vorhandene Saison, deren VORLAUF
    (alle frueheren vorhandenen Saisons) >= ``min_train`` ist. Train = genau
    dieser Vorlauf. Returns ``[(train_seasons, holdout_season), ...]``.
    """
    present = [s for s in order if s in seasons_present]
    folds: list[tuple[tuple[str, ...], str]] = []
    for i, hold in enumerate(present):
        train = tuple(present[:i])
        if len(train) >= min_train:
            folds.append((train, hold))
    return folds


def _clv_values(events: list, seasons: set[str], *, bet_source: str, min_edge: float,
                odds_min: float, odds_max: float) -> list[float]:
    """CLV-Werte aller selektierten Wetten in den gegebenen Saisons (gleiche
    Selektionslogik wie der Einzel-Holdout-Lauf)."""
    sel = [ev for ev in events if _season(ev.start_time) in seasons]
    bets, _diag = _build_league_bets(sel, bet_source=bet_source, min_edge=min_edge,
                                     odds_min=odds_min, odds_max=odds_max)
    return [b.clv_pct for b in bets if b.clv_pct is not None]


def _pooled_stats(clvs: list[float]) -> dict[str, Any]:
    """Gepoolte CLV-Kennzahlen + Standardfehler + 95%-KI (Normal-Naeherung).

    ``ci_excludes_zero`` ist True, wenn das KI komplett ueber ODER unter 0 liegt
    (0 also NICHT eingeschlossen). Bei n<2 ist kein KI definiert (None).
    """
    n = len(clvs)
    if n == 0:
        return {"n": 0, "mean_clv_pct": None, "median_clv_pct": None,
                "share_positive_clv_pct": None, "std": None, "standard_error": None,
                "ci95_low": None, "ci95_high": None, "ci_excludes_zero": None}
    m = sum(clvs) / n
    share = sum(1 for c in clvs if c > 0) / n * 100.0
    std = se = lo = hi = None
    excludes = None
    if n >= 2:
        var = sum((c - m) ** 2 for c in clvs) / (n - 1)      # Stichproben-Varianz
        std = math.sqrt(var)
        se = std / math.sqrt(n)
        lo, hi = m - Z95 * se, m + Z95 * se
        excludes = bool(lo > 0 or hi < 0)                     # 0 ausserhalb [lo, hi]?
    return {"n": n, "mean_clv_pct": _r(m), "median_clv_pct": _r(median(clvs)),
            "share_positive_clv_pct": _r(share, 1), "std": _r(std),
            "standard_error": _r(se), "ci95_low": _r(lo), "ci95_high": _r(hi),
            "ci_excludes_zero": excludes}


def _wf_parked(league: str, uncertain: bool, n_trials: int, reason: str,
               pooled: dict | None = None, in_edge: float | None = None) -> dict[str, Any]:
    """Walk-Forward-Block im Park-Zustand (Datenluecke / zu wenige Saisons)."""
    return {
        "league": league, "uncertain": uncertain, "folds": [],
        "consistency": {"positive_folds": 0, "total_folds": 0, "label": "0/0"},
        "in_sample_mean_clv_pct": _r(in_edge) if in_edge is not None else None,
        "pooled_oos": pooled or _pooled_stats([]),
        "statistically_secured": False,
        "verdict": {"status": "parked", "reason": reason, "in_sample_edge": _r(in_edge),
                    "out_of_sample_edge": (pooled or {}).get("mean_clv_pct"),
                    "n_trials": n_trials, "deflated_edge": None, "details": {}},
    }


def evaluate_league_walkforward(
    league: str, events: list, *, uncertain: bool, bet_source: str, min_edge: float,
    odds_min: float, odds_max: float, min_train: int, n_trials: int,
    min_oos: float, min_samples: int,
) -> dict[str, Any]:
    """Walk-Forward ueber rollende Holdouts + Pooling + Urteil fuer EINE Liga."""
    seasons_present = {_season(ev.start_time) for ev in events}
    fold_defs = walk_forward_folds(seasons_present, min_train=min_train)
    if not fold_defs:
        return _wf_parked(league, uncertain, n_trials,
                          f"nicht genug Saisons fuer Walk-Forward (min_train={min_train}, "
                          f"vorhanden={len(seasons_present)})")

    folds: list[dict] = []
    pooled_vals: list[float] = []
    train_means: list[float] = []
    positive_folds = 0
    for train_seasons, hold in fold_defs:
        oos_vals = _clv_values(events, {hold}, bet_source=bet_source, min_edge=min_edge,
                               odds_min=odds_min, odds_max=odds_max)
        train_vals = _clv_values(events, set(train_seasons), bet_source=bet_source,
                                 min_edge=min_edge, odds_min=odds_min, odds_max=odds_max)
        fold_mean = (sum(oos_vals) / len(oos_vals)) if oos_vals else None
        if fold_mean is not None and fold_mean > 0:
            positive_folds += 1
        if train_vals:
            train_means.append(sum(train_vals) / len(train_vals))
        pooled_vals.extend(oos_vals)
        folds.append({
            "holdout_season": hold, "train_seasons": list(train_seasons),
            "n": len(oos_vals), "train_n": len(train_vals),
            "mean_clv_pct": _r(fold_mean),
            "median_clv_pct": _r(median(oos_vals)) if oos_vals else None,
            "share_positive_clv_pct": (_r(100.0 * sum(1 for c in oos_vals if c > 0) / len(oos_vals), 1)
                                       if oos_vals else None),
        })

    pooled = _pooled_stats(pooled_vals)
    in_edge = (sum(train_means) / len(train_means)) if train_means else None
    total_folds = len(fold_defs)

    if in_edge is None:
        return _wf_parked(league, uncertain, n_trials,
                          "keine in-sample CLV-Beobachtungen ueber die Folds — nicht beurteilbar",
                          pooled=pooled) | {
            "folds": folds,
            "consistency": {"positive_folds": positive_folds, "total_folds": total_folds,
                            "label": f"{positive_folds}/{total_folds}"},
        }

    verdict = judge(
        in_sample_edge=in_edge, out_of_sample_edge=pooled["mean_clv_pct"],
        n_trials=n_trials, requires_validation=True, min_in_sample=0.0,
        min_out_of_sample=min_oos, min_samples=min_samples, n_samples=pooled["n"])
    vd = verdict.to_dict()
    vd["deflated_edge"] = _r(vd["deflated_edge"])

    # Statistisch abgesichert nur, wenn confirmed UND das KI komplett ueber 0 liegt.
    # An dieselbe UNGERUNDETE Basis wie ci_excludes_zero koppeln (sonst widersprechen
    # sich die Felder bei einer KI-Untergrenze knapp ueber 0, die auf 0.0 rundet).
    secured = bool(vd["status"] == "confirmed" and pooled["ci_excludes_zero"]
                   and (pooled["mean_clv_pct"] or 0) > 0)
    return {
        "league": league, "uncertain": uncertain, "folds": folds,
        "consistency": {"positive_folds": positive_folds, "total_folds": total_folds,
                        "label": f"{positive_folds}/{total_folds}"},
        "in_sample_mean_clv_pct": _r(in_edge),
        "pooled_oos": pooled,
        "statistically_secured": secured,
        "verdict": vd,
    }


def run_walkforward(
    csv_dir: str | Path, *,
    candidates: tuple[str, ...] = CANDIDATES,
    uncertain: tuple[str, ...] = UNCERTAIN,
    bet_source: str = "B365",
    min_edge: float = 2.0,
    odds_min: float = 2.0,
    odds_max: float = 4.0,
    min_train: int = DEFAULT_MIN_TRAIN,
    n_trials: int = N_TRIALS_DEFAULT,
    min_oos: float = DEFAULT_MIN_OOS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> tuple[dict, dict]:
    """Walk-Forward-CLV-Test (gepoolte Holdouts) fuer alle Kandidaten (+ unsichere)."""
    by_league, skipped, _loaded = load_events_by_league(csv_dir, bet_source=bet_source)
    targets: list[str] = list(candidates) + [u for u in uncertain if u not in candidates]
    leagues: dict[str, dict] = {}
    plot_by_league: dict[str, dict] = {}
    for lg in targets:
        is_unc = lg in uncertain
        events = by_league.get(lg, [])
        if not events:
            leagues[lg] = _wf_parked(lg, is_unc, n_trials,
                                     "keine Daten fuer diese Liga im csv_dir (download_data.py nutzen)")
            continue
        block = evaluate_league_walkforward(
            lg, events, uncertain=is_unc, bet_source=bet_source, min_edge=min_edge,
            odds_min=odds_min, odds_max=odds_max, min_train=min_train, n_trials=n_trials,
            min_oos=min_oos, min_samples=min_samples)
        leagues[lg] = block
        plot_by_league[lg] = {
            "league": lg, "uncertain": is_unc, "folds": block["folds"],
            "pooled": block["pooled_oos"], "in_sample_mean": block["in_sample_mean_clv_pct"],
            "status": block["verdict"]["status"], "secured": block["statistically_secured"],
        }

    report = {
        "meta": {
            "csv_dir": str(csv_dir), "bet_source": bet_source,
            "clv_benchmark": "pinnacle_close_devigged",
            "odds_filter": [odds_min, odds_max], "min_edge": min_edge,
            "season_order": list(SEASON_ORDER), "min_train_seasons": min_train,
            "n_trials": n_trials,
            "judge_params": {"min_in_sample": 0.0, "min_out_of_sample": min_oos,
                             "min_samples": min_samples},
            "ci_method": "normal-approx 95% (z=1.96), Wetten als unabhaengig behandelt",
            "candidates": list(candidates), "uncertain": list(uncertain),
            "skipped_files": skipped,
        },
        "leagues": leagues,
        "honest_note": WF_HONEST_NOTE,
    }
    return report, {"by_league": plot_by_league}


def walkforward_summary(report: dict) -> dict:
    """Nur Urteile + gepoolte Kennzahlen + Konsistenz + KI je Liga (zum Hochladen)."""
    verdicts = {}
    for lg, b in report["leagues"].items():
        p = b["pooled_oos"]
        v = b["verdict"]
        verdicts[lg] = {
            "status": v["status"], "reason": v["reason"],
            "uncertain": b.get("uncertain", False),
            "statistically_secured": b.get("statistically_secured", False),
            "consistency": b["consistency"]["label"],
            "in_sample_mean_clv_pct": b.get("in_sample_mean_clv_pct"),
            "pooled_oos_n": p["n"],
            "pooled_oos_mean_clv_pct": p["mean_clv_pct"],
            "pooled_oos_median_clv_pct": p["median_clv_pct"],
            "pooled_oos_share_positive_pct": p["share_positive_clv_pct"],
            "ci95": [p["ci95_low"], p["ci95_high"]],
            "ci_excludes_zero": p["ci_excludes_zero"],
        }
    statuses = [v["status"] for v in verdicts.values()]
    return {
        "meta": {k: report["meta"][k] for k in
                 ("bet_source", "clv_benchmark", "odds_filter", "season_order",
                  "min_train_seasons", "n_trials", "judge_params", "ci_method")},
        "verdicts": verdicts,
        "any_confirmed": "confirmed" in statuses,
        "any_secured": any(v["statistically_secured"] for v in verdicts.values()),
        "counts": {s: statuses.count(s) for s in ("confirmed", "parked", "rejected")},
        "honest_note": report["honest_note"],
        "summary_text": walkforward_summary_text(report),
    }


def walkforward_summary_text(report: dict) -> str:
    """Nuechterne Zusammenfassung — macht 'positiv aber NICHT abgesichert' und
    'nur KNAPP abgesichert' explizit (keine Schoenfaerberei einer duennen KI)."""
    rows = []
    secured = []
    razor_thin = []
    for lg, b in report["leagues"].items():
        p = b["pooled_oos"]
        v = b["verdict"]
        c = b["consistency"]
        tag = " (unsicher)" if b.get("uncertain") else ""
        ci = (f"KI[{p['ci95_low']},{p['ci95_high']}]" if p["ci95_low"] is not None else "KI n/a")
        if b.get("statistically_secured"):
            secured.append(lg)
            lo = p["ci95_low"]
            if lo is not None and lo < RAZOR_THIN_CI:        # Untergrenze knapp ueber 0
                mark = f", KNAPP abgesichert (KI-Untergrenze {lo}%, optimistische Annahme)"
                razor_thin.append(lg)
            else:
                mark = ", abgesichert"
        elif v["status"] == "confirmed":
            mark = ", aber NICHT abgesichert (0 im KI)"
        else:
            mark = ""
        rows.append(f"{lg}{tag}: in {b.get('in_sample_mean_clv_pct')}% -> pooled-OOS "
                    f"{p['mean_clv_pct']}% (n={p['n']}, {c['label']} Folds+, {ci}) "
                    f"[{v['status']}{mark}]")
    head = (f"Walk-Forward-CLV-Test (>= {report['meta']['min_train_seasons']} Saisons Training, "
            f"gepoolte Holdouts, B365, CLV vs DEVIGTE Pinnacle-Schluss). ")
    if secured:
        thin = (f" (davon nur KNAPP: {', '.join(razor_thin)} — KI-Untergrenze nahe 0)"
                if razor_thin else "")
        vline = (f"Statistisch abgesichert (gepooltes 95%-KI ueber 0, OPTIMISTISCHE "
                 f"Unabhaengigkeitsannahme): {', '.join(secured)}{thin}. Staerker als ein Einzel-"
                 "Holdout — aber +1-2 % bleibt duenn, und knappe KI-Untergrenzen koennen unter "
                 "der realen Korrelation der Wetten unter 0 rutschen.")
    else:
        vline = ("KEINE Liga ist statistisch abgesichert (kein confirmed-Urteil mit gepooltem "
                 "95%-KI ueber 0).")
    return (head + vline + " " + " | ".join(rows)
            + " Hinweis: KI ist Normal-Naeherung (Wetten korreliert -> echte Unsicherheit groesser); "
              "Kosten/Limits unmodelliert; Signal, kein Gewinn-Beweis.")


def make_walkforward_plots(report: dict, plotdata: dict, out_dir: str | Path) -> list[str]:
    """Je Liga Fold-fuer-Fold-CLV + gepoolte Linie/KI und In-Sample vs. gepoolt-OOS;
    plus eine Uebersicht (gepooltes OOS mean +/- KI je Liga)."""
    from arbfinder import plotting

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    overview_rows = []
    for lg, pl in plotdata["by_league"].items():
        if not pl.get("folds"):
            continue
        safe = lg.replace("/", "_")
        paths.append(plotting.plot_walkforward_league(
            lg, pl["folds"], pl["pooled"], pl.get("in_sample_mean"),
            status=pl.get("status", ""), secured=pl.get("secured", False),
            uncertain=pl.get("uncertain", False),
            out_path=str(d / f"walkforward_{safe}.png")))
        overview_rows.append({"league": lg, "uncertain": pl.get("uncertain", False),
                              "pooled": pl["pooled"], "secured": pl.get("secured", False)})
    if overview_rows:
        paths.append(plotting.plot_walkforward_overview(
            overview_rows, out_path=str(d / "walkforward_overview.png")))
    return paths
