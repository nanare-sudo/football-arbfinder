"""
CLI-Einstieg: ``arbfinder scan`` und ``arbfinder backtest``.

Die Unterbefehle spiegeln die Slash-Commands in .claude/commands/ wider:
* ``backtest`` zeigt die Metriken, vergleicht mit dem letzten Lauf und WARNT,
  wenn mehr Signale nur daher kommen, dass die Vollstaendigkeitspruefung
  aufgeweicht wurde (skipped_incomplete gefallen) — das waere kein Fortschritt.
* ``scan`` faehrt die Live-/Mock-Detektion und MELDET Gelegenheiten. Es platziert
  NIE Wetten (harte Leitplanke).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from arbfinder import backtest
from arbfinder.detector import DetectionResult, detect
from arbfinder.providers import get_provider
from arbfinder.strategies import Signal, all_strategies, get


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #
def _build_provider(args: argparse.Namespace):
    """Baut den gewuenschten Provider; reicht relevante Optionen durch."""
    if args.provider == "mock":
        return get_provider("mock", path=args.data)
    if args.provider == "theoddsapi":
        return get_provider(
            "theoddsapi", sport=args.sport, regions=args.regions, markets=args.markets
        )
    return get_provider(args.provider)


def _format_signal(s: Signal) -> str:
    legs = s.meta.get("legs", {})
    leg_str = ", ".join(
        f"{outcome} @ {info[1]} ({info[0]})" for outcome, info in legs.items()
    )
    stake_str = ", ".join(f"{k}={v}" for k, v in s.stakes.items())
    return (
        f"  • {s.event_name} [{s.market}] kind={s.kind} edge={s.edge_pct:.2f}%\n"
        f"      Quoten: {leg_str}\n"
        f"      Einsaetze: {stake_str}"
    )


def _print_scan(res: DetectionResult) -> None:
    print(f"Provider={res.provider}  Strategie={res.strategy}")
    print(
        f"Events: {res.events_in} -> {res.events_merged} zusammengefuehrt | "
        f"Maerkte geprueft: {res.markets_checked} | "
        f"verworfen (unvollstaendig): {res.skipped_incomplete} | "
        f"verworfen (<2 Ausgaenge): {res.skipped_no_market} | "
        f"Signale: {len(res.signals)}"
    )
    if not res.signals:
        print("Keine Arbitrage-Gelegenheiten gefunden.")
    for s in res.signals:
        print(_format_signal(s))
    print("\nHinweis: Nur Erkennung/Meldung — es werden KEINE Wetten platziert.")


def _compare_and_warn(old: dict[str, Any], new: dict[str, Any]) -> None:
    """Vergleicht mit dem letzten Lauf (NUR gleiche Strategie) und warnt vor
    aufgeweichtem Schutz."""

    # Strategien-uebergreifend NICHT vergleichen: die Metriken bedeuten
    # Verschiedenes (Arbitrage = garantierter Gewinn; Value = erwarteter Vorteil
    # MIT Risiko). Ein Zahlenvergleich waere irrefuehrend.
    if old.get("strategy") != new.get("strategy"):
        print(
            f"\n(Kein Vergleich: letzter Lauf war Strategie '{old.get('strategy')}', "
            f"dieser ist '{new.get('strategy')}' — Metriken nicht vergleichbar.)"
        )
        return

    def delta(key: str) -> str:
        o, n = old.get(key), new.get(key)
        if o is None or n is None:
            return f"{key}: {o} -> {n}"
        return f"{key}: {o} -> {n} ({n - o:+})"

    print("\nVergleich zum letzten Lauf:")
    for key in ("signals", "avg_edge_pct", "skipped_incomplete", "realized_pnl"):
        print(f"  {delta(key)}")

    so, sn = old.get("signals", 0), new.get("signals", 0)
    io, in_ = old.get("skipped_incomplete", 0), new.get("skipped_incomplete", 0)
    if sn > so and in_ < io:
        print(
            "  ⚠️  WARNUNG: mehr Signale, aber skipped_incomplete GESUNKEN — "
            "das deutet auf aufgeweichten Vollstaendigkeitsschutz hin, KEIN echter "
            "Fortschritt (siehe CLAUDE.md)."
        )


# --------------------------------------------------------------------------- #
# Unterbefehle
# --------------------------------------------------------------------------- #
def _cmd_scan(args: argparse.Namespace) -> int:
    provider = _build_provider(args)
    res = detect(provider, strategy_name=args.strategy, min_profit_pct=args.min_profit)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        _print_scan(res)
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    old = json.loads(out_path.read_text()) if out_path.exists() else None

    res, verdict = backtest.run_validated(args.strategy, args.data)
    data = res.to_dict()
    data["verdict"] = verdict.to_dict()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))
    print(json.dumps(data, indent=2))
    print(f"\nUrteil ({args.strategy}): {verdict.status.upper()} — {verdict.reason}")
    if getattr(get(args.strategy), "requires_validation", True):
        print(backtest.VALIDATION_NOTE)
    if old:
        _compare_and_warn(old, data)
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    from arbfinder.providers import TheOddsApiProvider
    from arbfinder.recorder import Recorder

    provider = TheOddsApiProvider(sport=args.sport, regions=args.regions, markets=args.markets)
    if not provider.api_key:
        print("Kein ODDS_API_KEY gesetzt — Aufzeichnung braucht eine lizenzierte API "
              "(kein Scraping). Setze: export ODDS_API_KEY=...")
        return 1
    rec = Recorder(provider, args.out)
    if args.once:
        n = rec.tick()
        print(f"{n} Zeilen aufgezeichnet -> {args.out}")
    else:
        print(f"Recorder startet (alle {args.interval} min) -> {args.out}. "
              f"Nur Aufzeichnung, nie setzen. Ctrl-C zum Stoppen.")
        rec.start(args.interval)
    return 0


def _cmd_fetch_results(args: argparse.Namespace) -> int:
    from arbfinder.providers.base import ProviderError
    from arbfinder.results import TheOddsApiScores, attach_results

    source = TheOddsApiScores(sport=args.sport, days_from=args.days_from)
    if not source.api_key:
        print("Kein ODDS_API_KEY gesetzt — Ergebnisse brauchen eine lizenzierte API.")
        return 1
    try:
        n = attach_results(args.data, source)
    except ProviderError as exc:           # redigierte Meldung (kein Key/keine URL)
        print(f"Ergebnis-Abruf fehlgeschlagen: {exc}")
        return 1
    print(f"{n} Zeile(n) mit Ergebnis ergaenzt in {args.data}")
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    from arbfinder.backfill import backfill
    from arbfinder.providers import TheOddsApiProvider
    from arbfinder.providers.base import ProviderError, parse_datetime

    provider = TheOddsApiProvider(sport=args.sport, regions=args.regions, markets=args.markets)
    if not provider.api_key:
        print("Kein ODDS_API_KEY gesetzt — historischer Backfill braucht eine lizenzierte API.")
        return 1
    try:
        stats = backfill(
            provider,
            start=parse_datetime(args.start), end=parse_datetime(args.end),
            interval_minutes=args.interval, out_path=args.out,
            max_snapshots=args.max_snapshots, max_credits=args.max_credits,
        )
    except (ValueError, ProviderError) as exc:    # max_snapshots/max_credits/Datum -> klare Meldung
        print(f"Backfill abgebrochen: {exc}")
        return 1
    print(f"Backfill: {stats.snapshots} Snapshots, {stats.rows} Zeilen, "
          f"{stats.skipped} Snapshots uebersprungen, {stats.skipped_events} Events verworfen "
          f"-> {args.out} | verbraucht ~{stats.spent_credits} Credits, "
          f"Kontingent verbleibend: {stats.credits_remaining}")
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from arbfinder.diagnostics import diagnose, format_report

    report = diagnose(
        args.data, strategy_name=args.strategy, start_capital=args.capital,
        flat_pct=args.flat_pct, kelly_fraction=args.kelly_fraction, kelly_cap=args.kelly_cap,
    )
    print(format_report(report))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"\nReport -> {args.out}")
    if args.plot:
        from arbfinder import plotting
        path = plotting.plot_bankroll(report["bankroll_curve"], start_capital=args.capital,
                                      out_path=args.plot)
        print(f"Bankroll-Plot -> {path}")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="arbfinder",
        description="Sportwetten-Arbitrage erkennen und MELDEN (nie setzen).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scan", help="Provider abfragen und Gelegenheiten melden")
    sc.add_argument("--provider", default="mock", help="mock | theoddsapi | ...")
    sc.add_argument("--strategy", default="arbitrage", choices=all_strategies())
    sc.add_argument("--data", default="fixtures/recorded_odds.jsonl",
                    help="Pfad fuer den mock-Provider")
    sc.add_argument("--min-profit", dest="min_profit", type=float, default=0.0,
                    help="Mindest-Profit in %% fuer ein Signal")
    sc.add_argument("--sport", default="upcoming", help="Sport-Key (theoddsapi)")
    sc.add_argument("--regions", default="eu", help="Regionen (theoddsapi)")
    sc.add_argument("--markets", default="h2h", help="Maerkte (theoddsapi)")
    sc.add_argument("--json", action="store_true", help="Roh-JSON ausgeben")
    sc.set_defaults(func=_cmd_scan)

    bt = sub.add_parser("backtest", help="Strategie ueber aufgezeichnete Daten testen")
    bt.add_argument("--strategy", default="arbitrage", choices=all_strategies())
    bt.add_argument("--data", default="fixtures/recorded_odds.jsonl")
    bt.add_argument("--out", default="results/last_backtest.json")
    bt.set_defaults(func=_cmd_backtest)

    rec = sub.add_parser("record", help="Quoten periodisch aufzeichnen (lizenzierte API)")
    rec.add_argument("--interval", type=float, default=10.0, help="Intervall in Minuten")
    rec.add_argument("--out", default="data/recorded_odds.jsonl")
    rec.add_argument("--sport", default="upcoming", help="Sport-Key (The Odds API)")
    rec.add_argument("--regions", default="eu")
    rec.add_argument("--markets", default="h2h")
    rec.add_argument("--once", action="store_true", help="Einmal abfragen und beenden")
    rec.set_defaults(func=_cmd_record)

    fr = sub.add_parser("fetch-results", help="Ergebnisse zu aufgezeichneten Events nachtragen")
    fr.add_argument("--data", default="data/recorded_odds.jsonl")
    fr.add_argument("--sport", default="upcoming", help="Sport-Key (scores-Endpoint)")
    fr.add_argument("--days-from", dest="days_from", type=int, default=3,
                    help="Wie viele Tage zurueck Ergebnisse abgefragt werden")
    fr.set_defaults(func=_cmd_fetch_results)

    bf = sub.add_parser("backfill",
                        help="Historische Quoten ueber The Odds API nachladen (ACHTUNG: 10x Credits)")
    bf.add_argument("--sport", required=True, help="Sport-Key (z.B. soccer_epl)")
    bf.add_argument("--from", dest="start", required=True,
                    help="Start ISO8601, z.B. 2024-08-01T12:00:00Z (Pflicht)")
    bf.add_argument("--to", dest="end", required=True, help="Ende ISO8601 (Pflicht)")
    bf.add_argument("--interval", type=float, required=True,
                    help="Intervall in Minuten, z.B. 10 (Pflicht; passend zur API-Aufloesung)")
    bf.add_argument("--out", default="data/historical_odds.jsonl")
    bf.add_argument("--regions", default="eu")
    bf.add_argument("--markets", default="h2h")
    bf.add_argument("--max-snapshots", dest="max_snapshots", type=int, default=100,
                    help="Obergrenze Snapshot-Anzahl; bewusst erhoehen fuer grosse Laeufe")
    bf.add_argument("--max-credits", dest="max_credits", type=int, default=1000,
                    help="Obergrenze geschaetzte Credits (faengt viele Markets/Regions ab)")
    bf.set_defaults(func=_cmd_backfill)

    dg = sub.add_parser("diagnose",
                        help="Bestehenden Value-Lauf diagnostizieren (Bankroll + Stress-Checks)")
    dg.add_argument("--data", required=True, help="JSONL mit settled Signalen (z.B. football-data)")
    dg.add_argument("--strategy", default="value", choices=all_strategies())
    dg.add_argument("--capital", type=float, default=100.0, help="Startkapital in EUR")
    dg.add_argument("--flat-pct", dest="flat_pct", type=float, default=1.0,
                    help="Flat-Einsatz in %% des Startkapitals")
    dg.add_argument("--kelly-fraction", dest="kelly_fraction", type=float, default=0.25,
                    help="Anteil der vollen Kelly-Groesse (z.B. 0.25 = 1/4 Kelly)")
    dg.add_argument("--kelly-cap", dest="kelly_cap", type=float, default=0.1,
                    help="Obergrenze fuer den Kelly-Anteil je Wette")
    dg.add_argument("--out", default="results/diagnosis.json")
    dg.add_argument("--plot", default=None, help="optional: Pfad fuer den Bankroll-Plot (matplotlib)")
    dg.set_defaults(func=_cmd_diagnose)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
