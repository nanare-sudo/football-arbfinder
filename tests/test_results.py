import json
from datetime import datetime, timezone

import pytest

from arbfinder.providers.base import ProviderError, read_jsonl
from arbfinder.results import (
    EventResult,
    ResultSource,
    TheOddsApiScores,
    attach_results,
    parse_scores,
)


def _dt(s):
    return datetime.fromisoformat(s)


class _FakeSource(ResultSource):
    name = "fake"

    def __init__(self, results):
        self._results = results

    def results(self):
        return list(self._results)


# --------------------------------------------------------------------------- #
# parse_scores: Sieger/Remis/offen
# --------------------------------------------------------------------------- #
_SCORES = [
    {"home_team": "Manchester City", "away_team": "Arsenal",
     "commence_time": "2026-08-15T15:00:00Z", "completed": True,
     "scores": [{"name": "Manchester City", "score": "2"}, {"name": "Arsenal", "score": "1"}]},
    {"home_team": "Spurs", "away_team": "United",
     "commence_time": "2026-08-16T14:00:00Z", "completed": True,
     "scores": [{"name": "Spurs", "score": "1"}, {"name": "United", "score": "1"}]},
    {"home_team": "Liverpool", "away_team": "Chelsea",
     "commence_time": "2026-08-17T14:00:00Z", "completed": False, "scores": None},
]


def test_parse_scores_sieger_remis_offen():
    res = {(r.home, r.away): r for r in parse_scores(_SCORES)}
    assert res[("Manchester City", "Arsenal")].winner == "Manchester City"   # Heimsieg
    assert res[("Spurs", "United")].winner == "Draw"                          # Remis
    assert res[("Liverpool", "Chelsea")].winner is None                       # nicht final


def test_parse_scores_erfindet_keinen_sieger_bei_fehlenden_scores():
    raw = [
        {"home_team": "A", "away_team": "B", "commence_time": "2026-08-15T15:00:00Z",
         "completed": True, "scores": []},                       # final, aber keine Scores
        {"home_team": "A", "away_team": "B", "commence_time": "2026-08-15T15:00:00Z",
         "completed": True, "scores": [{"name": "A", "score": "2"}]},   # nur ein Score
        {"away_team": "B", "commence_time": "2026-08-15T15:00:00Z",
         "completed": True, "scores": []},                       # home_team fehlt -> skip
    ]
    res = parse_scores(raw)
    assert len(res) == 2                                         # drittes uebersprungen
    assert res[0].winner is None and res[1].winner is None       # NICHTS erfunden


def test_parse_scores_zweiweg_kein_remis():
    raw = [{"home_team": "Nadal", "away_team": "Federer", "commence_time": "2026-08-15T15:00:00Z",
            "completed": True, "scores": [{"name": "Nadal", "score": "2"}, {"name": "Federer", "score": "0"}]}]
    assert parse_scores(raw)[0].winner == "Nadal"


# --------------------------------------------------------------------------- #
# attach_results: Match ueber Event-Identitaet, nicht rohe Namen
# --------------------------------------------------------------------------- #
def _snapshot_row(**over):
    row = {
        "ts": "2026-08-15T13:00:00Z", "commence_time": "2026-08-15T15:00:00Z",
        "event_id": "e1", "event_name": "Man City v Arsenal", "market": "h2h",
        "expected_outcomes": 3,
        "odds": {"Man City": {"A": 2.1}, "Draw": {"A": 3.6}, "Arsenal": {"A": 4.0}},
        "result": None,
    }
    row.update(over)
    return row


def _write(tmp_path, rows):
    p = tmp_path / "data.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_attach_results_matcht_ueber_identitaet_und_alias(tmp_path):
    p = _write(tmp_path, [_snapshot_row()])
    # Sieger heisst "Manchester City" (anderer Schreibweise als die Snapshot-Keys).
    src = _FakeSource([EventResult("Manchester City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Manchester City")])
    updated = attach_results(p, src)
    assert updated == 1
    # result = der Ausgangs-Schluessel, der in den Snapshot-Quoten vorkommt:
    assert read_jsonl(p)[0]["result"] == "Man City"


def test_attach_results_remis(tmp_path):
    p = _write(tmp_path, [_snapshot_row()])
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Draw")])
    attach_results(p, src)
    assert read_jsonl(p)[0]["result"] == "Draw"


def test_attach_results_ignoriert_anderen_tag(tmp_path):
    # Gleiche Teams, aber anderer Anstoss-Tag -> KEIN Match (Identitaet = Teams UND Zeit).
    p = _write(tmp_path, [_snapshot_row()])
    src = _FakeSource([EventResult("Manchester City", "Arsenal",
                                   _dt("2026-11-02T15:00:00+00:00"), "Manchester City")])
    assert attach_results(p, src) == 0
    assert read_jsonl(p)[0]["result"] is None


def test_attach_results_ignoriert_andere_teams(tmp_path):
    p = _write(tmp_path, [_snapshot_row()])
    src = _FakeSource([EventResult("Liverpool", "Chelsea",
                                   _dt("2026-08-15T15:00:00+00:00"), "Liverpool")])
    assert attach_results(p, src) == 0


def test_attach_results_probiert_weitere_treffer_wenn_mapping_scheitert(tmp_path):
    # Zwei Ergebnisse fuer dasselbe Event: das erste laesst sich NICHT abbilden
    # (unbekannter Siegername), das zweite schon -> das zweite gewinnt.
    p = _write(tmp_path, [_snapshot_row()])
    src = _FakeSource([
        EventResult("Man City", "Arsenal", _dt("2026-08-15T15:00:00+00:00"), "Unbekanntes Team"),
        EventResult("Man City", "Arsenal", _dt("2026-08-15T15:00:00+00:00"), "Manchester City"),
    ])
    assert attach_results(p, src) == 1
    assert read_jsonl(p)[0]["result"] == "Man City"


def test_attach_results_remis_variante_X(tmp_path):
    # Snapshot nutzt 'X' als Remis-Schluessel; Sieger 'Draw' -> result == 'X'.
    p = _write(tmp_path, [_snapshot_row(
        odds={"Man City": {"A": 2.5}, "X": {"A": 3.2}, "Arsenal": {"A": 2.8}})])
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Draw")])
    attach_results(p, src)
    assert read_jsonl(p)[0]["result"] == "X"


def test_attach_results_aktualisiert_alle_snapshots_eines_events(tmp_path):
    # Append-only: dasselbe Event hat mehrere Snapshot-Zeilen -> ALLE bekommen result.
    r1 = _snapshot_row(ts="2026-08-15T13:00:00Z")
    r2 = _snapshot_row(ts="2026-08-15T13:10:00Z")
    other = _snapshot_row(event_id="o", event_name="Liverpool v Chelsea",
                          commence_time="2026-08-17T14:00:00Z",
                          odds={"Liverpool": {"A": 2.0}, "Draw": {"A": 3.3}, "Chelsea": {"A": 3.5}})
    p = _write(tmp_path, [r1, r2, other])
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Man City")])
    assert attach_results(p, src) == 2
    assert [r["result"] for r in read_jsonl(p)] == ["Man City", "Man City", None]


def test_attach_results_erhaelt_kaputte_json_zeile(tmp_path, caplog):
    import logging

    p = tmp_path / "d.jsonl"
    p.write_text("// kommentar\n{kaputt\n" + json.dumps(_snapshot_row()) + "\n", encoding="utf-8")
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Man City")])
    with caplog.at_level(logging.WARNING):
        assert attach_results(p, src) == 1
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "// kommentar" and lines[1] == "{kaputt"     # beide erhalten


def test_attach_results_keine_aenderung_laesst_datei_unveraendert(tmp_path):
    p = _write(tmp_path, [_snapshot_row()])
    before = p.read_text(encoding="utf-8")
    src = _FakeSource([])                                            # keine Ergebnisse
    assert attach_results(p, src) == 0
    assert p.read_text(encoding="utf-8") == before                   # byte-identisch


def test_attach_results_ueberschreibt_bestehendes_nicht(tmp_path):
    p = _write(tmp_path, [_snapshot_row(result="Arsenal")])
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Man City")])
    assert attach_results(p, src) == 0                      # vorhandenes result bleibt
    assert read_jsonl(p)[0]["result"] == "Arsenal"


def test_attach_results_erhaelt_kommentarzeilen(tmp_path):
    p = tmp_path / "data.jsonl"
    p.write_text("// kommentar\n" + json.dumps(_snapshot_row()) + "\n", encoding="utf-8")
    src = _FakeSource([EventResult("Man City", "Arsenal",
                                   _dt("2026-08-15T15:00:00+00:00"), "Man City")])
    attach_results(p, src)
    assert p.read_text(encoding="utf-8").splitlines()[0] == "// kommentar"   # Kommentar bleibt


# --------------------------------------------------------------------------- #
# Mocked API (kein echter Netzwerk-Call)
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, data, *, ok=True, status_code=200):
        self._data, self.ok, self.status_code = data, ok, status_code

    def json(self):
        return self._data


def test_theoddsapi_scores_fetch_gemockt(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(_SCORES))
    src = TheOddsApiScores(sport="soccer_epl", api_key="dummy")
    res = src.results()
    assert any(r.winner == "Manchester City" for r in res)


def test_theoddsapi_scores_ohne_key_wirft():
    with pytest.raises(ProviderError):
        TheOddsApiScores(sport="soccer_epl", api_key="").results()


def test_scores_http_fehler_leakt_keinen_key(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp([], ok=False, status_code=429))
    src = TheOddsApiScores(sport="soccer_epl", api_key="SECRET123")
    with pytest.raises(ProviderError) as ei:
        src.results()
    assert "SECRET123" not in str(ei.value) and "429" in str(ei.value)
