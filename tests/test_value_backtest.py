"""Backtest-Anbindung der Value-Strategie: dreistufiges Urteil greift."""
from arbfinder import backtest


def _result(avg_edge, *, signals=3, n_with_result=0, pnl=None):
    return backtest.BacktestResult(
        strategy="value", events=10, signals=signals, avg_edge_pct=avg_edge,
        skipped_incomplete=0, realized_pnl=pnl, n_with_result=n_with_result,
    )


def test_value_wird_geparkt_bei_edge_ohne_oos():
    # in-sample Edge vorhanden, aber kein Out-of-Sample-Wert -> PARKED, nicht verworfen.
    v = backtest.make_verdict("value", _result(4.0))
    assert v.status == "parked"
    assert v.out_of_sample_edge is None


def test_value_wird_rejected_ohne_edge():
    # auch praediktive Strategien ohne in-sample Signal -> rejected.
    assert backtest.make_verdict("value", _result(0.0, signals=0)).status == "rejected"


def test_value_confirmed_nur_mit_robustem_oos():
    # Mit explizitem, tragendem OOS-Wert (genug Samples) -> confirmed.
    v = backtest.make_verdict("value", _result(4.0),
                              out_of_sample_edge=3.0, min_samples=100, n_samples=500)
    assert v.status == "confirmed"


def test_value_oos_schwach_wird_geparkt_nicht_verworfen():
    # in-sample stark, out-of-sample schwach -> parken (kein hartes Fallbeil).
    v = backtest.make_verdict("value", _result(4.0),
                              out_of_sample_edge=0.0, min_samples=100, n_samples=500)
    assert v.status == "parked"


def test_arbitrage_bleibt_confirmed_ohne_validierung():
    # Kontrast: reine Arbitrage braucht KEIN OOS und ist sofort confirmed.
    arb = backtest.BacktestResult("arbitrage", 3, 2, 2.5, 1, 51.65, 2)
    assert backtest.make_verdict("arbitrage", arb).status == "confirmed"


def test_value_requires_validation_steuert_judge():
    # Der Pfad haengt ausschliesslich am Strategie-Flag requires_validation.
    from arbfinder.strategies import get

    assert get("value").requires_validation is True
    assert get("arbitrage").requires_validation is False


# --------------------------------------------------------------------------- #
# A3: In-/Out-of-Sample-Split (purged_split) — Mechanismus & ehrliche Einordnung
# --------------------------------------------------------------------------- #
def test_run_validated_value_duenne_daten_bleibt_parked():
    # Mechanismus laeuft (OOS-Edge wird aus den 2 belegten Events berechnet),
    # aber zu wenige OOS-Belege -> bewusst "parked", NICHT faelschlich confirmed.
    res, v = backtest.run_validated("value", "fixtures/recorded_odds.jsonl")
    assert v.status == "parked"
    assert v.out_of_sample_edge is not None          # Split hat etwas berechnet
    assert v.details["n_samples"] < v.details["min_samples"]   # zu duenn belegt


def test_run_validated_mechanismus_kann_confirmen_wenn_genug_belege():
    # Beweis, dass der confirmed-Pfad ERREICHBAR ist: dieselben 2 OOS-Wetten,
    # aber die Sample-Schwelle gesenkt -> positiver OOS-Edge fuehrt zu confirmed.
    res, v = backtest.run_validated("value", "fixtures/recorded_odds.jsonl", min_samples=1)
    assert v.out_of_sample_edge is not None and v.out_of_sample_edge > 0
    assert v.status == "confirmed"


def test_run_validated_arbitrage_braucht_kein_oos():
    res, v = backtest.run_validated("arbitrage", "fixtures/recorded_odds.jsonl")
    assert v.status == "confirmed"
    assert v.out_of_sample_edge is None              # Arbitrage: kein OOS-Schritt
