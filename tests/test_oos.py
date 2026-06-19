import json
import math
import os
from datetime import datetime, timezone

import pytest

from arbfinder import cli, oos
from arbfinder.models import Event, Market

_DIR = "tests/data/oos"


def _has_no_nonfinite(obj) -> bool:
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_has_no_nonfinite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_has_no_nonfinite(v) for v in obj)
    return True


def _ev(dt):
    return Event("e", "H", "A", dt, result="H", markets=[Market("h2h", {"H": {}}, 3)])


# --------------------------------------------------------------------------- #
# Saison-Split
# --------------------------------------------------------------------------- #
def test_split_by_season():
    train_2223 = _ev(datetime(2022, 8, 13, tzinfo=timezone.utc))   # 2022/23
    train_2324 = _ev(datetime(2024, 5, 1, tzinfo=timezone.utc))    # 2023/24 (Mai)
    hold = _ev(datetime(2024, 8, 13, tzinfo=timezone.utc))         # 2024/25
    other = _ev(datetime(2019, 8, 13, tzinfo=timezone.utc))        # ausserhalb -> ignoriert
    tr, ho = oos.split_by_season([train_2223, train_2324, hold, other])
    assert len(tr) == 2 and len(ho) == 1
    assert ho[0] is hold


# --------------------------------------------------------------------------- #
# Urteile: confirmed / parked-schwach / parked-zu-wenig / missing
# --------------------------------------------------------------------------- #
def test_confirmed_wenn_oos_positiv_und_genug():
    rep, _ = oos.run(_DIR, candidates=("TT", "NN"), uncertain=(), min_samples=2, min_oos=0.5)
    tt = rep["leagues"]["TT"]
    assert tt["verdict"]["status"] == "confirmed"
    assert tt["in_sample"]["mean_clv_pct"] > 0 and tt["out_of_sample"]["mean_clv_pct"] > 0
    assert tt["delta"]["out_stays_positive"] is True
    # NN: in-sample positiv, Holdout NEGATIV -> parked (nicht confirmed, nicht rejected)
    assert rep["leagues"]["NN"]["verdict"]["status"] == "parked"
    assert rep["leagues"]["NN"]["out_of_sample"]["mean_clv_pct"] < 0


def test_parked_wenn_holdout_zu_klein():
    # min_samples gross -> selbst positives OOS wird geparkt (zu duenn), NICHT confirmed.
    rep, _ = oos.run(_DIR, candidates=("TT",), uncertain=(), min_samples=100, min_oos=0.5)
    v = rep["leagues"]["TT"]["verdict"]
    assert v["status"] == "parked" and "zu wenig" in v["reason"].lower()


def test_missing_league_parked_nicht_rejected():
    rep, _ = oos.run(_DIR, candidates=("ZZ",), uncertain=())
    v = rep["leagues"]["ZZ"]["verdict"]
    assert v["status"] == "parked"                          # Datenproblem, KEIN Fehlsignal
    assert "download_data" in v["reason"]
    assert rep["leagues"]["ZZ"]["error"]


def _typeA_event(dt, result="H"):
    odds = {"Home": {"PS": 2.10, "PSC": 2.05, "B365": 2.30},
            "Draw": {"PS": 3.50, "PSC": 3.55, "B365": 3.00},
            "Away": {"PS": 3.80, "PSC": 3.90, "B365": 3.20}}
    return Event("e", "Home", "Away", dt, result=result, markets=[Market("h2h", odds, 3)])


def test_present_aber_keine_insample_clv_wird_geparkt_nicht_rejected():
    # Liga NUR im Holdout vorhanden -> kein in-sample CLV -> DATENLUECKE, MUSS parken
    # (nicht 'rejected' mit erfundenem in_sample_edge=0.0).
    holdout_only = [_typeA_event(datetime(2024, 8, 10 + i, tzinfo=timezone.utc)) for i in range(5)]
    block = oos.evaluate_league(
        "XX", holdout_only, uncertain=False, bet_source="B365", min_edge=2.0,
        odds_min=2.0, odds_max=4.0, n_trials=17, min_oos=0.5, min_samples=2)
    assert block["in_sample"]["n_with_clv"] == 0
    assert block["out_of_sample"]["mean_clv_pct"] > 0       # OOS waere stark...
    assert block["verdict"]["status"] == "parked"          # ...aber Datenluecke -> parken
    assert block["verdict"]["in_sample_edge"] is None      # KEIN erfundenes 0.0
    assert "in-sample" in block["verdict"]["reason"]


def test_min_samples_gate_nutzt_n_with_clv_nicht_n_bets():
    # MM-Holdout: 4 Wetten, aber nur 2 mit CLV (2 Zeilen ohne PSC). Der OOS-Gate
    # MUSS auf n_with_clv (=2) schauen, NICHT auf n_bets (=4).
    rep3, _ = oos.run(_DIR, candidates=("MM",), uncertain=(), min_samples=3, min_oos=0.5)
    o = rep3["leagues"]["MM"]["out_of_sample"]
    assert o["n_bets"] == 4 and o["n_with_clv"] == 2        # n_bets > n_with_clv
    # min_samples=3: n_with_clv(2) < 3 -> parked. (Mit n_bets=4 waere es faelschlich confirmed.)
    assert rep3["leagues"]["MM"]["verdict"]["status"] == "parked"
    # min_samples=2: n_with_clv(2) >= 2 und OOS>0.5 -> confirmed.
    rep2, _ = oos.run(_DIR, candidates=("MM",), uncertain=(), min_samples=2, min_oos=0.5)
    assert rep2["leagues"]["MM"]["verdict"]["status"] == "confirmed"


def test_parked_bei_schwachem_aber_positivem_oos():
    # Holdout positiv (+9.36 %), aber unter min_oos=20 -> parked 'oos schwach'
    # (zu unterscheiden vom NEGATIV-Holdout-Fall).
    rep, _ = oos.run(_DIR, candidates=("TT",), uncertain=(), min_samples=2, min_oos=20.0)
    v = rep["leagues"]["TT"]["verdict"]
    assert v["status"] == "parked" and "schwach" in v["reason"]
    assert rep["leagues"]["TT"]["delta"]["out_stays_positive"] is True


def test_uncertain_wird_markiert():
    rep, _ = oos.run(_DIR, candidates=("TT",), uncertain=("NN",), min_samples=2)
    assert rep["leagues"]["NN"]["uncertain"] is True
    assert rep["leagues"]["TT"]["uncertain"] is False
    assert rep["meta"]["uncertain"] == ["NN"]


# --------------------------------------------------------------------------- #
# Struktur, JSON ohne NaN, Summary
# --------------------------------------------------------------------------- #
def test_report_struktur_und_kein_nan():
    rep, plotdata = oos.run(_DIR, candidates=("TT", "NN"), uncertain=(), min_samples=2)
    m = rep["meta"]
    assert m["bet_source"] == "B365" and m["clv_benchmark"] == "pinnacle_close_devigged"
    assert m["odds_filter"] == [2.0, 4.0]
    assert m["train_seasons"] == ["2020/21", "2021/22", "2022/23", "2023/24"]
    assert m["holdout_season"] == "2024/25"
    for lg in ("TT", "NN"):
        b = rep["leagues"][lg]
        assert {"in_sample", "out_of_sample", "delta", "verdict"} <= set(b)
    s = json.dumps(rep)
    assert "NaN" not in s and "Infinity" not in s and _has_no_nonfinite(rep)
    assert set(plotdata) == {"by_league"}


def test_summary_report_nur_urteile():
    rep, _ = oos.run(_DIR, candidates=("TT", "NN"), uncertain=(), min_samples=2)
    summ = oos.summary_report(rep)
    assert summ["any_confirmed"] is True                    # TT confirmed
    assert summ["counts"]["confirmed"] == 1 and summ["counts"]["parked"] == 1
    assert set(summ["verdicts"]) == {"TT", "NN"}
    assert summ["verdicts"]["TT"]["out_of_sample_n"] == 3
    json.dumps(summ)


def test_summary_text_sagt_klar_wenn_keine_confirmed():
    # min_samples gross -> nichts confirmed -> Text muss das KLAR sagen.
    rep, _ = oos.run(_DIR, candidates=("TT", "NN"), uncertain=(), min_samples=100)
    txt = oos.summary_text(rep)
    assert "KEINE Liga" in txt


# --------------------------------------------------------------------------- #
# Plots + CLI
# --------------------------------------------------------------------------- #
def test_make_plots(tmp_path):
    pytest.importorskip("matplotlib")
    rep, plotdata = oos.run(_DIR, candidates=("TT", "NN"), uncertain=(), min_samples=2)
    paths = oos.make_plots(rep, plotdata, tmp_path)
    assert any("oos_overview" in p for p in paths)
    assert all(os.path.exists(p) and p.endswith(".png") for p in paths)


def test_cli_oos_test(tmp_path, capsys):
    out = tmp_path / "oos.json"
    summ = tmp_path / "summary.json"
    # --candidates erlaubt es, die CLI auf die Beispieldaten zu richten -> echter Lauf
    # mit echtem Urteil (nicht nur der all-missing-Pfad).
    rc = cli.main(["oos-test", "--csv-dir", _DIR, "--candidates", "TT", "NN", "--uncertain",
                   "--out-json", str(out), "--summary-json", str(summ), "--min-samples", "2"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "JSON ->" in text and "Urteile ->" in text
    report = json.loads(out.read_text())
    summary = json.loads(summ.read_text())
    assert "leagues" in report and "verdicts" in summary
    assert summary["verdicts"]["TT"]["status"] == "confirmed"   # echtes berechnetes Urteil
    assert set(summary["verdicts"]) == {"TT", "NN"}
