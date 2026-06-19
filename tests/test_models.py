from datetime import datetime, timezone

import pytest

from arbfinder.models import Event, Market


def _dt(s: str = "2026-08-15T15:00:00+00:00") -> datetime:
    return datetime.fromisoformat(s)


def test_event_braucht_start_time_als_datetime():
    with pytest.raises(TypeError):
        Event(event_id="e1", home="A", away="B", start_time="2026-08-15")  # type: ignore[arg-type]


def test_event_braucht_nichtleere_teams():
    with pytest.raises(ValueError):
        Event(event_id="e1", home="", away="B", start_time=_dt())


def test_event_name():
    ev = Event(event_id="e1", home="Man City", away="Arsenal", start_time=_dt())
    assert ev.name == "Man City v Arsenal"


def test_market_vollstaendigkeit():
    voll = Market("h2h", {"H": {"A": 2.0}, "X": {"A": 3.4}, "Aw": {"A": 3.6}}, expected_outcomes=3)
    teil = Market("h2h", {"H": {"A": 2.0}, "Aw": {"A": 3.6}}, expected_outcomes=3)
    assert voll.is_complete is True
    assert teil.is_complete is False
    assert teil.present_outcomes == 2


def test_market_unbekannte_erwartung_gilt_nicht_als_unvollstaendig():
    m = Market("h2h", {"H": {"A": 2.0}}, expected_outcomes=0)
    assert m.is_complete is True  # kann nicht als incomplete bewiesen werden


def test_best_per_outcome_nimmt_hoechste_quote_und_ignoriert_muell():
    m = Market(
        "h2h",
        {"H": {"A": 2.05, "B": 2.12}, "Aw": {"A": 0.0, "B": 3.6}},
        expected_outcomes=2,
    )
    best = m.best_per_outcome()
    assert best["H"] == ("B", 2.12)
    assert best["Aw"] == ("B", 3.6)  # ungueltige 0.0-Quote ignoriert


def test_to_snapshots_format_passt_zur_strategy():
    ev = Event(
        event_id="e1",
        home="Man City",
        away="Arsenal",
        start_time=_dt(),
        snapshot_ts=datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc),
        result="Man City",
        markets=[
            Market("h2h", {"Man City": {"A": 2.1}, "Draw": {"A": 3.6}, "Arsenal": {"A": 4.0}}, 3),
            Market("totals", {"Over": {"A": 1.9}, "Under": {"A": 1.95}}, 2),
        ],
    )
    snaps = ev.to_snapshots()
    assert len(snaps) == 2  # ein Snapshot je Markt -> Markttypen getrennt
    s0 = snaps[0]
    # exakt die Schluessel, die Strategy.evaluate / backtest erwarten
    assert s0["event_id"] == "e1"
    assert s0["event_name"] == "Man City v Arsenal"
    assert s0["market"] == "h2h"
    assert s0["expected_outcomes"] == 3
    assert s0["result"] == "Man City"
    assert s0["odds"]["Man City"] == {"A": 2.1}
    assert snaps[1]["market"] == "totals"


def test_to_snapshots_speist_strategy_end_to_end():
    from arbfinder.strategies import get

    ev = Event(
        event_id="x",
        home="H",
        away="Aw",
        start_time=_dt(),
        markets=[Market("h2h", {"H": {"A": 2.70}, "X": {"B": 3.55}, "Aw": {"B": 3.30}}, 3)],
    )
    sigs = get("arbitrage").evaluate(ev.to_snapshots()[0])
    assert sigs and sigs[0].edge_pct > 0


def test_get_market_statt_blind_index_null():
    ev = Event(
        event_id="e1",
        home="H",
        away="Aw",
        start_time=_dt(),
        markets=[Market("h2h", {}, 3), Market("totals", {}, 2)],
    )
    assert ev.get_market("totals").market_type == "totals"
    assert ev.get_market("spreads") is None
