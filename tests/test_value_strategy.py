from arbfinder.fair_probability import ConsensusDevigModel
from arbfinder.strategies import all_strategies, get
from arbfinder.strategies.value import ValueStrategy


def _snap(odds, expected=2, market="h2h", **kw):
    return {
        "event_id": "x", "event_name": "T", "market": market,
        "expected_outcomes": expected, "odds": odds, **kw,
    }


def test_value_ist_registriert_und_validierungspflichtig():
    assert "value" in all_strategies()
    assert get("value").requires_validation is True


def test_value_findet_konstruierten_fall():
    # B3 bietet auf A eine deutlich grosszuegigere Quote als der Konsens (B1,B2).
    odds = {"A": {"B1": 2.0, "B2": 2.0, "B3": 2.3}, "B": {"B1": 2.0, "B2": 2.0, "B3": 1.8}}
    sigs = get("value").evaluate(_snap(odds, expected=2))
    assert all(s.kind == "value" for s in sigs)
    a = [s for s in sigs if s.meta["outcome"] == "A"]
    assert a and a[0].edge_pct > 10            # 2.3 * 0.5 - 1 = 15%
    assert a[0].stakes == {"A": 100.0}         # EIN Ausgang, kein Hedge


def test_value_ignoriert_fairen_markt():
    # Alle Bookies einig, kein Vig -> keine Quote schlaegt den Konsens.
    odds = {"A": {"B1": 2.0, "B2": 2.0, "B3": 2.0}, "B": {"B1": 2.0, "B2": 2.0, "B3": 2.0}}
    assert get("value").evaluate(_snap(odds, expected=2)) == []


def test_value_einbookie_ausgang_erzeugt_kein_signal():
    # min_books=1 isoliert den Fall: 'B' hat nur einen Bookie -> kein Konsens fuer
    # B, waehrend A (2 Bookies) feuert. (Der Default min_books=2 wuerde hier — nur
    # ein vollstaendiger Bookie nach Leave-one-out — gar nichts melden.)
    odds = {"A": {"B1": 1.9, "B2": 2.0}, "B": {"B1": 3.0}}
    strat = ValueStrategy(model=ConsensusDevigModel(min_books=1))
    sigs = strat.evaluate(_snap(odds, expected=2))
    assert all(s.meta["outcome"] != "B" for s in sigs)   # B selektiv verworfen
    assert any(s.meta["outcome"] == "A" for s in sigs)    # ... A feuert weiterhin (nicht alles weg)


def test_value_default_min_books_zwei_verwirft_duenne_2bookie_signale():
    # Mit dem Default (min_books=2) liefert ein 2-Bookie-Markt KEIN Value-Signal:
    # nach Leave-one-out bliebe nur ein einziger "Konsens"-Bookie.
    odds = {"A": {"B1": 2.0, "B2": 2.3}, "B": {"B1": 2.0, "B2": 1.8}}
    assert get("value").evaluate(_snap(odds, expected=2)) == []


def test_value_respektiert_vollstaendigkeit():
    assert get("value").evaluate(_snap({"A": {"B1": 2.0}}, expected=2)) == []  # nur 1 Ausgang
    voll_aber_fehlend = {"A": {"B1": 2.0, "B2": 2.0}, "B": {"B1": 2.0, "B2": 2.0}}
    assert get("value").evaluate(_snap(voll_aber_fehlend, expected=3)) == []   # Ausgang fehlt
    # Gegenprobe: vollstaendiger 3-Wege-Markt mit Value FEUERT (nicht "immer leer").
    voll_und_value = {
        "A": {"B1": 2.0, "B2": 2.0, "B3": 2.8},
        "B": {"B1": 4.0, "B2": 4.0, "B3": 3.5},
        "C": {"B1": 4.0, "B2": 4.0, "B3": 3.5},
    }
    sigs = get("value").evaluate(_snap(voll_und_value, expected=3))
    assert any(s.meta["outcome"] == "A" for s in sigs)


def test_value_end_to_end_ueber_fixture():
    # Provider -> normalize -> value: die konstruierten Value-Faelle tauchen auf
    # (mit normalisierten Namen), und alles ist kind="value".
    from arbfinder.detector import detect
    from arbfinder.providers import MockProvider

    res = detect(MockProvider("fixtures/recorded_odds.jsonl"), strategy_name="value")
    found = {(s.event_name, s.meta["outcome"]) for s in res.signals}
    assert ("Bayern Munich v Dortmund", "Bayern Munich") in found   # v1: Value auf Bayern
    assert ("Nadal v Federer", "Federer") in found                  # v2: Value auf Federer
    assert all(s.kind == "value" for s in res.signals)


def test_value_edge_formel():
    # Kontrolliert: A best=2.4, Konsens (B1,B2) fair A=0.5 -> edge = 2.4*0.5-1 = 20%
    odds = {"A": {"B1": 2.0, "B2": 2.0, "JUICY": 2.4}, "B": {"B1": 2.0, "B2": 2.0, "JUICY": 1.7}}
    sigs = get("value").evaluate(_snap(odds, expected=2))
    a = next(s for s in sigs if s.meta["outcome"] == "A")
    assert abs(a.edge_pct - 20.0) < 1e-6
    assert a.meta["best"] == ["JUICY", 2.4]
    assert abs(a.meta["fair_prob"] - 0.5) < 1e-6


# --------------------------------------------------------------------------- #
# PinnacleValueStrategy (scharfer Pinnacle-Anker, Bet auf Max-Eroeffnung)
# --------------------------------------------------------------------------- #
def _pinn_snap(odds, expected=3):
    return {"event_id": "p", "event_name": "H v A", "market": "h2h",
            "expected_outcomes": expected, "odds": odds}


_PINN_ODDS = {
    "H": {"PS": 2.0, "Max": 2.4, "PSC": 2.2},
    "D": {"PS": 3.6, "Max": 3.8, "PSC": 3.5},
    "A": {"PS": 4.0, "Max": 4.3, "PSC": 3.8},
}


def test_pinnacle_value_registriert_und_validierungspflichtig():
    assert "pinnacle_value" in all_strategies()
    assert get("pinnacle_value").requires_validation is True


def test_pinnacle_value_feuert_auf_max_vs_pinnacle():
    from arbfinder.strategies.value import PinnacleValueStrategy
    sigs = PinnacleValueStrategy(bet_source="Max", anchor="open", min_edge_pct=2.0).evaluate(_pinn_snap(_PINN_ODDS))
    h = next(s for s in sigs if s.meta["outcome"] == "H")
    assert h.edge_pct > 10                                # Max 2.4 vs faire ~0.486 -> ~16.8%
    assert h.meta["best"] == ["Max", 2.4]
    assert h.meta["clv_close"] == 2.2                     # Pinnacle-Schluss fuer CLV
    assert h.kind == "value"


def test_pinnacle_value_anker_open_vs_close_aendert_edge():
    from arbfinder.strategies.value import PinnacleValueStrategy
    eo = next(s for s in PinnacleValueStrategy(bet_source="Max", anchor="open").evaluate(_pinn_snap(_PINN_ODDS))
              if s.meta["outcome"] == "H").edge_pct
    ec = next(s for s in PinnacleValueStrategy(bet_source="Max", anchor="close").evaluate(_pinn_snap(_PINN_ODDS))
              if s.meta["outcome"] == "H").edge_pct
    assert abs(eo - ec) > 1e-6                            # Anker-Wahl wirkt sich aus


def test_pinnacle_value_kein_signal_wenn_bet_gleich_pinnacle():
    from arbfinder.strategies.value import PinnacleValueStrategy
    fair_market = {"H": {"PS": 2.0, "Max": 2.0, "PSC": 2.0},
                   "D": {"PS": 3.6, "Max": 3.6, "PSC": 3.6},
                   "A": {"PS": 4.0, "Max": 4.0, "PSC": 4.0}}
    sigs = PinnacleValueStrategy(bet_source="Max", min_edge_pct=2.0).evaluate(_pinn_snap(fair_market))
    assert all(s.meta["outcome"] != "H" for s in sigs)   # H: Max=PS -> nur Vig, kein Value
