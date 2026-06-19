import pytest

from arbfinder.fair_probability import ConsensusDevigModel, FairProbabilityModel


def test_abstrakt_nicht_instanziierbar():
    with pytest.raises(TypeError):
        FairProbabilityModel()  # type: ignore[abstract]


def test_devig_summe_ist_eins():
    m = ConsensusDevigModel()
    odds = {"A": {"B1": 2.0, "B2": 2.05}, "B": {"B1": 2.0, "B2": 1.98}}
    fair = m.estimate(odds)
    assert fair is not None
    assert abs(sum(fair.values()) - 1.0) < 1e-9


def test_devig_rechnet_vig_heraus():
    # Ein Bookie mit Vig: implizite Summe > 1, fair-Summe == 1, Verhaeltnis bleibt.
    m = ConsensusDevigModel()
    odds = {"A": {"X": 1.5}, "B": {"X": 2.5}}     # implied .6667 + .4 = 1.0667
    fair = m.estimate(odds)
    assert fair is not None
    assert abs(fair["A"] - 0.625) < 1e-6          # .6667 / 1.0667
    assert abs(fair["B"] - 0.375) < 1e-6


def test_leave_one_out_schliesst_eigene_quote_aus():
    m = ConsensusDevigModel()
    odds = {
        "A": {"B1": 2.0, "B2": 2.0, "WILD": 10.0},
        "B": {"B1": 2.0, "B2": 2.0, "WILD": 1.05},
    }
    excl = m.estimate(odds, exclude_bookie="WILD")
    only = m.estimate({"A": {"B1": 2.0, "B2": 2.0}, "B": {"B1": 2.0, "B2": 2.0}})
    assert excl == only                            # WILD haengt NICHT mehr drin
    assert m.estimate(odds) != excl                # mit WILD waere das Ergebnis anders


def test_kein_unabhaengiger_konsens_ohne_zweiten_kompletten_bookie():
    m = ConsensusDevigModel()
    # X komplett (quotet A und B), Y unvollstaendig (nur A). X ausschliessen ->
    # kein vollstaendiger Bookie mehr -> kein Konsens.
    odds = {"A": {"X": 2.0, "Y": 2.1}, "B": {"X": 2.0}}
    assert m.estimate(odds, exclude_bookie="X") is None


def test_unter_zwei_ausgaengen_kein_konsens():
    m = ConsensusDevigModel()
    assert m.estimate({"A": {"X": 2.0}}) is None
