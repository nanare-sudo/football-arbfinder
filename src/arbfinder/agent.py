"""
Agent — laesst den Detector periodisch laufen und MELDET Gelegenheiten.

>>> HARTE LEITPLANKE <<<
    Dieser Agent platziert NIEMALS Wetten. Er erkennt und meldet (Konsole +
    Logfile). Es gibt bewusst keine "place"/"bet"-Funktion.

Aufbau:
* ``evaluate_opportunities`` / ``run_once`` enthalten die Logik fuer EINEN Lauf
  und sind ohne Scheduler testbar.
* ``start`` haengt das in einen APScheduler-BlockingScheduler (optionale
  Abhaengigkeit, erst hier importiert), der ``run_once`` im Intervall aufruft.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import logging

from arbfinder.detector import DetectionResult, detect
from arbfinder.providers import get_provider
from arbfinder.strategies import Signal


@dataclass
class Alert:
    """Eine gemeldete Gelegenheit. Reine Meldung — keine Aktion am Markt."""

    ts: str
    event_name: str
    market: str
    kind: str
    edge_pct: float
    stakes: dict[str, float] = field(default_factory=dict)
    legs: dict[str, Any] = field(default_factory=dict)

    def message(self) -> str:
        legs = ", ".join(
            f"{o} @ {info[1]} ({info[0]})" for o, info in self.legs.items()
        )
        return (
            f"MELDUNG (keine Wette platziert) | {self.event_name} [{self.market}] "
            f"{self.kind} edge={self.edge_pct:.2f}% | {legs}"
        )


@dataclass
class AgentConfig:
    """Konfiguration eines Agent-Laufs."""

    provider: str = "mock"
    provider_kwargs: dict[str, Any] = field(default_factory=dict)
    strategy: str = "arbitrage"
    min_profit: float = 0.0          # Mindest-Profit fuer ein Signal (Detector)
    min_alert_edge: float = 0.0      # zusaetzliche Schwelle: ab welcher Edge melden
    interval_seconds: int = 60
    logfile: str | None = None


def _alert_from_signal(sig: Signal, now: datetime) -> Alert:
    return Alert(
        ts=now.isoformat(),
        event_name=sig.event_name,
        market=sig.market,
        kind=sig.kind,
        edge_pct=sig.edge_pct,
        stakes=dict(sig.stakes),
        legs=dict(sig.meta.get("legs", {})),
    )


def evaluate_opportunities(
    result: DetectionResult,
    *,
    min_alert_edge: float = 0.0,
    now: datetime | None = None,
) -> list[Alert]:
    """Filtert Signale nach Mindest-Edge und sortiert die Meldungen absteigend."""
    now = now or datetime.now(timezone.utc)
    chosen = sorted(
        (s for s in result.signals if s.edge_pct >= min_alert_edge),
        key=lambda s: s.edge_pct,
        reverse=True,
    )
    return [_alert_from_signal(s, now) for s in chosen]


def setup_alert_logger(logfile: str | None = None) -> logging.Logger:
    """Logger fuer Alerts: immer Konsole, optional zusaetzlich eine Datei."""
    logger = logging.getLogger("arbfinder.agent.alerts")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()  # idempotent: keine doppelten Handler bei Mehrfachaufruf

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def run_once(
    config: AgentConfig,
    *,
    alert_logger: logging.Logger | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    """Fuehrt EINEN Detektionslauf aus und meldet die Gelegenheiten."""
    log = alert_logger or setup_alert_logger(config.logfile)
    provider = get_provider(config.provider, **config.provider_kwargs)
    result = detect(provider, strategy_name=config.strategy, min_profit_pct=config.min_profit)
    alerts = evaluate_opportunities(result, min_alert_edge=config.min_alert_edge, now=now)

    log.info(
        "Lauf: %d Signale -> %d Meldungen (geprueft=%d, verworfen_unvollstaendig=%d)",
        len(result.signals), len(alerts), result.markets_checked, result.skipped_incomplete,
    )
    for a in alerts:
        log.warning(a.message())
    if not alerts:
        log.info("Keine meldenswerte Gelegenheit in diesem Lauf.")
    return alerts


def start(config: AgentConfig) -> None:  # pragma: no cover - blockierender Scheduler
    """Startet den periodischen Agent (blockiert bis Ctrl-C)."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:
        raise RuntimeError(
            "Paket 'apscheduler' fehlt. Installiere mit: pip install arbfinder[agent]"
        ) from exc

    log = setup_alert_logger(config.logfile)
    log.info(
        "Agent startet: provider=%s, strategie=%s, alle %ds. "
        "KEINE automatische Platzierung — nur Meldung.",
        config.provider, config.strategy, config.interval_seconds,
    )
    sched = BlockingScheduler()
    sched.add_job(
        lambda: run_once(config, alert_logger=log),
        trigger="interval",
        seconds=config.interval_seconds,
        id="scan",
        next_run_time=datetime.now(timezone.utc),  # sofort ein erster Lauf
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent gestoppt.")
        sched.shutdown(wait=False)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="arbfinder-agent",
        description="Periodischer Arbitrage-Scanner. MELDET nur, setzt nie.",
    )
    p.add_argument("--provider", default="mock")
    p.add_argument("--data", default="fixtures/recorded_odds.jsonl",
                   help="Pfad fuer den mock-Provider")
    p.add_argument("--strategy", default="arbitrage")
    p.add_argument("--min-profit", dest="min_profit", type=float, default=0.0)
    p.add_argument("--min-alert-edge", dest="min_alert_edge", type=float, default=0.0)
    p.add_argument("--interval", dest="interval_seconds", type=int, default=60)
    p.add_argument("--logfile", default=None)
    p.add_argument("--once", action="store_true", help="Einen Lauf ausfuehren und beenden")
    args = p.parse_args(argv)

    provider_kwargs: dict[str, Any] = {"path": args.data} if args.provider == "mock" else {}
    config = AgentConfig(
        provider=args.provider,
        provider_kwargs=provider_kwargs,
        strategy=args.strategy,
        min_profit=args.min_profit,
        min_alert_edge=args.min_alert_edge,
        interval_seconds=args.interval_seconds,
        logfile=args.logfile,
    )
    if args.once:
        run_once(config)
    else:
        start(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
