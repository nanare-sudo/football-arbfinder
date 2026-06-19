"""Härtungs-Tests aus dem Value-Betting-Review (Korrektheit & Abdeckung)."""
import math

import pytest

from arbfinder import backtest
from arbfinder.fair_probability import ConsensusDevigModel
from arbfinder.providers.base import coerce_float
from arbfinder.strategies import get
from arbfinder.strategies.value import ValueStrategy


def _snap(odds, expected=2, **kw):
    return {"event_id": "x", "event_name": "T", "market": "h2h",
            "expected_outcomes": expected, "odds": odds, **kw}


# --------------------------------------------------------------------------- #
# Nicht-endliche Quoten (NaN/inf) duerfen nie ein Signal erzeugen
# --------------------------------------------------------------------------- #
def test_coerce_float_verwirft_nan_inf():
    assert coerce_float("nan") is None
    assert coerce_float(float("inf")) is None
    assert coerce_float(float("-inf")) is None
    assert coerce_float("2.0") == 2.0


def test_estimate_ignoriert_nan_bookie_statt_nan_verteilung():
    m = ConsensusDevigModel()
    fair = m.estimate({"A": {"b1": float("nan"), "b2": 2.0}, "B": {"b1": 2.0, "b2": 2.0}})
    assert fair == {"A": 0.5, "B": 0.5}                      # b1 (NaN) ignoriert
    assert all(math.isfinite(v) for v in fair.values())


def test_value_kein_signal_bei_nan_inf_quoten():
    nan_markt = {"A": {"b1": float("nan"), "b2": 2.0}, "B": {"b1": 2.0, "b2": 2.0}}
    inf_markt = {"A": {"b1": float("inf"), "b2": 2.0}, "B": {"b1": 2.0, "b2": 2.0}}
    for odds in (nan_markt, inf_markt):
        sigs = get("value").evaluate(_snap(odds, expected=2))
        assert all(math.isfinite(s.edge_pct) for s in sigs)  # nie ein NaN-Edge-Signal


# --------------------------------------------------------------------------- #
# Leave-one-out Gleichstand: beurteilter Preis nie im Konsens (reihenfolge-stabil)
# --------------------------------------------------------------------------- #
def test_tie_break_ist_reihenfolge_unabhaengig():
    o1 = {"home": {"B": 2.10, "A": 2.10, "C": 1.90}, "away": {"A": 1.80, "C": 2.00}}
    o2 = {"home": {"A": 2.10, "B": 2.10, "C": 1.90}, "away": {"A": 1.80, "C": 2.00}}
    edge = lambda o: [s.edge_pct for s in get("value").evaluate(_snap(o, expected=2))
                      if s.meta["outcome"] == "home"]
    assert edge(o1) == edge(o2) and edge(o1)                 # gleich, und es feuert


# --------------------------------------------------------------------------- #
# ConsensusDevigModel: min_books-Gate und Konstruktor-Schutz
# --------------------------------------------------------------------------- #
def test_min_books_zwei_verlangt_zwei_komplette_bookies():
    m = ConsensusDevigModel(min_books=2)
    assert m.estimate({"A": {"X": 2.0}, "B": {"X": 2.0}}) is None          # nur 1 -> None
    assert m.estimate({"A": {"X": 2.0, "Y": 2.0}, "B": {"X": 2.0, "Y": 2.0}}) is not None


def test_min_books_kleiner_eins_wirft():
    with pytest.raises(ValueError):
        ConsensusDevigModel(min_books=0)


# --------------------------------------------------------------------------- #
# Mindest-Edge-Grenze (edge == Schwelle MUSS feuern) und Multiplizitaet
# --------------------------------------------------------------------------- #
def test_min_edge_grenze_ist_inklusiv():
    # A: best 2.5, Konsens (B1,B2) fair 0.5 -> edge = 2.5*0.5-1 = exakt 25.0
    odds = {"A": {"B1": 2.0, "B2": 2.0, "JUICY": 2.5}, "B": {"B1": 2.0, "B2": 2.0, "JUICY": 1.7}}
    fires = ValueStrategy(min_edge_pct=25.0).evaluate(_snap(odds, expected=2))
    assert any(s.meta["outcome"] == "A" for s in fires)            # 25.0 < 25.0 ist False -> feuert
    above = ValueStrategy(min_edge_pct=25.0001).evaluate(_snap(odds, expected=2))
    assert all(s.meta["outcome"] != "A" for s in above)           # knapp drueber -> nicht


def test_value_kann_mehrere_signale_je_markt_liefern():
    odds = {"A": {"B1": 2.0, "B2": 2.0, "B3": 2.4}, "B": {"B1": 2.0, "B2": 2.0, "B3": 2.4}}
    sigs = get("value").evaluate(_snap(odds, expected=2))
    assert {s.meta["outcome"] for s in sigs} == {"A", "B"}        # beide Seiten Value


# --------------------------------------------------------------------------- #
# Backtest-Integration: run('value') rechnet PnL fuer Gewinn UND Verlust
# --------------------------------------------------------------------------- #
def test_run_value_realized_pnl_gewinn_und_verlust(tmp_path):
    # Nur Ausgang A ist Value (best 2.6); B ist bei allen gleich -> kein B-Signal.
    loss = ('{"event_id":"L","market":"h2h","expected_outcomes":2,'
            '"odds":{"A":{"B1":2.0,"B2":2.0,"B3":2.6},"B":{"B1":1.9,"B2":1.9,"B3":1.9}},'
            '"result":"B"}')          # auf A gesetzt, B gewinnt -> Verlust -100
    win = ('{"event_id":"W","market":"h2h","expected_outcomes":2,'
           '"odds":{"A":{"B1":2.0,"B2":2.0,"B3":2.6},"B":{"B1":1.9,"B2":1.9,"B3":1.9}},'
           '"result":"A"}')           # auf A gesetzt, A gewinnt -> +100*(2.6-1)=+160
    p = tmp_path / "value_pnl.jsonl"
    p.write_text(loss + "\n" + win + "\n", encoding="utf-8")

    res = backtest.run("value", str(p))
    assert res.signals == 2 and res.n_with_result == 2
    assert res.realized_pnl == 60.0          # -100 (Verlust, kein Hedge!) + 160 (Gewinn)


# --------------------------------------------------------------------------- #
# Validierungs-Wiring: Deflation informativ; zu wenige Samples -> parked
# --------------------------------------------------------------------------- #
def _vresult(edge):
    return backtest.BacktestResult("value", 10, 3, edge, 0, None, 0)


def test_make_verdict_value_deflation_informativ():
    v = backtest.make_verdict("value", _vresult(4.0), n_trials=10)
    assert v.deflated_edge is not None and v.deflated_edge < v.in_sample_edge  # mild geschrumpft
    # Deflation ist NIE allein der Grund: Urteil haengt an OOS (hier None) -> parked.
    assert v.status == "parked"


def test_make_verdict_value_oos_aber_zu_wenig_samples_wird_geparkt():
    v = backtest.make_verdict("value", _vresult(4.0),
                              out_of_sample_edge=3.0, min_samples=100, n_samples=50)
    assert v.status == "parked"          # positiver OOS, aber zu duenn belegt -> nicht confirmed
