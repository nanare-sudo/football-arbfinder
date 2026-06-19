import json
import math
import os
from datetime import datetime, timezone

import pytest

from arbfinder import cli, leaguescan
from arbfinder.fair_probability import PinnacleAnchorModel
from arbfinder.models import Event, Market

_DIR = "tests/data/leagues"
_DT = datetime(2023, 8, 12, 15, 0, tzinfo=timezone.utc)

# Lockere Kriterien, damit die kleinen Beispiel-CSVs die robust=True-Logik testen.
_RELAXED = {"min_bets": 2, "min_bucket_n": 1, "min_positive_buckets": 2}


def _event(*, psh, psd, psa, psch, pscd, psca, b365h, b365d, b365a, result="H"):
    odds = {
        "Home": {"PS": psh, "PSC": psch, "B365": b365h},
        "Draw": {"PS": psd, "PSC": pscd, "B365": b365d},
        "Away": {"PS": psa, "PSC": psca, "B365": b365a},
    }
    return Event("e", "Home", "Away", _DT, result=result, markets=[Market("h2h", odds, 3)])


def _has_no_nonfinite(obj) -> bool:
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_has_no_nonfinite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_has_no_nonfinite(v) for v in obj)
    return True


# --------------------------------------------------------------------------- #
# Loader: alle CSVs eines Ordners, je Liga getrennt, Skips sichtbar
# --------------------------------------------------------------------------- #
def test_loader_gruppiert_je_liga_und_meldet_skips():
    by_league, skipped, loaded = leaguescan.load_events_by_league(_DIR, bet_source="B365")
    assert set(by_league) == {"E1", "D2"}
    assert len(by_league["E1"]) == 8 and len(by_league["D2"]) == 4
    # noPS-Datei wird NICHT still verschluckt, sondern mit Grund vermerkt:
    assert any(s["file"] == "E2_noPS.csv" and "PS" in s["reason"] for s in skipped)
    assert {"E1.csv", "D2.csv"} <= set(loaded) and "E2_noPS.csv" not in loaded


def test_loader_kein_ordner_wirft():
    with pytest.raises((NotADirectoryError, FileNotFoundError)):
        leaguescan.load_events_by_league("tests/data/leagues/E1.csv", bet_source="B365")


# --------------------------------------------------------------------------- #
# Korrektur 2: CLV gegen die DEVIGTE Schluss (nicht die rohe Quote)
# --------------------------------------------------------------------------- #
def test_clv_gegen_devigte_schluss_nicht_roh():
    ev = _event(psh=2.10, psd=3.50, psa=3.80, psch=2.05, pscd=3.55, psca=3.90,
                b365h=2.30, b365d=3.00, b365a=3.20)
    bets, diag = leaguescan._build_league_bets([ev], bet_source="B365", min_edge=2.0,
                                               odds_min=2.0, odds_max=4.0)
    assert len(bets) == 1                                  # nur Home qualifiziert
    assert diag["n_edge_in_range"] == 1
    h = bets[0]
    # faire (devigte) Schluss-Wahrscheinlichkeit Home:
    fair_close = PinnacleAnchorModel(anchor="close").estimate(ev.markets[0].odds)["Home"]
    expected = (2.30 * fair_close - 1.0) * 100.0
    assert abs(h.clv_pct - expected) < 1e-9
    # devigtes CLV ist STRENGER (kleiner) als das rohe CLV gegen PSC:
    raw_clv = (2.30 / 2.05 - 1.0) * 100.0
    assert h.clv_pct < raw_clv
    # pinnacle_close haelt die faire (devigte) Benchmark-Quote, nicht die rohe PSC:
    assert abs(h.pinnacle_close - 1.0 / fair_close) < 1e-9


# --------------------------------------------------------------------------- #
# Korrektur 3: moderater Quotenfilter 2.0-4.0
# --------------------------------------------------------------------------- #
def test_quotenfilter_schliesst_ausserhalb_aus():
    ev = _event(psh=2.10, psd=3.50, psa=3.80, psch=2.05, pscd=3.55, psca=3.90,
                b365h=2.30, b365d=3.00, b365a=3.20)
    drin, _ = leaguescan._build_league_bets([ev], bet_source="B365", min_edge=2.0,
                                            odds_min=2.0, odds_max=4.0)
    raus, diag = leaguescan._build_league_bets([ev], bet_source="B365", min_edge=2.0,
                                               odds_min=2.0, odds_max=2.2)   # 2.30 > 2.2
    assert len(drin) == 1 and len(raus) == 0
    # der Edge-Treffer wird als Aussenseiter (>odds_max) SICHTBAR verworfen:
    assert diag["n_edge_pass"] == 1 and diag["n_edge_above_range"] == 1


def test_odds_filter_diag_favorit_und_summen_invariante():
    # Favorit: Edge-Treffer mit Quote < odds_min wird als below_range gezaehlt.
    fav = _event(psh=1.40, psd=5.00, psa=9.00, psch=1.40, pscd=5.00, psca=9.00,
                 b365h=1.50, b365d=5.00, b365a=9.00)
    bets, diag = leaguescan._build_league_bets([fav], bet_source="B365", min_edge=2.0,
                                               odds_min=2.0, odds_max=4.0)
    assert len(bets) == 0                                    # Favorit unter odds_min -> keine Wette
    assert diag["n_edge_pass"] == 1 and diag["n_edge_below_range"] == 1
    # Summen-Invariante: pass == in_range + below_range + above_range
    assert (diag["n_edge_pass"]
            == diag["n_edge_in_range"] + diag["n_edge_below_range"] + diag["n_edge_above_range"])


def test_moderate_bucket_grenzen():
    assert leaguescan._moderate_bucket(2.0) == "<2.5"
    assert leaguescan._moderate_bucket(2.5) == "2.5-3.0"
    assert leaguescan._moderate_bucket(3.0) == "3.0-3.5"
    assert leaguescan._moderate_bucket(3.5) == "3.5+"
    assert leaguescan._moderate_bucket(4.0) == "3.5+"


# --------------------------------------------------------------------------- #
# Robustheits-Logik
# --------------------------------------------------------------------------- #
def _block(mean, share, n, buckets):
    return {"clv": {"mean_clv_pct": mean, "share_positive_pct": share, "n": n},
            "by_odds_bucket": buckets}


def test_robust_true_und_gruende():
    b = _block(5.0, 60.0, 100,
               [{"bucket": "<2.5", "n_bets": 30, "clv_mean_pct": 3.0},
                {"bucket": "2.5-3.0", "n_bets": 30, "clv_mean_pct": 2.0}])
    ok, reasons = leaguescan._is_robust(b, leaguescan.DEFAULT_ROBUST)
    assert ok is True
    assert any("2 von 2" in r for r in reasons)


def test_robust_false_negativ_kleine_stichprobe_einzelbucket():
    crit = leaguescan.DEFAULT_ROBUST
    neg, _ = leaguescan._is_robust(_block(-1.0, 60.0, 100,
        [{"bucket": "<2.5", "n_bets": 30, "clv_mean_pct": 3.0},
         {"bucket": "2.5-3.0", "n_bets": 30, "clv_mean_pct": 2.0}]), crit)
    small, _ = leaguescan._is_robust(_block(5.0, 60.0, 10,
        [{"bucket": "<2.5", "n_bets": 5, "clv_mean_pct": 3.0},
         {"bucket": "2.5-3.0", "n_bets": 5, "clv_mean_pct": 2.0}]), crit)
    single_ok, single_reasons = leaguescan._is_robust(_block(5.0, 60.0, 100,
        [{"bucket": "<2.5", "n_bets": 100, "clv_mean_pct": 3.0}]), crit)
    assert neg is False and small is False and single_ok is False
    assert any("nur ein Bucket" in r for r in single_reasons)


# --------------------------------------------------------------------------- #
# Gesamt-Scan: Struktur, Ranking, keine NaN, Korrekturen in meta
# --------------------------------------------------------------------------- #
def test_scan_struktur_ranking_und_kein_nan():
    report, plotdata = leaguescan.scan(_DIR, bet_source="B365", robust_criteria=_RELAXED)
    m = report["meta"]
    assert m["bet_source"] == "B365"                       # Korrektur 1
    assert m["clv_benchmark"] == "pinnacle_close_devigged"  # Korrektur 2
    assert m["odds_filter"] == [2.0, 4.0]                   # Korrektur 3
    assert m["n_files_skipped"] == 1
    # Ranking nach mean_clv: E1 (positiv) vor D2 (negativ).
    assert [r["league"] for r in report["ranking"]] == ["E1", "D2"]
    assert report["leagues"]["E1"]["robust"] is True
    assert report["leagues"]["D2"]["robust"] is False
    assert report["leagues"]["E1"]["clv"]["share_positive_pct"] == 100.0
    # Quotenfilter ist sichtbar dokumentiert (keine stille Beschneidung):
    diag = report["leagues"]["E1"]["odds_filter_diag"]
    assert diag["n_edge_in_range"] == 8 and set(diag) == {
        "n_edge_pass", "n_edge_in_range", "n_edge_below_range", "n_edge_above_range"}
    assert (diag["n_edge_pass"]
            == diag["n_edge_in_range"] + diag["n_edge_below_range"] + diag["n_edge_above_range"])
    assert report["verdict"]["best_league"] == "E1" and report["verdict"]["any_robust"] is True
    # JSON valide, keine NaN/inf:
    s = json.dumps(report)
    assert "NaN" not in s and "Infinity" not in s and _has_no_nonfinite(report)
    assert set(plotdata) == {"by_league", "ranking"}


def test_scan_default_bet_source_ist_b365():
    # Korrektur 1: die Default-Bet-Quelle MUSS B365 sein (NICHT Max).
    report, _ = leaguescan.scan(_DIR)
    assert report["meta"]["bet_source"] == "B365"


def test_scan_default_kriterien_nicht_robust_aber_ehrlich():
    # Default min_bets=50 -> die kleinen Beispiel-Ligen sind NICHT robust.
    report, _ = leaguescan.scan(_DIR, bet_source="B365")
    v = report["verdict"]
    assert v["any_robust"] is False
    assert v["best_league"] == "E1" and v["best_basis"] == "bestes_mean_clv_KLEINE_stichprobe"
    assert "KEINE Liga" in v["summary"]


# --------------------------------------------------------------------------- #
# best_league.json
# --------------------------------------------------------------------------- #
def test_best_league_report_robust_und_nicht_robust():
    rep_r, _ = leaguescan.scan(_DIR, bet_source="B365", robust_criteria=_RELAXED)
    best_r = leaguescan.best_league_report(rep_r)
    assert best_r["league"] == "E1" and best_r["robust"] is True and "note" not in best_r
    assert best_r["meta"]["clv_benchmark"] == "pinnacle_close_devigged"
    json.dumps(best_r)                                      # serialisierbar

    rep_d, _ = leaguescan.scan(_DIR, bet_source="B365")    # default -> nicht robust
    best_d = leaguescan.best_league_report(rep_d)
    assert best_d["robust"] is False and "ACHTUNG" in best_d["note"]


def test_best_league_report_leer(tmp_path):
    # Ordner ohne verwertbare CSVs -> kein best, klare Aussage statt Crash.
    (tmp_path / "leer.csv").write_text("Div,Date,HomeTeam,AwayTeam,FTR,B365H,B365D,B365A\n")
    report, _ = leaguescan.scan(tmp_path, bet_source="B365")
    best = leaguescan.best_league_report(report)
    assert best["league"] is None and "Keine Liga" in best["note"]
    assert report["verdict"]["any_robust"] is False


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #
def test_make_plots_erzeugt_dateien(tmp_path):
    pytest.importorskip("matplotlib")
    report, plotdata = leaguescan.scan(_DIR, bet_source="B365", robust_criteria=_RELAXED)
    paths = leaguescan.make_plots(report, plotdata, tmp_path)
    # league_clv + je Top-Liga (hier 2) ein Histogramm + eine Bankroll-Kurve:
    assert any("league_clv" in p for p in paths)
    assert all(os.path.exists(p) and p.endswith(".png") for p in paths)


# --------------------------------------------------------------------------- #
# CLI end-to-end
# --------------------------------------------------------------------------- #
def test_cli_league_scan_schreibt_beide_jsons(tmp_path, capsys):
    out = tmp_path / "scan.json"
    best = tmp_path / "best.json"
    rc = cli.main(["league-scan", "--csv-dir", _DIR,
                   "--out-json", str(out), "--best-json", str(best)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "JSON ->" in text and "Beste Liga ->" in text
    assert "uebersprungen" in text                         # Skip wird gemeldet
    report = json.loads(out.read_text())
    assert report["meta"]["bet_source"] == "B365"
    best_report = json.loads(best.read_text())
    assert "league" in best_report
