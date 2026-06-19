import logging
from datetime import datetime, timedelta, timezone

import pytest

from arbfinder.backfill import backfill
from arbfinder.models import Event, Market
from arbfinder.providers.base import read_jsonl

_START = datetime(2024, 8, 1, 12, 0, tzinfo=timezone.utc)


def _ev():
    return Event(
        event_id="h1", home="Home", away="Away",
        start_time=datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc),
        markets=[Market("h2h", {"Home": {"B": 2.0}, "Draw": {"B": 3.4}, "Away": {"B": 3.6}}, 3)],
    )


class _FakeHist:
    """Dupliziert die fuer den Backfill genutzte Oberflaeche von TheOddsApiProvider."""

    name = "theoddsapi"

    def __init__(self, *, regions="eu", markets="h2h", fail_at=frozenset(), remaining_seq=None):
        self.regions, self.markets = regions, markets
        self.calls: list[datetime] = []
        self.last_quota: dict = {}
        self._fail_at = set(fail_at)
        self._remaining_seq = remaining_seq

    def fetch_historical(self, date):
        i = len(self.calls)
        self.calls.append(date)
        if i in self._fail_at:
            raise RuntimeError("boom")
        remaining = self._remaining_seq[i] if self._remaining_seq else "490"
        self.last_quota = {"remaining": remaining, "used": str(10 * (i + 1)), "last": "10"}
        return [_ev()], date - timedelta(minutes=2)     # API liefert naechstgelegenen Snapshot


def test_backfill_schreibt_zeilen_mit_echter_snapshot_ts(tmp_path):
    prov = _FakeHist()
    out = tmp_path / "h.jsonl"
    stats = backfill(prov, start=_START, end=_START + timedelta(minutes=20),
                     interval_minutes=10, out_path=out)
    assert (stats.snapshots, stats.rows, stats.skipped) == (3, 3, 0)
    assert prov.calls == [_START, _START + timedelta(minutes=10), _START + timedelta(minutes=20)]
    rows = read_jsonl(out)
    assert len(rows) == 3
    assert rows[0]["ts"] == (_START - timedelta(minutes=2)).isoformat()   # echte Snapshot-Zeit
    assert rows[0]["commence_time"] == "2024-08-01T15:00:00+00:00"
    assert rows[0]["odds"]["Home"] == {"B": 2.0}


def test_backfill_ueberspringt_fehlerhaften_snapshot(tmp_path, caplog):
    prov = _FakeHist(fail_at={1})
    out = tmp_path / "h.jsonl"
    with caplog.at_level(logging.WARNING, logger="arbfinder.backfill"):
        stats = backfill(prov, start=_START, end=_START + timedelta(minutes=20),
                         interval_minutes=10, out_path=out)
    assert (stats.snapshots, stats.skipped, stats.rows) == (2, 1, 2)      # 2. Call kaputt
    assert any("uebersprungen" in r.message for r in caplog.records)


def test_backfill_bricht_vor_jedem_call_ab_wenn_zu_gross(tmp_path):
    prov = _FakeHist()
    with pytest.raises(ValueError):
        backfill(prov, start=_START, end=_START + timedelta(minutes=60),
                 interval_minutes=10, out_path=tmp_path / "h.jsonl", max_snapshots=2)
    assert prov.calls == []                                               # kein Call -> kein Credit verbrannt


def test_backfill_stoppt_bei_erschoepftem_kontingent(tmp_path, caplog):
    prov = _FakeHist(remaining_seq=["100", "0", "100"])
    with caplog.at_level(logging.WARNING, logger="arbfinder.backfill"):
        stats = backfill(prov, start=_START, end=_START + timedelta(minutes=20),
                         interval_minutes=10, out_path=tmp_path / "h.jsonl")
    assert len(prov.calls) == 2 and stats.snapshots == 2                  # nach remaining=0 gestoppt
    assert any("erschoepft" in r.message for r in caplog.records)


def test_backfill_loggt_kostenschaetzung(tmp_path, caplog):
    prov = _FakeHist(markets="h2h,totals", regions="eu,uk")
    with caplog.at_level(logging.WARNING, logger="arbfinder.backfill"):
        backfill(prov, start=_START, end=_START + timedelta(minutes=10),
                 interval_minutes=10, out_path=tmp_path / "h.jsonl")
    # 2 Snapshots * 10x * 2 Markets * 2 Regions = 80 Credits
    assert any("80 Credits" in r.message for r in caplog.records)
