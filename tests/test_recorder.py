import logging
from datetime import datetime, timezone

from arbfinder.models import Event, Market
from arbfinder.providers.base import OddsProvider, read_jsonl
from arbfinder.recorder import Recorder


class _FakeProvider(OddsProvider):
    """Liefert feste Events bzw. simuliert eine fehlerhafte API-Antwort."""

    name = "fake"

    def __init__(self, events, *, raises=False, last_quota=None):
        self._events = events
        self._raises = raises
        self.last_quota = last_quota or {}

    def fetch_events(self):
        if self._raises:
            raise RuntimeError("API kaputt")
        return list(self._events)


def _event():
    return Event(
        event_id="e1", home="Man City", away="Arsenal",
        start_time=datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc),
        sport="soccer", league="EPL",
        markets=[Market("h2h", {"Man City": {"BookieA": 2.1}, "Draw": {"BookieA": 3.6},
                                "Arsenal": {"BookieA": 4.0}}, 3)],
    )


_T1 = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)
_T2 = datetime(2026, 8, 15, 13, 10, tzinfo=timezone.utc)


def test_recorder_schreibt_korrekte_zeile(tmp_path):
    out = tmp_path / "rec.jsonl"
    n = Recorder(_FakeProvider([_event()]), out).tick(now=_T1)
    assert n == 1
    rows = read_jsonl(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == _T1.isoformat()                              # Abfragezeit
    assert r["commence_time"] == "2026-08-15T15:00:00+00:00"       # echter Anstoss
    assert r["event_name"] == "Man City v Arsenal"
    assert r["market"] == "h2h" and r["expected_outcomes"] == 3
    assert r["odds"]["Man City"] == {"BookieA": 2.1}
    assert r["result"] is None                                     # spaeter nachzutragen


def test_recorder_ueberspringt_fehlerhafte_antwort_ohne_crash(tmp_path, caplog):
    out = tmp_path / "rec.jsonl"
    with caplog.at_level(logging.WARNING):
        n = Recorder(_FakeProvider([], raises=True), out).tick(now=_T1)
    assert n == 0
    assert not out.exists()                                        # nichts geschrieben
    assert any("uebersprungen" in r.message for r in caplog.records)


def test_recorder_dedupliziert_nicht_dieselben_events_ueber_zeit(tmp_path):
    out = tmp_path / "rec.jsonl"
    rec = Recorder(_FakeProvider([_event()]), out)
    rec.tick(now=_T1)
    rec.tick(now=_T2)                                              # gleicher Event, neue Zeit
    rows = read_jsonl(out)
    assert len(rows) == 2                                          # APPEND-ONLY, kein Dedup
    assert {r["ts"] for r in rows} == {_T1.isoformat(), _T2.isoformat()}


def test_recorder_loggt_api_kontingent(tmp_path, caplog):
    out = tmp_path / "rec.jsonl"
    prov = _FakeProvider([_event()], last_quota={"remaining": "123", "used": "7"})
    with caplog.at_level(logging.INFO, logger="arbfinder.recorder"):
        Recorder(prov, out).tick(now=_T1)
    assert any("123" in r.message for r in caplog.records)         # Verbrauch geloggt
