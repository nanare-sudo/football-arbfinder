from datetime import datetime, timezone

from arbfinder import cli
from arbfinder.diagnostics import (
    BetRecord,
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
