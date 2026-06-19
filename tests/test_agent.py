from datetime import datetime, timezone

from arbfinder.agent import (
    AgentConfig,
    evaluate_opportunities,
    run_once,
    setup_alert_logger,
)
from arbfinder.detector import detect
from arbfinder.providers import MockProvider


def _cfg(**kw):
    base = dict(provider="mock", provider_kwargs={"path": "fixtures/recorded_odds.jsonl"})
    base.update(kw)
    return AgentConfig(**base)


def test_run_once_liefert_alerts():
    alerts = run_once(_cfg(), now=datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc))
    assert len(alerts) == 2
    # absteigend nach Edge sortiert:
    assert alerts[0].edge_pct >= alerts[1].edge_pct
    assert all(a.kind == "arbitrage" for a in alerts)


def test_alert_message_betont_keine_platzierung():
    alerts = run_once(_cfg(), now=datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc))
    assert "keine Wette platziert" in alerts[0].message()


def test_min_alert_edge_filtert():
    res = detect(MockProvider("fixtures/recorded_odds.jsonl"))
    assert evaluate_opportunities(res, min_alert_edge=99.0) == []
    assert len(evaluate_opportunities(res, min_alert_edge=0.0)) == 2


def test_run_once_schreibt_logfile(tmp_path):
    logfile = tmp_path / "alerts.log"
    logger = setup_alert_logger(str(logfile))
    run_once(_cfg(), alert_logger=logger,
             now=datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc))
    content = logfile.read_text(encoding="utf-8")
    assert "MELDUNG" in content
    assert "Manchester City" in content  # normalisierter Name im Alert


def test_agent_hat_keine_platzierungsfunktion():
    import arbfinder.agent as agent

    verboten = {"place_bet", "place", "bet", "stake_bet", "submit_bet", "execute"}
    assert verboten.isdisjoint(set(dir(agent)))
