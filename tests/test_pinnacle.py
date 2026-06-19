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


def test_verdict_warnt_bei_max_line_shopping():
    report, _ = pinnacle.run([_PINN], bet_source="Max")
    assert any("LINE-SHOPPING" in r or "Line-Shopping" in r for r in report["verdict"]["reasons"])


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
