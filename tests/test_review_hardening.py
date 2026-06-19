"""
Zusaetzliche Tests aus dem adversarialen Review (Korrektheits- & Schutz-Fixes
sowie geschlossene Abdeckungsluecken). Jeder Test pinnt ein konkretes Verhalten,
das vorher ungetestet war oder durch einen Fix geaendert wurde.
"""
from datetime import datetime, timedelta, timezone

import logging

import pytest

from arbfinder import backtest, cli
from arbfinder.backtest import _simulate_pnl
from arbfinder.models import Event, Market, count_priced_outcomes
from arbfinder.normalize import canonical_team, merge_events
from arbfinder.providers.base import read_jsonl
from arbfinder.providers.theoddsapi import _expected_outcomes, parse_response

_DT = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)


def _ev(home, away, when, *, market="h2h", odds=None, exp=3, result=None, ts=None, eid="x"):
    return Event(
        event_id=eid, home=home, away=away, start_time=when,
        markets=[Market(market, odds or {}, exp)], result=result, snapshot_ts=ts,
    )


# --------------------------------------------------------------------------- #
# models: gemeinsame Vollstaendigkeits-Semantik
# --------------------------------------------------------------------------- #
def test_count_priced_outcomes_ignoriert_leere_bookmap():
    assert count_priced_outcomes({"A": {"x": 2.0}, "B": {}, "C": {"y": 3.1}}) == 2


def test_to_snapshots_exakter_keyset():
    snap = _ev("H", "Aw", _DT, odds={"H": {"A": 2.0}}).to_snapshots()[0]
    assert set(snap) == {
        "ts", "event_id", "event_name", "start_time",
        "market", "expected_outcomes", "odds", "result",
    }


# --------------------------------------------------------------------------- #
# normalize: difflib-Fallback, Single-Linkage, Reconciliation, Noise-Tokens
# --------------------------------------------------------------------------- #
def test_difflib_fallback_ohne_rapidfuzz(monkeypatch):
    import arbfinder.normalize as nz

    monkeypatch.setattr(nz, "_HAVE_RAPIDFUZZ", False)
    assert nz.canonical_team("Manchestr City") == "Manchester City"   # Tippfehler korrigiert
    assert nz.canonical_team("City Manchester") == "Manchester City"  # Wortreihenfolge (token-sort)
    assert nz.canonical_team("United") == "United"                    # kein Falschtreffer


def test_merge_single_linkage_ist_transitiv():
    # 15:00 - 16:00 - 17:00 (Luecken je 60 <= 90) -> EIN Event.
    # Der alte anker-basierte Code haette 17:00 abgespalten (120 > 90).
    evs = [
        _ev("Man City", "Arsenal", _DT + timedelta(hours=h),
            eid=f"e{h}", odds={"Manchester City": {f"B{h}": 2.0}})
        for h in (0, 1, 2)
    ]
    assert len(merge_events(evs)) == 1


def test_merge_grosse_luecke_trennt():
    e1 = _ev("Man City", "Arsenal", _DT, eid="a", odds={"Manchester City": {"B": 2.0}})
    e2 = _ev("Man City", "Arsenal", _DT + timedelta(hours=3), eid="b",
             odds={"Manchester City": {"B": 2.0}})
    assert len(merge_events([e1, e2])) == 2   # 180 min > 90 -> getrennt


def test_merge_reconciliation_expected_max_und_result():
    e1 = _ev("Man City", "Arsenal", _DT, exp=2, odds={"Man City": {"A": 2.0}}, eid="a")
    e2 = _ev("Man City", "Arsenal", _DT, exp=3, odds={"Draw": {"B": 3.6}},
             result="Man City", eid="b")
    merged = merge_events([e1, e2])
    assert len(merged) == 1
    m = merged[0].get_market("h2h")
    assert m.expected_outcomes == 3                      # max(2, 3)
    assert merged[0].result == "Manchester City"         # einseitiges result bleibt + normalisiert


def test_merge_gleicher_bookie_juengerer_snapshot_gewinnt():
    old = _ev("Man City", "Arsenal", _DT, odds={"Man City": {"BookieA": 2.00}},
              ts=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc), eid="old")
    new = _ev("Man City", "Arsenal", _DT, odds={"Man City": {"BookieA": 2.50}},
              ts=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc), eid="new")
    for order in ([old, new], [new, old]):                # Ergebnis unabhaengig von Reihenfolge
        merged = merge_events(order)
        assert merged[0].get_market("h2h").odds["Manchester City"]["BookieA"] == 2.50


def test_noise_tokens_kein_falschmerge():
    assert canonical_team("Arsenal FC") == "Arsenal"                    # fc weiter entfernt
    assert canonical_team("Barcelona SC") != canonical_team("Barcelona")  # SC NICHT entfernt


# --------------------------------------------------------------------------- #
# theoddsapi: 2-Wege, Linien-Trennung, expected-Helper
# --------------------------------------------------------------------------- #
_TENNIS = [{
    "id": "t1", "sport_key": "tennis_atp", "commence_time": "2026-08-15T15:00:00Z",
    "home_team": "Nadal", "away_team": "Federer",
    "bookmakers": [{"key": "pin", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Nadal", "price": 1.8}, {"name": "Federer", "price": 2.1}]}]}],
}]

_TOTALS = [{
    "id": "o1", "sport_key": "soccer_epl", "commence_time": "2026-08-15T15:00:00Z",
    "home_team": "A", "away_team": "B",
    "bookmakers": [
        {"key": "b1", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "price": 1.90, "point": 2.5},
            {"name": "Under", "price": 1.90, "point": 2.5}]}]},
        {"key": "b2", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "price": 2.50, "point": 3.5},
            {"name": "Under", "price": 1.50, "point": 3.5}]}]},
    ],
}]


def test_theoddsapi_tennis_2way_expected_2():
    ev = parse_response(_TENNIS)[0]
    assert ev.get_market("h2h").expected_outcomes == 2


def test_theoddsapi_totals_linien_getrennt_kein_phantom():
    ev = parse_response(_TOTALS)[0]
    # zwei getrennte Linien-Maerkte, NICHT ein kollabierter Markt:
    assert set(ev.market_types) == {"totals_2.5", "totals_3.5"}
    m25 = ev.get_market("totals_2.5")
    assert set(m25.odds) == {"Over", "Under"} and m25.expected_outcomes == 2
    # die kollabierte (fehlerhafte) Variante haette Over@3.5 (2.5) mit Under@2.5 (1.9)
    # zu einem Phantom-Arb (margin<1) gemischt; getrennt ist keine Linie ein Arb:
    from arbfinder.arbitrage import Quote, find_arbitrage
    for mt in ("totals_2.5", "totals_3.5"):
        m = ev.get_market(mt)
        quotes = [Quote(o, b, p) for o, bk in m.odds.items() for b, p in bk.items()]
        assert not find_arbitrage(mt, quotes).is_arbitrage


def test_expected_outcomes_helper():
    assert _expected_outcomes("h2h", "soccer_epl", 3) == 3
    assert _expected_outcomes("h2h", "tennis_atp", 2) == 2
    assert _expected_outcomes("totals", "soccer_epl", 2) == 2
    assert _expected_outcomes("spreads", "soccer_epl", 2) == 2
    assert _expected_outcomes("btts", "soccer_epl", 2) == 0   # unbekannt -> "Erwartung unbekannt"


# --------------------------------------------------------------------------- #
# providers.read_jsonl: defensiv gegen kaputte Zeilen
# --------------------------------------------------------------------------- #
def test_read_jsonl_ueberspringt_kaputte_zeile(tmp_path, caplog):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a":1}\nNICHT JSON\n{"b":2}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        rows = read_jsonl(p)
    assert rows == [{"a": 1}, {"b": 2}]
    assert any("kein gueltiges JSON" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# backtest: realized_pnl & _simulate_pnl & leere-Bookmap-Vollstaendigkeit
# --------------------------------------------------------------------------- #
def test_realized_pnl_wert_und_count():
    res = backtest.run("arbitrage", "fixtures/recorded_odds.jsonl")
    assert res.n_with_result == 2
    assert res.realized_pnl == 51.65


def test_simulate_pnl_branches():
    assert _simulate_pnl({"H": 500.0, "Aw": 500.0}, "H", {"H": {"A": 2.2}}) == 100.0
    assert _simulate_pnl({"H": 500.0, "Aw": 500.0}, "X", {"H": {"A": 2.2}}) == -1000.0   # nicht gesetzt
    # gesetzt, aber kein bepreister Buchmacher fuer den Ausgang -> Totalverlust statt Crash:
    assert _simulate_pnl({"H": 500.0, "Aw": 500.0}, "H", {"Aw": {"B": 2.0}}) == -1000.0
    # bester Bookie fuer den Gewinnausgang:
    assert _simulate_pnl({"H": 1000.0}, "H", {"H": {"A": 2.0, "B": 2.5}}) == 1500.0


def test_backtest_zaehlt_leere_bookmap_als_incomplete(tmp_path):
    row = ('{"event_id":"x","commence_time":"2026-08-15T15:00:00Z","market":"h2h",'
           '"expected_outcomes":3,"odds":{"H":{"A":2.0},"X":{"A":3.4},"Aw":{}}}')
    p = tmp_path / "empty.jsonl"
    p.write_text(row + "\n", encoding="utf-8")
    res = backtest.run("arbitrage", str(p))
    assert res.signals == 0
    assert res.skipped_incomplete == 1   # leere Bookie-Map fuer 'Aw' -> nur 2 bepreiste Ausgaenge


# --------------------------------------------------------------------------- #
# cli: None-sicherer Vergleich
# --------------------------------------------------------------------------- #
def test_compare_and_warn_none_pnl_kein_crash(capsys):
    old = {"signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": None}
    new = {"signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": 10.0}
    cli._compare_and_warn(old, new)   # darf nicht werfen
    out = capsys.readouterr().out
    assert "realized_pnl: None -> 10.0" in out
    assert "WARNUNG" not in out


# --------------------------------------------------------------------------- #
# agent: Logger-Handling & Alert-Rendering
# --------------------------------------------------------------------------- #
def test_setup_alert_logger_idempotent_und_konsolenmodus(tmp_path):
    from arbfinder.agent import setup_alert_logger

    lf = str(tmp_path / "a.log")
    l1 = setup_alert_logger(lf)
    n1 = len(l1.handlers)
    l2 = setup_alert_logger(lf)
    assert l1 is l2 and len(l2.handlers) == n1            # keine Handler-Vermehrung
    lc = setup_alert_logger(None)
    files = [h for h in lc.handlers if isinstance(h, logging.FileHandler)]
    streams = [h for h in lc.handlers if type(h) is logging.StreamHandler]
    assert len(files) == 0 and len(streams) == 1          # Konsole-only Branch


def test_alert_message_leg_slots():
    from arbfinder.agent import AgentConfig, run_once

    alerts = run_once(
        AgentConfig(provider="mock", provider_kwargs={"path": "fixtures/recorded_odds.jsonl"}),
        now=_DT,
    )
    msg = next(a.message() for a in alerts if "Manchester City" in a.event_name)
    assert "Manchester City @ 2.12 (BookieB)" in msg      # Quote im Preis-Slot, Bookie in Klammern
