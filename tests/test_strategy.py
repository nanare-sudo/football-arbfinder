from arbfinder.strategies import get
from arbfinder import backtest


def test_arbitrage_strategy_findet_arb():
    ev = {"event_id": "x", "event_name": "T", "market": "h2h", "expected_outcomes": 3,
          "odds": {"H": {"A": 2.70}, "X": {"B": 3.55}, "Aw": {"B": 3.30}}}
    sigs = get("arbitrage").evaluate(ev)
    assert sigs and sigs[0].edge_pct > 0


def test_unvollstaendig_wird_verworfen():
    ev = {"event_id": "x", "market": "h2h", "expected_outcomes": 3,
          "odds": {"H": {"A": 1.5}, "Aw": {"A": 2.0}}}  # Draw fehlt
    assert get("arbitrage").evaluate(ev) == []


def test_backtest_laeuft():
    res = backtest.run("arbitrage", "fixtures/recorded_odds.jsonl")
    assert res.events == 5                # 3 Arb-Events + 2 Value-Events (v1, v2)
    assert res.skipped_incomplete == 1    # nur e2 ist unvollstaendig; v1/v2 sind komplett
