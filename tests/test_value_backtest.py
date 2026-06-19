"""Backtest-Anbindung der Value-Strategie: dreistufiges Urteil greift."""
import json

from arbfinder import backtest


def _value_row(eid, result, *, kickoff):
    # 3 Bookies, B3 grosszuegig auf 'H' -> Value (nur) auf 'H' (Default min_books=2:
    # nach Leave-one-out bleiben B1,B2). 'A' feuert nicht (B1,B2 gleichauf am Max).
    return {
        "event_id": eid, "event_name": f"{eid} Home v {eid} Away",
        "commence_time": kickoff, "market": "h2h", "expected_outcomes": 2,
        "odds": {"H": {"B1": 2.0, "B2": 2.0, "B3": 2.5}, "A": {"B1": 2.0, "B2": 2.0, "B3": 1.7}},
        "result": result,
    }


def _write_rows(tmp_path, rows):
    p = tmp_path / "vrun.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


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


def test_run_validated_confirmed_deterministisch_bei_gewinnenden_oos_wetten(tmp_path):
    # 5 Value-Events, jede Wette (auf 'H') GEWINNT. Deterministisch: 5 belegte
    # OOS-Wetten, je +150 auf 100 Einsatz -> OOS-Edge exakt +150%. min_samples=1.
    rows = [_value_row(f"w{i}", "H", kickoff=f"2026-08-1{i}T15:00:00Z") for i in range(5)]
    res, v = backtest.run_validated("value", _write_rows(tmp_path, rows), min_samples=1)
    assert v.details == {} and v.status == "confirmed"
    assert v.out_of_sample_edge == 150.0          # exakt, nicht zufaellig


def test_run_validated_verlierende_oos_wetten_bleiben_parked(tmp_path):
    # Gleiche 5 Events, aber jede Wette VERLIERT (Ergebnis 'A'): OOS-Edge -100%.
    # Beweist, dass Verluste gezaehlt werden UND ein negatives OOS NICHT confirmt.
    rows = [_value_row(f"l{i}", "A", kickoff=f"2026-08-1{i}T15:00:00Z") for i in range(5)]
    res, v = backtest.run_validated("value", _write_rows(tmp_path, rows), min_samples=1)
    assert v.out_of_sample_edge == -100.0
    assert v.status == "parked"                   # kein false confirm bei Verlust


def test_run_validated_zu_wenige_zeilen_fuer_split_bleibt_parked(tmp_path):
    # len(rows) < k -> Split-Schleife wird uebersprungen -> kein OOS -> parked.
    rows = [_value_row("only", "H", kickoff="2026-08-15T15:00:00Z")]
    res, v = backtest.run_validated("value", _write_rows(tmp_path, rows))
    assert v.out_of_sample_edge is None and v.status == "parked"


def test_run_validated_keine_belegten_wetten_bleibt_parked_ohne_crash(tmp_path):
    # >= k Zeilen, aber KEIN Ergebnis -> staked==0 -> oos None (kein ZeroDivision).
    rows = [_value_row(f"n{i}", None, kickoff=f"2026-08-1{i}T15:00:00Z") for i in range(5)]
    res, v = backtest.run_validated("value", _write_rows(tmp_path, rows))
    assert v.out_of_sample_edge is None and v.status == "parked"


def test_run_validated_arbitrage_braucht_kein_oos():
    res, v = backtest.run_validated("arbitrage", "fixtures/recorded_odds.jsonl")
    assert v.status == "confirmed"
    assert v.out_of_sample_edge is None              # Arbitrage: kein OOS-Schritt
