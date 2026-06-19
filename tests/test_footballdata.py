import logging

from arbfinder import backtest
from arbfinder.providers.base import read_jsonl
from arbfinder.providers.footballdata import FootballDataProvider, to_jsonl

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


def test_laeuft_durch_normalize_team_identitaet(tmp_path):
    out = tmp_path / "o.jsonl"
    to_jsonl(_SAMPLE, out)
    names = {r["event_name"] for r in read_jsonl(out)}
    assert any("Manchester City" in n for n in names)               # 'Man City' -> kanonisch


def test_value_backtest_auf_footballdata_end_to_end(tmp_path):
    out = tmp_path / "o.jsonl"
    assert to_jsonl(_SAMPLE, out) == 8
    res, v = backtest.run_validated("value", str(out))
    assert res.events == 8 and res.n_with_result == 8              # ECHTE Ergebnisse vorhanden
    assert res.signals > 0 and res.realized_pnl is not None        # PnL wird berechnet
    assert v.status == "parked"                                    # kleine Stichprobe -> nicht confirmed
