import json
import os
from datetime import datetime, timezone

import pytest

from arbfinder import cli, pinnacle
from arbfinder.fair_probability import PinnacleAnchorModel
from arbfinder.models import Event, Market
from arbfinder.pinnacle import PinnBet

_PINN = "tests/data/footballdata_pinnacle_sample.csv"
_DT = datetime(2023, 8, 12, 15, 0, tzinfo=timezone.utc)


def _pb(clv, *, won=True, season="2023/24", bookie="Max", odd=2.4):
    return PinnBet(commence_time=_DT, season=season, event_name="H v A", outcome="H",
                   bookie=bookie, odd=odd, fair_prob=0.5, won=won,
                   clv_pct=clv, pinnacle_close=2.0)


# --------------------------------------------------------------------------- #
# C1: CLV-Berechnung
# --------------------------------------------------------------------------- #
def test_clv_pro_wette_genommen_vs_pinnacle_schluss():
    ev = Event("e", "H", "A", _DT, result="H", markets=[Market("h2h", {
        "H": {"PS": 2.0, "Max": 2.4, "PSC": 2.0},        # CLV = 2.4/2.0 - 1 = +20%
        "D": {"PS": 3.6, "Max": 3.8, "PSC": 3.6},
        "A": {"PS": 4.0, "Max": 4.3, "PSC": 4.0},
    }, 3)])
    bets = pinnacle._build_bets([ev], PinnacleAnchorModel(anchor="open").estimate,
                                bet_source="Max", min_edge=2.0)
    h = next(b for b in bets if b.outcome == "H")
    assert abs(h.clv_pct - 20.0) < 1e-9
    assert h.pinnacle_close == 2.0 and h.won is True


# --------------------------------------------------------------------------- #
# C2: CLV-Statistik
# --------------------------------------------------------------------------- #
def test_clv_stats_anteil_mittel_median():
    s = pinnacle.clv_stats([_pb(10.0), _pb(-5.0), _pb(3.0)])
    assert s["n"] == 3
    assert s["share_positive_pct"] == round(2 / 3 * 100, 1)
    assert s["mean_clv_pct"] == round(8.0 / 3, 3)        # gerundet auf 3 Stellen
    assert s["median_clv_pct"] == 3.0
    assert sum(b["n"] for b in s["distribution_buckets"]) == 3


# --------------------------------------------------------------------------- #
# C+D+E1: Gesamtlauf -> JSON-Struktur
# --------------------------------------------------------------------------- #
def _has_no_nonfinite(obj) -> bool:
    if isinstance(obj, float):
        import math
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_has_no_nonfinite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_has_no_nonfinite(v) for v in obj)
    return True


def test_run_end_to_end_struktur_und_kein_nan():
    report, plotdata = pinnacle.run([_PINN], bet_source="Max", anchor="open")
    assert report["meta"]["n_events"] == 8 and report["meta"]["bet_source"] == "Max"
    for anchor in ("pinnacle_anchor", "consensus_anchor"):
        b = report["strategies"][anchor]
        assert {"clv", "pnl_flat", "pnl_kelly", "by_season", "by_bookie",
                "by_odds_bucket", "haircut_sensitivity"} <= set(b)
        assert len(b["haircut_sensitivity"]) == 4                  # 0/1/2/3 %
        assert "share_positive_pct" in b["clv"]
    assert report["verdict"]["primary_signal"] == "CLV"
    assert isinstance(report["verdict"]["survives"], bool)
    assert "sign_flip_fixed" in report["head_to_head"]
    # JSON valide + keine NaN/inf (null stattdessen):
    s = json.dumps(report)
    assert "NaN" not in s and "Infinity" not in s
    assert _has_no_nonfinite(report)
    assert {"pinnacle", "consensus"} <= set(plotdata)


def test_anchor_wahl_open_close_laeuft():
    ro, _ = pinnacle.run([_PINN], anchor="open")
    rc, _ = pinnacle.run([_PINN], anchor="close")
    assert ro["meta"]["anchor"] == "open" and rc["meta"]["anchor"] == "close"
    # open- und close-Anker MUESSEN unterschiedliche fair_probs -> andere Edges
    # -> andere Wettmengen/CLV liefern; sonst waere die Anker-Wahl wirkungslos.
    po, pc = ro["meta"]["n_bets_pinnacle"], rc["meta"]["n_bets_pinnacle"]
    clv_o = ro["strategies"]["pinnacle_anchor"]["clv"]["mean_clv_pct"]
    clv_c = rc["strategies"]["pinnacle_anchor"]["clv"]["mean_clv_pct"]
    assert (po != pc) or (clv_o != clv_c)


def test_verdict_warnt_bei_max_line_shopping():
    report, _ = pinnacle.run([_PINN], bet_source="Max")
    assert any("LINE-SHOPPING" in r or "Line-Shopping" in r for r in report["verdict"]["reasons"])
    assert any("Markt-Maximum" in c for c in report["verdict"]["caveats"])


# --------------------------------------------------------------------------- #
# Verdict-Logik: survives = clv_ok UND pnl_ok (gegen Schoenfaerberei)
# --------------------------------------------------------------------------- #
def _mkpinn(*, mean_clv, share, end_cap, roi, ruined=False, start=100.0):
    return {
        "clv": {"mean_clv_pct": mean_clv, "share_positive_pct": share, "n": 50},
        "pnl_flat": {"start": start, "end_capital": end_cap, "roi_turnover_pct": roi,
                     "max_drawdown_pct": 0.0, "ruin": {"ruined": ruined}},
    }


def test_verdict_survives_braucht_clv_UND_pnl():
    # CLV positiv, PnL aber Verlust -> survives MUSS False sein (kein AND-Bypass).
    v = pinnacle._verdict(_mkpinn(mean_clv=8.0, share=77.0, end_cap=80.0, roi=-5.0))
    assert v["clv_positive"] is True and v["pnl_secondary_ok"] is False
    assert v["survives"] is False
    # Beides positiv -> survives True.
    v2 = pinnacle._verdict(_mkpinn(mean_clv=3.0, share=60.0, end_cap=115.0, roi=4.0))
    assert v2["survives"] is True


def test_verdict_breakeven_zaehlt_nicht_als_pnl_bestaetigung():
    # Genau Break-even (Endkapital == Start, ROI 0) ist KEIN realisierter Edge.
    v = pinnacle._verdict(_mkpinn(mean_clv=5.0, share=70.0, end_cap=100.0, roi=0.0))
    assert v["pnl_secondary_ok"] is False and v["survives"] is False


def test_verdict_widerspruch_bei_clv_ohne_pnl():
    v = pinnacle._verdict(_mkpinn(mean_clv=8.0, share=77.0, end_cap=0.0, roi=-100.0, ruined=True))
    assert any("WIDERSPRUCH" in r for r in v["reasons"])


def test_verdict_b365_einzelquelle_ohne_line_shopping():
    v = pinnacle._verdict(_mkpinn(mean_clv=2.7, share=55.0, end_cap=88.0, roi=-2.0),
                          bet_source="B365")
    assert not any("LINE-SHOPPING" in r for r in v["reasons"])
    assert any("EINZELNE Quelle" in c for c in v["caveats"])
    assert not any("Markt-Maximum" in c for c in v["caveats"])


def test_b365_pfad_durch_run():
    report, _ = pinnacle.run([_PINN], bet_source="B365")
    assert not any("LINE-SHOPPING" in r for r in report["verdict"]["reasons"])
    assert "einzelne Quelle 'B365'" in pinnacle.summary_text(report)


# --------------------------------------------------------------------------- #
# sign_flip-Logik + Drei-Zustands-Diagnose
# --------------------------------------------------------------------------- #
def _season_rows(*rois):
    return [{"season": f"S{i}", "roi_pct": r} for i, r in enumerate(rois)]


def test_signflip_truthiness():
    assert pinnacle._signflip(_season_rows(5.0, -3.0)) is True
    assert pinnacle._signflip(_season_rows(5.0, 81.0)) is False
    assert pinnacle._signflip(_season_rows(-5.0, -3.0)) is False
    assert pinnacle._signflip(_season_rows()) is False
    assert pinnacle._signflip([{"season": "S", "roi_pct": None}]) is False


def test_head_to_head_drei_zustaende():
    # fixed: Konsens (2. Arg) wechselt, Pinnacle (1. Arg) nicht.
    fixed = pinnacle._head_to_head(_mk_block(2.0, 3.0), _mk_block(5.0, -3.0))
    assert fixed["sign_flip_state"] == "fixed" and fixed["sign_flip_fixed"] is True
    # Regression: Pinnacle wechselt, Konsens nicht.
    regress = pinnacle._head_to_head(_mk_block(2.0, -3.0), _mk_block(5.0, 3.0))
    assert regress["sign_flip_state"] == "pinnacle_introduced_flip"
    assert "REGRESSION" in regress["summary_text"] and regress["sign_flip_fixed"] is False
    # Praemisse nicht reproduziert: keiner wechselt.
    none = pinnacle._head_to_head(_mk_block(2.0, 3.0), _mk_block(-5.0, -3.0))
    assert none["sign_flip_state"] == "no_flip_to_fix" and none["premise_reproduced"] is False


def _mk_block(*rois):
    return {"by_season": _season_rows(*rois),
            "clv": {"mean_clv_pct": 1.0, "share_positive_pct": 50.0, "n": 10}}


# --------------------------------------------------------------------------- #
# by_season ist RUIN-UNABHAENGIG (kein truncation-Artefakt)
# --------------------------------------------------------------------------- #
def test_by_season_ruin_unabhaengig():
    # Bet 1 (Saison A) verliert bei flat_pct=100 -> Bankroll-Ruin. Trotzdem
    # MUSS by_season alle settled Wetten (auch Saison B danach) enthalten.
    a = PinnBet(commence_time=_DT, season="2022/23", event_name="x", outcome="H",
                bookie="Max", odd=2.0, fair_prob=0.6, won=False, clv_pct=1.0, pinnacle_close=1.9)
    later = datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc)
    b = PinnBet(commence_time=later, season="2023/24", event_name="y", outcome="H",
                bookie="Max", odd=2.0, fair_prob=0.6, won=True, clv_pct=2.0, pinnacle_close=1.9)
    block, _ = pinnacle._anchor_report([a, b], start_capital=100.0, flat_pct=100.0,
                                       kelly_fraction=0.25, kelly_cap=0.1, haircuts=(0.0,))
    assert block["pnl_flat"]["ruin"]["ruined"] is True       # Bankroll ruiniert
    seasons = {r["season"]: r for r in block["by_season"]}
    assert set(seasons) == {"2022/23", "2023/24"}            # Saison B NICHT verschluckt
    assert sum(r["n_bets"] for r in block["by_season"]) == 2  # alle settled, nicht truncated
    assert seasons["2023/24"]["roi_pct"] == 100.0            # Gewinner-Saison: +100 % flat-unit


# --------------------------------------------------------------------------- #
# Konsens leave-one-out + min_books
# --------------------------------------------------------------------------- #
def test_consensus_fair_fn_leave_one_out_und_min_books():
    fn = pinnacle._consensus_fair_fn("B365")
    odds = {"H": {"B365": 2.0, "BW": 2.1, "IW": 2.05},
            "D": {"B365": 3.6, "BW": 3.5, "IW": 3.55},
            "A": {"B365": 4.0, "BW": 4.1, "IW": 4.05}}
    fair = fn(odds)
    assert fair is not None and abs(sum(fair.values()) - 1.0) < 1e-9
    # Leave-one-out: B365 darf den Konsens NICHT mitbestimmen.
    fair_no_b365 = pinnacle._consensus_fair_fn("B365")(
        {o: {b: p for b, p in books.items() if b != "B365"} for o, books in odds.items()})
    assert abs(fair["H"] - fair_no_b365["H"]) < 1e-9
    # Pool zu klein (nur B365 vorhanden, der ausgeschlossen wird) -> None.
    assert fn({"H": {"B365": 2.0}, "D": {"B365": 3.6}, "A": {"B365": 4.0}}) is None


# --------------------------------------------------------------------------- #
# Ungesettelte Wetten: zaehlen fuer CLV, NICHT fuer PnL
# --------------------------------------------------------------------------- #
def test_unsettled_zaehlt_clv_nicht_pnl():
    ev = Event("e", "H", "A", _DT, result=None, markets=[Market("h2h", {
        "H": {"PS": 2.0, "Max": 2.4, "PSC": 2.0},
        "D": {"PS": 3.6, "Max": 3.8, "PSC": 3.6},
        "A": {"PS": 4.0, "Max": 4.3, "PSC": 4.0},
    }, 3)])
    bets = pinnacle._build_bets([ev], PinnacleAnchorModel(anchor="open").estimate,
                                bet_source="Max", min_edge=2.0)
    h = next(b for b in bets if b.outcome == "H")
    assert h.won is None and h.clv_pct is not None
    assert pinnacle.clv_stats(bets)["n"] == len([b for b in bets if b.clv_pct is not None])
    block, _ = pinnacle._anchor_report(bets, start_capital=100.0, flat_pct=1.0,
                                       kelly_fraction=0.25, kelly_cap=0.1, haircuts=(0.0,))
    assert block["pnl_flat"]["end_capital"] == 100.0         # nichts gesettelt -> kein PnL
    assert block["clv"]["n"] >= 1                            # CLV zaehlt trotzdem


def test_clv_stats_leer_gibt_none():
    s = pinnacle.clv_stats([])
    assert s == {"share_positive_pct": None, "mean_clv_pct": None,
                 "median_clv_pct": None, "n": 0, "distribution_buckets": []}
    s2 = pinnacle.clv_stats([_pb(None)])                     # clv_pct=None -> ignoriert
    assert s2["n"] == 0 and s2["mean_clv_pct"] is None


# --------------------------------------------------------------------------- #
# E2: Plots
# --------------------------------------------------------------------------- #
def test_make_plots_erzeugt_sechs_pngs(tmp_path):
    pytest.importorskip("matplotlib")
    _, plotdata = pinnacle.run([_PINN])
    paths = pinnacle.make_plots(plotdata, tmp_path)
    assert len(paths) == 6
    assert all(os.path.exists(p) and p.endswith(".png") for p in paths)


# --------------------------------------------------------------------------- #
# E3: CLI end-to-end
# --------------------------------------------------------------------------- #
def test_pinnacle_cli_schreibt_json_und_summary(tmp_path, capsys):
    out = tmp_path / "run.json"
    rc = cli.main(["pinnacle-run", "--csv", _PINN, "--out-json", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "JSON ->" in text and "CLV (primaer)" in text
    report = json.loads(out.read_text())
    assert "verdict" in report and "strategies" in report
