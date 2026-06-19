"""Tests fuer download_data.py — OHNE Netzwerk (nur die Offline-Logik:
Validierung, Selbstkorrektur, Zaehlung). Der eigentliche Download wird nie
ausgefuehrt, weil alle Test-Kombinationen bereits als Datei vorliegen
(skip-if-exists-Zweig)."""
import download_data as dd

_HDR_PINN = "Div,Date,HomeTeam,AwayTeam,FTR,PSH,PSD,PSA,B365H,B365D,B365A\n"


def test_header_und_pinnacle_erkennung():
    cols = dd._header_cols(_HDR_PINN + "E1,12/08/2024,A,B,H,2.1,3.5,3.8,2.3,3.0,3.2\n")
    assert dd.looks_like_csv(cols) and dd.has_pinnacle(cols)
    # nur Pflichtfelder, KEIN Pinnacle:
    assert not dd.has_pinnacle(["Date", "HomeTeam", "AwayTeam", "FTR", "B365H"])
    # Pinnacle-SCHLUSS alleine zaehlt auch:
    assert dd.has_pinnacle(["Date", "HomeTeam", "AwayTeam", "FTR", "PSCH", "PSCD", "PSCA"])
    # kein echtes football-data-CSV:
    assert not dd.looks_like_csv(["foo", "bar"])


def test_header_cols_bom_sicher():
    assert dd._header_cols("﻿Date,HomeTeam,AwayTeam,FTR\n")[0] == "Date"


def test_is_valid_pinnacle_file(tmp_path):
    good = tmp_path / "g.csv"
    good.write_text(_HDR_PINN + "E1,12/08/2024,A,B,H,2.1,3.5,3.8,2.3,3.0,3.2\n")
    bad = tmp_path / "b.csv"
    bad.write_text("a,b,c\nx,y,z\n")
    assert dd.is_valid_pinnacle_file(good) is True
    assert dd.is_valid_pinnacle_file(bad) is False


def test_skip_if_exists_revalidiert_und_korrigiert(tmp_path):
    # (a) valide Pinnacle-Datei -> ok, bleibt liegen
    (tmp_path / "E1_2425.csv").write_text(
        _HDR_PINN + "E1,12/08/2024,A,B,H,2.1,3.5,3.8,2.3,3.0,3.2\n")
    # (b) gueltiges CSV OHNE Pinnacle -> entfernt + no_pinnacle
    (tmp_path / "E2_2425.csv").write_text(
        "Div,Date,HomeTeam,AwayTeam,FTR,B365H,B365D,B365A\nE2,12/08/2024,A,B,H,2.3,3.0,3.2\n")
    # (c) kein gueltiges CSV (Kopf fehlt) -> entfernt + missing
    (tmp_path / "E3_2425.csv").write_text("garbage,header\nx,y\n")

    res = dd.download_all(out_dir=tmp_path, leagues=("E1", "E2", "E3"), seasons=("2425",))

    assert res["ok"] == ["E1_2425.csv"]
    assert res["no_pinnacle"] == ["E2_2425.csv"]
    assert res["missing"] == ["E3_2425.csv"]
    # Liga ganz ohne Pinnacle gemeldet (E2, E3), E1 nicht:
    assert set(res["leagues_without_pinnacle"]) == {"E2", "E3"}
    # ungueltige Dateien wurden ENTFERNT (keine stillen Leichen):
    assert not (tmp_path / "E2_2425.csv").exists()
    assert not (tmp_path / "E3_2425.csv").exists()
    # valid_files_in_dir ist echt validiert (passt zum Label "gueltig"):
    assert res["valid_files_in_dir"] == ["E1_2425.csv"]


def test_valid_count_ignoriert_fremde_csv(tmp_path):
    # Eine fremde/ungueltige CSV im Ordner darf NICHT als "gueltig" zaehlen.
    (tmp_path / "ZZ_9999.csv").write_text("not,a,real,header\n")
    res = dd.download_all(out_dir=tmp_path, leagues=(), seasons=())
    assert res["valid_files_in_dir"] == []
