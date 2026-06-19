import logging
from datetime import datetime, timezone

import pytest

from arbfinder.models import Event
from arbfinder.providers import MockProvider, get_provider
from arbfinder.providers.base import parse_datetime, split_teams
from arbfinder.providers.theoddsapi import TheOddsApiProvider, parse_response
from arbfinder.providers.base import ProviderError


# --------------------------------------------------------------------------- #
# Mock-Provider gegen die echte Fixture
# --------------------------------------------------------------------------- #
def test_mock_liest_fixture_und_liefert_events():
    events = MockProvider("fixtures/recorded_odds.jsonl").fetch_events()
    assert len(events) == 5              # 3 Arb-Events (e1-e3) + 2 Value-Events (v1, v2)
    assert all(isinstance(e, Event) for e in events)
    e1 = next(e for e in events if e.event_id == "e1")
    assert e1.home == "Man City" and e1.away == "Arsenal"
    # Anstosszeit (commence_time), NICHT die Snapshot-Zeit (ts):
    assert e1.start_time == datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    assert e1.snapshot_ts == datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)
    assert e1.markets[0].market_type == "h2h"
    assert e1.markets[0].expected_outcomes == 3
    assert e1.result == "Man City"


def test_mock_events_speisen_strategy(tmp_path):
    events = MockProvider("fixtures/recorded_odds.jsonl").fetch_events()
    from arbfinder.strategies import get

    strat = get("arbitrage")
    sigs = [s for e in events for snap in e.to_snapshots() for s in strat.evaluate(snap)]
    assert sigs  # e1 und e3 sind echte Arbs


def test_get_provider_registry():
    assert isinstance(get_provider("mock"), MockProvider)
    with pytest.raises(KeyError):
        get_provider("does-not-exist")


# --------------------------------------------------------------------------- #
# Defensives Parsen: alternative Feldnamen + Listen-Quoten
# --------------------------------------------------------------------------- #
def test_mock_defensive_feldnamen(tmp_path):
    # Voellig andere Schlussel als die Standard-Fixture:
    line = (
        '{"id":"z9","home_team":"FC Bar","away_team":"FC Baz",'
        '"kickoff":"2026-09-01T18:00:00Z","market_key":"h2h","n_outcomes":2,'
        '"prices":[{"name":"FC Bar","price":"2.10","bookmaker":"X"},'
        '{"name":"FC Baz","price":2.0,"bookmaker":"Y"}],"winner":"FC Bar"}'
    )
    p = tmp_path / "alt.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    events = MockProvider(p).fetch_events()
    assert len(events) == 1
    ev = events[0]
    assert (ev.home, ev.away) == ("FC Bar", "FC Baz")
    assert ev.start_time == datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    assert ev.markets[0].odds["FC Bar"] == {"X": 2.10}  # String-Quote zu float
    assert ev.result == "FC Bar"


def test_mock_ueberspringt_fehlende_anstosszeit_ohne_erfinden(tmp_path, caplog):
    # Zeile OHNE Anstosszeit -> darf NICHT mit erfundener Zeit durchrutschen.
    bad = '{"event_id":"x","event_name":"A v B","market":"h2h","odds":{"A":{"K":2.0},"B":{"K":2.0}}}'
    good = ('{"event_id":"y","event_name":"C v D","commence_time":"2026-09-02T12:00:00Z",'
            '"market":"h2h","odds":{"C":{"K":2.0},"D":{"K":2.0}}}')
    p = tmp_path / "mixed.jsonl"
    p.write_text(bad + "\n" + good + "\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        events = MockProvider(p).fetch_events()
    assert [e.event_id for e in events] == ["y"]  # kaputte Zeile weg, gute bleibt
    assert any("uebersprungen" in r.message for r in caplog.records)


def test_split_teams_varianten():
    assert split_teams("Man City v Arsenal") == ("Man City", "Arsenal")
    assert split_teams("Nadal vs Federer") == ("Nadal", "Federer")
    assert split_teams("Team A @ Team B") == ("Team A", "Team B")
    with pytest.raises(ValueError):
        split_teams("kein trenner hier")


def test_parse_datetime_varianten():
    assert parse_datetime("2026-08-15T15:00:00Z").tzinfo is not None
    # Epoch-Sekunden werden als UTC interpretiert:
    assert parse_datetime(1755270000) == datetime(2025, 8, 15, 15, 0, tzinfo=timezone.utc)
    # naiver String -> als UTC angenommen
    assert parse_datetime("2026-01-01T00:00:00").tzinfo is not None
    with pytest.raises(ValueError):
        parse_datetime("nicht-ein-datum")


# --------------------------------------------------------------------------- #
# The Odds API: Mapping ohne Netzwerk
# --------------------------------------------------------------------------- #
_SAMPLE = [
    {
        "id": "abc",
        "sport_key": "soccer_epl",
        "sport_title": "EPL",
        "commence_time": "2026-08-15T15:00:00Z",
        "home_team": "Manchester City",
        "away_team": "Arsenal",
        "bookmakers": [
            {"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Manchester City", "price": 2.10},
                    {"name": "Draw", "price": 3.60},
                    {"name": "Arsenal", "price": 4.00}]}]},
            {"key": "bet365", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Manchester City", "price": 2.12},
                    {"name": "Draw", "price": 3.75},
                    {"name": "Arsenal", "price": 4.10}]}]},
        ],
    }
]


def test_theoddsapi_parse_response_mappt_und_setzt_expected_outcomes():
    events = parse_response(_SAMPLE)
    assert len(events) == 1
    ev = events[0]
    assert ev.home == "Manchester City"
    m = ev.get_market("h2h")
    assert m is not None
    # Soccer -> 3-Wege erwartet (Remis moeglich)
    assert m.expected_outcomes == 3
    assert m.odds["Manchester City"] == {"pinnacle": 2.10, "bet365": 2.12}


def test_theoddsapi_ohne_key_wirft_klare_meldung(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    prov = TheOddsApiProvider(sport="soccer_epl", api_key=None)
    with pytest.raises(ProviderError):
        prov.fetch_events()
