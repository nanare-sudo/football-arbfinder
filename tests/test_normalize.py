from datetime import datetime, timedelta, timezone

from arbfinder.models import Event, Market
from arbfinder.normalize import (
    ALIASES,
    _canonicalize,
    _clean,
    canonical_team,
    default_gazetteer,
    event_identity,
    merge_events,
    normalize_event,
    same_event,
)


def _ev(home, away, when, *, market="h2h", odds=None, exp=3, result=None, ts=None, eid="x"):
    return Event(
        event_id=eid,
        home=home,
        away=away,
        start_time=when,
        markets=[Market(market, odds or {}, exp)],
        result=result,
        snapshot_ts=ts,
    )


# --------------------------------------------------------------------------- #
# Pflicht-Faelle aus CLAUDE.md
# --------------------------------------------------------------------------- #
def test_man_city_gleich_manchester_city():
    assert canonical_team("Man City") == canonical_team("Manchester City") == "Manchester City"


def test_zwei_gleiche_teams_verschiedene_tage_werden_NICHT_zusammengefuehrt():
    t1 = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)  # anderer Tag
    e1 = _ev("Man City", "Arsenal", t1, eid="a", odds={"Man City": {"K": 2.0}})
    e2 = _ev("Manchester City", "Arsenal", t2, eid="b", odds={"Manchester City": {"K": 2.0}})
    # gleiche Identitaet? NEIN.
    assert event_identity(e1) != event_identity(e2)
    assert same_event(e1, e2) is False
    merged = merge_events([e1, e2])
    assert len(merged) == 2  # bleiben getrennt


# --------------------------------------------------------------------------- #
# Stufen einzeln
# --------------------------------------------------------------------------- #
def test_stufe2_entfernt_club_suffix():
    assert canonical_team("Arsenal FC") == "Arsenal"
    assert canonical_team("Arsenal") == "Arsenal"
    assert canonical_team("Arsenal FC") == canonical_team("Arsenal")


def test_stufe2_diakritika_und_satzzeichen():
    assert _clean("Atlético  Madrid!") == "atletico madrid"
    assert canonical_team("Bayern") == "Bayern Munich"  # via Alias


def test_stufe3_fuzzy_faengt_tippfehler():
    # "Manchestr City" ist in keinem Alias, aber nah genug am Gazetteer-Namen.
    assert canonical_team("Manchestr City") == "Manchester City"


def test_stufe3_aus_wenn_gazetteer_leer():
    # Mit leerem known kein Fuzzy -> Tippfehler bleibt (nur Stufe 1/2).
    assert canonical_team("Manchestr City", known=[]) == "Manchestr City"


def test_kein_falsch_positiver_fuzzy_treffer():
    # "United" ist mehrdeutig -> darf NICHT auf "Manchester United" gezogen werden.
    assert canonical_team("United") == "United"


def test_alias_werte_sind_kanonisch_invariant():
    # Garantiert, dass Stufe 1 und Stufe 2 auf denselben String konvergieren.
    for value in ALIASES.values():
        assert _canonicalize(_clean(value)) == value, value


# --------------------------------------------------------------------------- #
# Identitaet / Merge
# --------------------------------------------------------------------------- #
def test_reihenfolge_der_teams_egal_fuer_identitaet():
    t = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    assert event_identity(_ev("Man City", "Arsenal", t)) == event_identity(_ev("Arsenal", "Man City", t))


def test_minuten_drift_innerhalb_toleranz_wird_zusammengefuehrt():
    t1 = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=5)  # selbe Anstosszeit, leicht abweichend gemeldet
    e1 = _ev("Man City", "Arsenal", t1, eid="p1",
             odds={"Manchester City": {"BookieA": 2.05}, "Draw": {"BookieA": 3.6}})
    e2 = _ev("Manchester City", "Arsenal", t2, eid="p2",
             odds={"Manchester City": {"BookieB": 2.12}, "Arsenal": {"BookieB": 4.1}})
    merged = merge_events([e1, e2])
    assert len(merged) == 1
    m = merged[0].get_market("h2h")
    # Quoten beider Anbieter unter EINEM kanonischen Ausgang vereinigt:
    assert m.odds["Manchester City"] == {"BookieA": 2.05, "BookieB": 2.12}
    assert set(m.odds) == {"Manchester City", "Draw", "Arsenal"}


def test_merge_haelt_markttypen_getrennt():
    t = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    e1 = _ev("Man City", "Arsenal", t, market="h2h",
             odds={"Man City": {"A": 2.0}}, exp=3)
    e2 = _ev("Man City", "Arsenal", t, market="totals",
             odds={"Over": {"A": 1.9}, "Under": {"A": 1.95}}, exp=2)
    merged = merge_events([e1, e2])
    assert len(merged) == 1
    assert sorted(merged[0].market_types) == ["h2h", "totals"]  # NIE vermischt


def test_normalize_event_vereinheitlicht_ausgaenge_und_result():
    t = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    e = _ev("Man City", "Arsenal", t,
            odds={"Man City": {"A": 2.1}, "Draw": {"A": 3.6}, "Arsenal": {"A": 4.0}},
            result="Man City")
    n = normalize_event(e)
    assert n.home == "Manchester City"
    assert set(n.markets[0].odds) == {"Manchester City", "Draw", "Arsenal"}
    assert n.result == "Manchester City"  # result mitnormalisiert (fuer PnL)


def test_default_gazetteer_enthaelt_aliaswerte():
    gaz = default_gazetteer()
    assert "Manchester City" in gaz and "Tottenham Hotspur" in gaz
