import json

import pytest

from arbfinder import cli


def test_scan_json(capsys):
    rc = cli.main(["scan", "--provider", "mock", "--data", "fixtures/recorded_odds.jsonl", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "mock"
    assert out["n_signals"] == 2
    assert out["skipped_incomplete"] == 1


def test_scan_text_meldet_keine_platzierung(capsys):
    cli.main(["scan", "--provider", "mock"])
    text = capsys.readouterr().out
    assert "KEINE Wetten platziert" in text
    assert "Manchester City" in text  # normalisiert


def test_scan_min_profit_filtert(capsys):
    cli.main(["scan", "--provider", "mock", "--min-profit", "99", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["n_signals"] == 0


def test_backtest_schreibt_und_meldet_urteil(tmp_path, capsys):
    out = tmp_path / "bt.json"
    rc = cli.main(["backtest", "--strategy", "arbitrage",
                   "--data", "fixtures/recorded_odds.jsonl", "--out", str(out)])
    assert rc == 0
    assert "CONFIRMED" in capsys.readouterr().out
    assert json.loads(out.read_text())["verdict"]["status"] == "confirmed"


def test_compare_and_warn_meldet_aufgeweichten_schutz(capsys):
    # gleiche Strategie (realer Pfad) -> Vergleich laeuft und warnt.
    old = {"strategy": "arbitrage", "signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 3, "realized_pnl": 10.0}
    new = {"strategy": "arbitrage", "signals": 5, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": 10.0}
    cli._compare_and_warn(old, new)
    assert "WARNUNG" in capsys.readouterr().out


def test_compare_keine_warnung_bei_echtem_fortschritt(capsys):
    old = {"strategy": "arbitrage", "signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": 10.0}
    new = {"strategy": "arbitrage", "signals": 3, "avg_edge_pct": 2.6, "skipped_incomplete": 1, "realized_pnl": 12.0}
    cli._compare_and_warn(old, new)
    assert "WARNUNG" not in capsys.readouterr().out


def test_compare_ueberspringt_strategiewechsel(capsys):
    # Value-Lauf darf NICHT gegen Arbitrage-Lauf verglichen werden.
    old = {"strategy": "arbitrage", "signals": 2, "skipped_incomplete": 1}
    new = {"strategy": "value", "signals": 8, "skipped_incomplete": 1}
    cli._compare_and_warn(old, new)
    out = capsys.readouterr().out
    assert "Kein Vergleich" in out
    assert "Vergleich zum letzten Lauf" not in out   # kein irrefuehrender Zahlenvergleich
    assert "WARNUNG" not in out                       # und kein Fehlalarm


# --------------------------------------------------------------------------- #
# backfill-Subcommand (gemockt, kein Netzwerk)
# --------------------------------------------------------------------------- #
def test_backfill_cli_pflichtargumente_fehlend_fehler():
    # --interval fehlt -> argparse beendet mit SystemExit (Code 2).
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["backfill", "--sport", "soccer_epl", "--from", "2024-08-01T12:00:00Z",
             "--to", "2024-08-01T13:00:00Z"])


def test_backfill_cli_ohne_key_exit_1(capsys, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    rc = cli.main(["backfill", "--sport", "soccer_epl",
                   "--from", "2024-08-01T12:00:00Z", "--to", "2024-08-01T13:00:00Z",
                   "--interval", "10"])
    assert rc == 1
    assert "ODDS_API_KEY" in capsys.readouterr().out          # klare Meldung, kein Traceback


def test_backfill_cli_zu_grosser_lauf_wird_abgebrochen(capsys, monkeypatch):
    # Dummy-Key -> Provider gebaut, aber der Lauf bricht VOR jedem Call ab (kein Netz).
    monkeypatch.setenv("ODDS_API_KEY", "dummy")
    rc = cli.main(["backfill", "--sport", "soccer_epl",
                   "--from", "2024-08-01T00:00:00Z", "--to", "2024-08-02T00:00:00Z",
                   "--interval", "1", "--max-snapshots", "10"])
    assert rc == 1
    assert "Backfill abgebrochen" in capsys.readouterr().out
