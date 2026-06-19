import json

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
    old = {"signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 3, "realized_pnl": 10.0}
    new = {"signals": 5, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": 10.0}
    cli._compare_and_warn(old, new)
    assert "WARNUNG" in capsys.readouterr().out


def test_compare_keine_warnung_bei_echtem_fortschritt(capsys):
    old = {"signals": 2, "avg_edge_pct": 2.5, "skipped_incomplete": 1, "realized_pnl": 10.0}
    new = {"signals": 3, "avg_edge_pct": 2.6, "skipped_incomplete": 1, "realized_pnl": 12.0}
    cli._compare_and_warn(old, new)
    assert "WARNUNG" not in capsys.readouterr().out
