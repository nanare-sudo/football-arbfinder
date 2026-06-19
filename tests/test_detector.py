from datetime import datetime, timezone

from arbfinder.detector import detect
from arbfinder.models import Event, Market
from arbfinder.providers import MockProvider
from arbfinder.providers.base import OddsProvider


def _dt(s="2026-08-15T15:00:00+00:00"):
    return datetime.fromisoformat(s)


class _ListProvider(OddsProvider):
    """Test-Provider, der eine feste Event-Liste liefert."""

    name = "list"

    def __init__(self, events):
        self._events = events

    def fetch_events(self):
        return list(self._events)


def test_detect_gegen_mock_fixture():
    res = detect(MockProvider("fixtures/recorded_odds.jsonl"))
    assert res.events_in == 5                # 3 Arb-Events + 2 Value-Events (v1, v2)
    assert res.events_merged == 5            # alle verschiedene Events
    # e2 ist unvollstaendig (nur 2/3 Ausgaenge) -> gezaehlt, nicht still weg.
    assert res.skipped_incomplete == 1
    # v1/v2 sind KEINE Arbitrage -> die Arbitrage-Strategie meldet nur e1 und e3.
    assert len(res.signals) == 2
    assert all(s.edge_pct > 0 for s in res.signals)


def test_unvollstaendige_maerkte_werden_gezaehlt_nicht_signalisiert():
    incomplete = Event(
        "e", "H", "Aw", _dt(),
        markets=[Market("h2h", {"H": {"A": 1.5}, "Aw": {"A": 2.0}}, expected_outcomes=3)],
    )
    res = detect(_ListProvider([incomplete]))
    assert res.skipped_incomplete == 1
    assert res.signals == []


def test_markttypen_werden_getrennt_nie_vermischt():
    # h2h (3-Wege, vollstaendig, ein echtes Arb) + totals (2-Wege) am selben Event.
    ev = Event(
        "e", "H", "Aw", _dt(),
        markets=[
            Market("h2h", {"H": {"A": 2.70}, "X": {"B": 3.55}, "Aw": {"B": 3.30}}, 3),
            Market("totals", {"Over": {"A": 1.90}, "Under": {"B": 1.95}}, 2),
        ],
    )
    res = detect(_ListProvider([ev]))
    assert res.markets_checked == 2          # zwei getrennte Maerkte
    markets = {s.market for s in res.signals}
    # h2h ist ein Arb; totals (1/1.9 + 1/1.95 > 1) ist keins -> nur h2h signalisiert.
    assert markets == {"h2h"}


def test_min_profit_filtert():
    ev = Event(
        "e", "H", "Aw", _dt(),
        markets=[Market("h2h", {"H": {"A": 2.70}, "X": {"B": 3.55}, "Aw": {"B": 3.30}}, 3)],
    )
    lo = detect(_ListProvider([ev]), min_profit_pct=0.0)
    hi = detect(_ListProvider([ev]), min_profit_pct=99.0)
    assert len(lo.signals) == 1
    assert hi.signals == []                  # zu hohe Schwelle -> nichts


def test_to_dict_ist_serialisierbar():
    import json

    res = detect(MockProvider("fixtures/recorded_odds.jsonl"))
    d = res.to_dict()
    assert d["n_signals"] == 2
    json.dumps(d)                            # darf nicht werfen
