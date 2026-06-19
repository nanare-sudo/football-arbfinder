import json

from arbfinder import backtest
from arbfinder.strategies import get
from arbfinder.strategies.base import Strategy


def test_arbitrage_requires_validation_false():
    assert get("arbitrage").requires_validation is False
    assert Strategy.requires_validation is True  # Default fuer praediktive Strategien


def test_make_verdict_fuer_arbitrage_ist_confirmed_ohne_oos():
    res = backtest.run("arbitrage", "fixtures/recorded_odds.jsonl")
    v = backtest.make_verdict("arbitrage", res)
    assert res.avg_edge_pct > 0
    assert v.status == "confirmed"
    assert v.out_of_sample_edge is None       # reine Arbitrage braucht kein OOS
    assert v.in_sample_edge == res.avg_edge_pct


def test_make_verdict_rejected_wenn_kein_edge():
    # avg_edge_pct == 0 (keine Signale) -> rejected, auch fuer Arbitrage.
    fake = backtest.BacktestResult(
        strategy="arbitrage", events=0, signals=0, avg_edge_pct=0.0,
        skipped_incomplete=0, realized_pnl=None, n_with_result=0,
    )
    assert backtest.make_verdict("arbitrage", fake).status == "rejected"


def test_main_schreibt_metriken_und_verdict_nach_results(tmp_path):
    out = tmp_path / "bt.json"
    backtest.main(["--strategy", "arbitrage", "--data", "fixtures/recorded_odds.jsonl",
                   "--out", str(out)])
    data = json.loads(out.read_text())
    # Metriken top-level (plotting-kompatibel) UND Urteil daneben:
    assert data["strategy"] == "arbitrage"
    assert "avg_edge_pct" in data
    assert data["verdict"]["status"] == "confirmed"
