import pytest

from arbfinder.fair_probability import (
    ConsensusDevigModel,
    FairProbabilityModel,
    PinnacleAnchorModel,
)


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
    # min_books=1, weil hier bewusst die Ein-Bookie-Devig-Mathematik geprueft wird.
    m = ConsensusDevigModel(min_books=1)
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


# --------------------------------------------------------------------------- #
# PinnacleAnchorModel (scharfer Einzel-Anker)
# --------------------------------------------------------------------------- #
def test_pinnacle_devig_summe_eins_und_vig_raus():
    m = PinnacleAnchorModel(anchor="open")
    # Pinnacle-Eroeffnung (PS) je Ausgang; B365/Max werden ignoriert.
    odds = {
        "H": {"PS": 2.0, "B365": 2.2, "Max": 2.3, "PSC": 1.9},
        "D": {"PS": 3.5, "B365": 3.6, "Max": 3.8, "PSC": 3.4},
        "A": {"PS": 4.0, "B365": 4.1, "Max": 4.3, "PSC": 3.9},
    }
    fair = m.estimate(odds)
    assert abs(sum(fair.values()) - 1.0) < 1e-9
    # implied 0.5 / 0.2857 / 0.25 = sum 1.0357 -> H ~ 0.4828
    assert abs(fair["H"] - (0.5 / (0.5 + 1 / 3.5 + 0.25))) < 1e-9


def test_pinnacle_anchor_open_vs_close():
    odds = {"H": {"PS": 2.0, "PSC": 1.5}, "A": {"PS": 2.0, "PSC": 3.0}}
    fo = PinnacleAnchorModel(anchor="open").estimate(odds)
    fc = PinnacleAnchorModel(anchor="close").estimate(odds)
    assert abs(fo["H"] - 0.5) < 1e-9                      # open: 2.0/2.0 -> 0.5/0.5
    assert fc["H"] > fo["H"]                              # close: 1.5 favorit -> hoehere Wkt H
    assert abs(sum(fc.values()) - 1.0) < 1e-9


def test_pinnacle_unvollstaendiger_anker_gibt_none():
    odds = {"H": {"PS": 2.0}, "A": {"B365": 2.0}}         # A hat keine PS-Quote
    assert PinnacleAnchorModel(anchor="open").estimate(odds) is None


def test_pinnacle_ungueltiger_anchor_param():
    with pytest.raises(ValueError):
        PinnacleAnchorModel(anchor="middle")
