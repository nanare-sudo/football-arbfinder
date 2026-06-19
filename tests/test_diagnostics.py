from datetime import datetime, timezone

from arbfinder import cli
from arbfinder.diagnostics import (
    BetRecord,
    _assess,
    _odds_bucket,
    _season,
    collect_bets,
    concentration,
    diagnose,
    format_report,
    simulate,
)
from arbfinder.providers.footballdata import to_jsonl

_CT = datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc)


def _bet(odd, won, *, bookie="B", fair=0.5, when=_CT):
    return BetRecord(when, _season(when), "E v F", "H", bookie, odd, fair, won)


# --------------------------------------------------------------------------- #
# Helfer
# --------------------------------------------------------------------------- #
def test_season_label():
    assert _season(datetime(2023, 8, 15, tzinfo=timezone.utc)) == "2023/24"
    assert _season(datetime(2024, 1, 15, tzinfo=timezone.utc)) == "2023/24"   # gleiche Saison
    assert _season(datetime(2024, 8, 15, tzinfo=timezone.utc)) == "2024/25"


def test_odds_bucket():
    assert _odds_bucket(1.5) == "<2.0"
    assert _odds_bucket(2.5) == "2.0-3.5"
    assert _odds_bucket(4.0) == "3.5-6.0"
    assert _odds_bucket(7.0) == ">6.0"


def test_odds_bucket_grenzen():
    assert _odds_bucket(2.0) == "2.0-3.5"          # strikt < an den Grenzen
    assert _odds_bucket(3.5) == "3.5-6.0"
    assert _odds_bucket(6.0) == ">6.0"
    assert _odds_bucket(1.999) == "<2.0" and _odds_bucket(5.999) == "3.5-6.0"


# --------------------------------------------------------------------------- #
# Schritt 1: Bankroll-Management
# --------------------------------------------------------------------------- #
def test_flat_pnl_und_roi():
    s = simulate([_bet(2.0, True), _bet(2.0, False)], rule="flat", start_capital=100.0, flat_pct=1.0)
    assert s.n_bets == 2 and s.n_wins == 1
    assert abs(s.end_capital - 100.0) < 1e-9        # +1 (Sieg) -1 (Verlust) = 0
    assert s.turnover == 2.0 and abs(s.roi_pct) < 1e-9


def test_flat_gewinnlauf():
    s = simulate([_bet(2.0, True)] * 3, rule="flat", start_capital=100.0, flat_pct=1.0)
    assert abs(s.end_capital - 103.0) < 1e-9 and abs(s.roi_pct - 100.0) < 1e-9


def test_konto_ist_bindende_grenze_und_ruin():
    # Start 2 EUR, flat 1 EUR: zwei Verluste -> Konto 0 -> RUIN beim 3. Versuch.
    s = simulate([_bet(2.0, False)] * 5, rule="flat", start_capital=2.0, flat_pct=50.0)
    assert s.ruined and s.ruin_bet_index == 2
    assert s.n_bets == 2 and s.end_capital == 0.0       # nur 2 Wetten platziert, dann Stopp


def test_setzt_nie_mehr_als_vorhanden():
    s = simulate([_bet(2.0, False)] * 3, rule="flat", start_capital=0.5, flat_pct=200.0)
    assert s.placed[0].stake == 0.5 and s.ruined         # nur das vorhandene Kapital gesetzt


def test_kelly_compoundet_und_ruint_nie():
    # Reine Verlustserie: fraktionale Kelly schrumpft geometrisch, erreicht nie 0.
    s = simulate([_bet(2.0, False, fair=0.6)] * 20, rule="kelly",
                 start_capital=100.0, kelly_fraction=0.25, kelly_cap=0.1)
    assert not s.ruined and 0 < s.end_capital < 100.0


def test_kelly_exakte_stake_und_endkapital():
    # f = 0.25 * (0.6*2-1)/(2-1) = 0.05 -> Einsatz 5 EUR, Gewinn 5 EUR.
    s1 = simulate([_bet(2.0, True, fair=0.6)], rule="kelly", start_capital=100.0,
                  kelly_fraction=0.25, kelly_cap=0.1)
    assert abs(s1.placed[0].stake - 5.0) < 1e-9 and abs(s1.placed[0].pnl - 5.0) < 1e-9
    # 20 Verluste compoundet: 100 * 0.95^20 (pinnt die Kelly-Formel exakt).
    s20 = simulate([_bet(2.0, False, fair=0.6)] * 20, rule="kelly", start_capital=100.0,
                   kelly_fraction=0.25, kelly_cap=0.1)
    assert abs(s20.end_capital - 100.0 * 0.95 ** 20) < 1e-9


def test_kelly_cap_bindet_und_nie_ueber_kapital():
    # f geclippt auf cap=2.0 -> desired=200 -> auf vorhandenes Kapital begrenzt.
    s = simulate([_bet(2.0, True, fair=0.9)], rule="kelly", start_capital=100.0,
                 kelly_fraction=4.0, kelly_cap=2.0)
    assert s.placed[0].stake == 100.0


def test_max_drawdown_wird_gemessen():
    # +1 (Peak 101), dann -1 -1 (Tal 99) -> DD = (101-99)/101 ~ 1.98%
    s = simulate([_bet(2.0, True), _bet(2.0, False), _bet(2.0, False)],
                 rule="flat", start_capital=100.0, flat_pct=1.0)
    assert abs(s.max_drawdown_pct - (2.0 / 101.0 * 100.0)) < 1e-6


# --------------------------------------------------------------------------- #
# Stress-Check 1: Preis-Abschlag
# --------------------------------------------------------------------------- #
def test_haircut_senkt_endkapital():
    bets = [_bet(2.0, True)] * 10
    s0 = simulate(bets, rule="flat", start_capital=100.0, flat_pct=1.0, haircut_pct=0.0)
    s3 = simulate(bets, rule="flat", start_capital=100.0, flat_pct=1.0, haircut_pct=3.0)
    assert s3.end_capital < s0.end_capital               # Abschlag frisst Auszahlung


# --------------------------------------------------------------------------- #
# Stress-Check 2/3: Konzentration / Buckets
# --------------------------------------------------------------------------- #
def test_concentration_gruppiert_pnl_und_roi():
    bets = [_bet(2.0, True, bookie="A"), _bet(2.0, False, bookie="B"), _bet(2.0, True, bookie="A")]
    s = simulate(bets, rule="flat", start_capital=100.0, flat_pct=1.0)
    conc = concentration(s.placed, lambda r: r.bookie)
    assert conc["A"]["pnl"] == 2.0 and conc["A"]["n"] == 2     # zwei Siege bei A
    assert conc["B"]["pnl"] == -1.0


def test_gain_share_ist_beschraenkt_und_nie_none():
    # Grosse gegenlaeufige Gruppen, winziges Netto -> Anteile bleiben in [0,100]
    # (nicht 249% / negativ wie ein Netto-Anteil).
    bets = [_bet(2.0, True, bookie="A")] * 60 + [_bet(2.0, False, bookie="B")] * 59
    s = simulate(bets, rule="flat", start_capital=1000.0, flat_pct=0.1)   # 1 EUR flat
    conc = concentration(s.placed, lambda r: r.bookie)
    for g in conc.values():
        assert g["gain_share_pct"] is not None and 0.0 <= g["gain_share_pct"] <= 100.0
    assert conc["A"]["gain_share_pct"] == 100.0      # alle Gewinne aus A
    assert conc["B"]["gain_share_pct"] == 0.0        # B verliert -> 0 Gewinnanteil


def test_gain_share_bei_netto_verlust_kein_none():
    s = simulate([_bet(2.0, False, bookie="A")] * 3, rule="flat", start_capital=100.0, flat_pct=1.0)
    conc = concentration(s.placed, lambda r: r.bookie)
    assert conc["A"]["gain_share_pct"] == 0.0        # keine Gewinne -> 0, NICHT None


# --------------------------------------------------------------------------- #
# _assess: survives=True (benigne Daten) UND jeder Fehl-Trigger einzeln
# --------------------------------------------------------------------------- #
def _benign_report():
    sweep = [{"haircut_pct": h, "end_capital": 130.0 - 2 * h, "roi_pct": 5.0, "ruined": False}
             for h in (0.0, 1.0, 2.0, 3.0)]                 # bleibt ueberall > 100
    bookie = {"A": {"gain_share_pct": 40.0, "roi_pct": 5.0},
              "B": {"gain_share_pct": 35.0, "roi_pct": 4.0},
              "C": {"gain_share_pct": 25.0, "roi_pct": 3.0}}
    season = {"S1": {"gain_share_pct": 55.0, "roi_pct": 5.0},
              "S2": {"gain_share_pct": 45.0, "roi_pct": 4.0}}   # keine negative Saison
    buckets = {"<2.0": {"gain_share_pct": 30.0, "roi_pct": 5.0, "n": 30, "pnl": 9.0},
               "2.0-3.5": {"gain_share_pct": 40.0, "roi_pct": 5.0, "n": 40, "pnl": 12.0},
               "3.5-6.0": {"gain_share_pct": 30.0, "roi_pct": 4.0, "n": 20, "pnl": 8.0},
               ">6.0": {"gain_share_pct": 0.0, "roi_pct": 2.0, "n": 10, "pnl": 1.0}}
    return {
        "start_capital": 100.0, "n_bets": 100,
        "rules": {"flat": {"end_capital": 130.0, "roi_pct": 5.0}},
        "haircut_sweep": {"flat": sweep},
        "concentration_by_bookie": {"flat": bookie},
        "concentration_by_season": {"flat": season},
        "concentration_by_odds_bucket": {"flat": buckets},
    }


def test_assess_benigne_daten_ueberleben():
    a = _assess(_benign_report())
    assert a["survives"] is True
    assert "Vorsichtig weiterpruefen" in a["recommendation"]
    assert any("IN-SAMPLE" in c for c in a["caveats"])         # Ehrlichkeit auch im Positivfall


def test_assess_fragil_bei_haircut_faellt_durch():
    rep = _benign_report()
    rep["haircut_sweep"]["flat"][2]["end_capital"] = 99.0      # kippt bei 2% unter 100
    assert _assess(rep)["survives"] is False


def test_assess_saison_instabilitaet_faellt_durch():
    rep = _benign_report()
    rep["concentration_by_season"]["flat"]["S2"]["roi_pct"] = -8.0   # eine Saison negativ
    a = _assess(rep)
    assert a["survives"] is False
    assert any("dreht ueber Saisons" in r for r in a["reasons"])


def test_assess_devig_bias_faellt_durch():
    rep = _benign_report()
    rep["concentration_by_odds_bucket"]["flat"][">6.0"] = {
        "gain_share_pct": 0.0, "roi_pct": -5.0, "n": 60, "pnl": -20.0}    # 60% Wetten, verlieren
    assert _assess(rep)["survives"] is False


# --------------------------------------------------------------------------- #
# End-to-end auf Beispieldaten (keine Downloads)
# --------------------------------------------------------------------------- #
def test_collect_bets_chronologisch(tmp_path):
    out = tmp_path / "s.jsonl"
    to_jsonl("tests/data/footballdata_sample.csv", out)
    bets = collect_bets(str(out))
    assert bets
    assert all(b.odd > 1.0 and 0.0 < b.fair_prob < 1.0 for b in bets)
    assert [b.commence_time for b in bets] == sorted(b.commence_time for b in bets)


def test_collect_bets_ueberspringt_unsettled_und_loggt(tmp_path, caplog):
    import json
    import logging

    base = {"market": "h2h", "expected_outcomes": 3,
            "odds": {"A": {"B1": 1.8, "B2": 1.8, "B3": 2.6},
                     "B": {"B1": 4.0, "B2": 4.0, "B3": 3.0},
                     "Draw": {"B1": 4.0, "B2": 4.0, "B3": 3.0}}}
    settled = {**base, "event_id": "s", "event_name": "Settled v X",
               "commence_time": "2024-08-01T15:00:00Z", "result": "A"}
    unsettled = {**base, "event_id": "u", "event_name": "Unsettled v Y",
                 "commence_time": "2024-08-02T15:00:00Z"}            # KEIN result
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps(settled) + "\n" + json.dumps(unsettled) + "\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="arbfinder.diagnostics"):
        bets = collect_bets(str(p))
    assert bets and all("Settled" in b.event_name for b in bets)     # nur settled
    assert not any("Unsettled" in b.event_name for b in bets)
    assert any("unsettled" in r.message.lower() for r in caplog.records)


def test_diagnose_end_to_end(tmp_path):
    out = tmp_path / "s.jsonl"
    to_jsonl("tests/data/footballdata_sample.csv", out)
    rep = diagnose(str(out))
    assert set(rep["rules"]) == {"flat", "kelly"}
    assert len(rep["haircut_sweep"]["flat"]) == 4                  # 0/1/2/3 %
    assert set(rep["concentration_by_odds_bucket"]["flat"])        # Buckets vorhanden
    a = rep["assessment"]
    assert isinstance(a["survives"], bool) and a["verdict"] and a["recommendation"]
    assert any("IN-SAMPLE" in c for c in a["caveats"])             # Ehrlichkeit drin
    assert "FAZIT" in format_report(rep)


def test_diagnose_cli(tmp_path, capsys):
    data = tmp_path / "s.jsonl"
    to_jsonl("tests/data/footballdata_sample.csv", data)
    out = tmp_path / "diag.json"
    rc = cli.main(["diagnose", "--data", str(data), "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "Value-Diagnose" in text and "FAZIT" in text
    import json
    assert "assessment" in json.loads(out.read_text())
