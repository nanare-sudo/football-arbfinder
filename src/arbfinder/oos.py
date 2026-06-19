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
from typing import Any

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
