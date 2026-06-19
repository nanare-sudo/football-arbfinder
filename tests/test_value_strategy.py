from arbfinder.strategies import all_strategies, get


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
    # 'B' hat nur einen Bookie -> kein unabhaengiger Konsens fuer B -> kein B-Signal.
    odds = {"A": {"B1": 1.9, "B2": 2.0}, "B": {"B1": 3.0}}
    sigs = get("value").evaluate(_snap(odds, expected=2))
    assert all(s.meta["outcome"] != "B" for s in sigs)


def test_value_respektiert_vollstaendigkeit():
    assert get("value").evaluate(_snap({"A": {"B1": 2.0}}, expected=2)) == []  # nur 1 Ausgang
    voll_aber_fehlend = {"A": {"B1": 2.0, "B2": 2.0}, "B": {"B1": 2.0, "B2": 2.0}}
    assert get("value").evaluate(_snap(voll_aber_fehlend, expected=3)) == []   # Ausgang fehlt


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
