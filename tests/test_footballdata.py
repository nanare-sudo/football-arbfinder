import logging
import os

import pytest

from arbfinder import backtest
from arbfinder.providers.base import read_jsonl
from arbfinder.providers.footballdata import (
    FootballDataProvider,
    _bookie_triples,
    _parse_kickoff,
    to_jsonl,
)

_SAMPLE = "tests/data/footballdata_sample.csv"


def test_mappt_spalten_und_setzt_result():
    evs = FootballDataProvider(_SAMPLE).fetch_events()
    assert len(evs) == 8
    e = evs[0]
    assert e.home == "Man City" and e.away == "Chelsea"
    m = e.markets[0]
    assert set(m.odds) == {"Man City", "Draw", "Chelsea"} and m.expected_outcomes == 3
    assert set(m.odds["Man City"]) == {"B365", "BW", "PS", "WH"}    # Max/Avg NICHT als Bookie
    assert m.odds["Man City"]["B365"] == 1.50
    assert e.result == "Man City"                                   # FTR=H -> Heimteam


def test_result_mapping_h_d_a():
    by_home = {e.home: e for e in FootballDataProvider(_SAMPLE).fetch_events()}
    assert by_home["Arsenal"].result == "Draw"                      # FTR=D
    assert by_home["Tottenham"].result == "Newcastle"              # FTR=A -> Auswaertsteam
    assert by_home["Liverpool"].result == "Liverpool"             # FTR=H


def test_bevorzugt_schlussquoten(tmp_path):
    csv_text = ("Div,Date,HomeTeam,AwayTeam,FTR,B365H,B365D,B365A,B365CH,B365CD,B365CA\n"
                "E0,17/08/2024,A,B,H,1.50,4.0,6.0,1.80,4.2,5.0\n")
    p = tmp_path / "c.csv"
    p.write_text(csv_text, encoding="utf-8")
    e = FootballDataProvider(p).fetch_events()[0]
    assert e.markets[0].odds["A"]["B365"] == 1.80                   # Closing, nicht 1.50


def test_bookie_code_auf_c_nicht_als_closing_missdeutet(tmp_path):
    # VC (VC Bet) endet selbst auf 'C': VCH/VCD/VCA = pre-match, VCCH/... = closing.
    # Es darf KEIN Phantom-Bookie 'V' entstehen; 'VC' muss die Schlussquote nutzen.
    csv_text = ("Div,Date,HomeTeam,AwayTeam,FTR,VCH,VCD,VCA,VCCH,VCCD,VCCA\n"
                "E0,17/08/2024,A,B,H,2.00,3.4,3.6,2.10,3.5,3.7\n")
    p = tmp_path / "vc.csv"
    p.write_text(csv_text, encoding="utf-8")
    e = FootballDataProvider(p).fetch_events()[0]
    assert set(e.markets[0].odds["A"]) == {"VC"}              # nicht {'V','VC'}
    assert e.markets[0].odds["A"]["VC"] == 2.10              # Closing (VCCH), nicht 2.00


def test_ueberspringt_zeile_ohne_teams(tmp_path, caplog):
    csv_text = ("Div,Date,HomeTeam,AwayTeam,FTR,B365H,B365D,B365A\n"
                "E0,17/08/2024,,B,H,1.5,4.0,6.0\n"
                "E0,18/08/2024,C,D,A,2.0,3.4,3.6\n")
    p = tmp_path / "x.csv"
    p.write_text(csv_text, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        evs = FootballDataProvider(p).fetch_events()
    assert [e.home for e in evs] == ["C"]                           # kaputte Zeile uebersprungen


def test_bookie_triples_ignoriert_handicap_overunder_corner():
    # Nur echte 1X2-Triples; AH (kein D), Over/Under, Corners, Aggregate raus.
    fields = ["HomeTeam", "AwayTeam", "FTR",
              "B365H", "B365D", "B365A",            # 1X2 Bookie
              "B365AHH", "B365AHA",                 # Asian Handicap (kein D)
              "B365>2.5", "B365<2.5",               # Over/Under
              "HC", "AC",                           # Eckball-Spalten
              "MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA"]  # Aggregate
    assert set(_bookie_triples(fields)) == {"B365"}


def test_bookie_triples_schliesst_closing_aggregate_aus():
    fields = ["B365H", "B365D", "B365A", "B365CH", "B365CD", "B365CA",
              "MaxH", "MaxD", "MaxA", "MaxCH", "MaxCD", "MaxCA"]
    res = _bookie_triples(fields)
    assert set(res) == {"B365"}                                     # Max UND MaxC ausgeschlossen
    assert res["B365"] == ("B365CH", "B365CD", "B365CA")            # Closing bevorzugt


def test_ftr_leer_oder_unbekannt_setzt_result_none(tmp_path):
    csv_text = ("Div,Date,HomeTeam,AwayTeam,FTR,B365H,B365D,B365A\n"
                "E0,17/08/2024,A,B,,1.5,4.0,6.0\n"                  # FTR leer
                "E0,18/08/2024,C,D,X,2.0,3.4,3.6\n")                # FTR ungueltig
    p = tmp_path / "f.csv"
    p.write_text(csv_text, encoding="utf-8")
    evs = FootballDataProvider(p).fetch_events()
    assert len(evs) == 2 and all(e.result is None for e in evs)     # nicht uebersprungen, nichts erfunden


def test_parse_kickoff_zeit_fehlt_oder_unparsbar_datumsgenau():
    assert _parse_kickoff("17/08/2024", "").hour == 0              # keine Zeit -> 00:00
    k = _parse_kickoff("17/08/2024", "nonsense")
    assert (k.hour, k.minute) == (0, 0)                            # unparsbare Zeit -> 00:00 (kein Raten)
    assert _parse_kickoff("17/08/2024", "12:30").hour == 12
    assert _parse_kickoff("17/08/24", "").year == 2024             # zweistelliges Jahr


def test_to_jsonl_ueberschreibt_statt_anzuhaengen(tmp_path):
    out = tmp_path / "o.jsonl"
    n1 = to_jsonl(_SAMPLE, out)
    n2 = to_jsonl(_SAMPLE, out)
    assert n1 == n2 == 8 and len(read_jsonl(out)) == 8             # ueberschrieben, nicht verdoppelt


def test_echtes_csv_parst_wenn_vorhanden():
    path = "data/E0_2324.csv"
    if not os.path.exists(path):
        pytest.skip("echtes football-data CSV nicht vorhanden (gitignored)")
    evs = FootballDataProvider(path).fetch_events()
    assert len(evs) == 380
    bookies = set(evs[0].markets[0].odds.get(evs[0].home, {}))
    assert {"B365", "PS", "VC", "WH"}.issubset(bookies)            # echte Bookies, VC via Closing
    assert not ({"V", "Max", "Avg", "MaxC", "AvgC"} & bookies)     # keine Phantome/Aggregate


def test_laeuft_durch_normalize_team_identitaet(tmp_path):
    out = tmp_path / "o.jsonl"
    to_jsonl(_SAMPLE, out)
    names = {r["event_name"] for r in read_jsonl(out)}
    assert any("Manchester City" in n for n in names)               # 'Man City' -> kanonisch
    assert not any("Man City" in n for n in names)                  # rohe Schreibweise verschwunden


def test_value_backtest_auf_footballdata_end_to_end(tmp_path):
    out = tmp_path / "o.jsonl"
    assert to_jsonl(_SAMPLE, out) == 8
    res, v = backtest.run_validated("value", str(out))
    assert res.events == 8 and res.n_with_result == 8              # ECHTE Ergebnisse vorhanden
    assert res.signals > 0 and res.realized_pnl is not None        # PnL wird berechnet
    assert v.status == "parked"                                    # kleine Stichprobe -> nicht confirmed


# --------------------------------------------------------------------------- #
# Pinnacle-Loader (offen + Schluss + waehlbare Bet-Quelle)
# --------------------------------------------------------------------------- #
_PINN = "tests/data/footballdata_pinnacle_sample.csv"


def test_load_pinnacle_events_keys_und_result():
    from arbfinder.providers.footballdata import load_pinnacle_events
    from arbfinder.fair_probability import PinnacleAnchorModel

    evs = load_pinnacle_events(_PINN, bet_source="Max")
    assert len(evs) == 8
    e = evs[0]
    assert e.home == "Man City" and e.result == "Man City"        # FTR=H
    m = e.markets[0].odds
    # Eroeffnungsquellen + Pinnacle-Schluss unter eindeutigen Keys:
    assert {"PS", "PSC", "Max", "B365", "Avg"}.issubset(set(m["Man City"]))
    assert m["Man City"]["PS"] == 2.00 and m["Man City"]["PSC"] == 2.20
    assert m["Man City"]["Max"] == 2.40
    # Pinnacle-Anker (open) devigt sauber:
    fair = PinnacleAnchorModel(anchor="open").estimate(e.markets[0].odds)
    assert abs(sum(fair.values()) - 1.0) < 1e-9


def test_load_pinnacle_bet_source_b365_und_fehlerfall():
    from arbfinder.providers.footballdata import load_pinnacle_events

    evs = load_pinnacle_events(_PINN, bet_source="B365")          # andere Bet-Quelle ok
    assert "B365" in evs[0].markets[0].odds["Man City"]
    with pytest.raises(ValueError):
        load_pinnacle_events(_PINN, bet_source="DoesNotExist")
