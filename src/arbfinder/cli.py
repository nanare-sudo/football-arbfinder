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


def _cmd_pinnacle_run(args: argparse.Namespace) -> int:
    from arbfinder import pinnacle

    try:
        report, plotdata = pinnacle.run(
            args.csv, bet_source=args.bet_source, anchor=args.anchor, min_edge=args.min_edge)
    except (ValueError, OSError) as exc:
        print(f"Pinnacle-Lauf fehlgeschlagen: {exc}")
        return 1

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"JSON -> {args.out_json}")
    if args.plots:
        try:
            for pth in pinnacle.make_plots(plotdata, args.plots):
                print(f"Plot -> {pth}")
        except Exception as exc:                       # noqa: BLE001 - z.B. matplotlib fehlt
            print(f"Plots uebersprungen: {exc}")
    print("\n" + pinnacle.summary_text(report))
    return 0


def _cmd_league_scan(args: argparse.Namespace) -> int:
    from arbfinder import leaguescan

    criteria = {}
    if args.min_mean_clv is not None:
        criteria["min_mean_clv_pct"] = args.min_mean_clv
    if args.min_share is not None:
        criteria["min_share_positive_pct"] = args.min_share
    if args.min_bets is not None:
        criteria["min_bets"] = args.min_bets

    try:
        report, plotdata = leaguescan.scan(
            args.csv_dir, bet_source=args.bet_source, min_edge=args.min_edge,
            odds_min=args.odds_min, odds_max=args.odds_max,
            robust_criteria=criteria or None)
    except (ValueError, OSError) as exc:
        print(f"Liga-Scan fehlgeschlagen: {exc}")
        return 1

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"JSON -> {args.out_json}")

    best = leaguescan.best_league_report(report)
    best_out = Path(args.best_json)
    best_out.parent.mkdir(parents=True, exist_ok=True)
    best_out.write_text(json.dumps(best, indent=2))
    print(f"Beste Liga -> {args.best_json}")

    if args.plots:
        try:
            for pth in leaguescan.make_plots(report, plotdata, args.plots):
                print(f"Plot -> {pth}")
        except Exception as exc:                       # noqa: BLE001 - z.B. matplotlib fehlt
            print(f"Plots uebersprungen: {exc}")

    if report["meta"]["skipped_files"]:
        print(f"\n{report['meta']['n_files_skipped']} CSV(s) uebersprungen "
              f"(keine PS*/Bet-Quelle): "
              + ", ".join(s["file"] for s in report["meta"]["skipped_files"]))
    print("\n" + leaguescan.summary_text(report))
    return 0


def _cmd_oos_test(args: argparse.Namespace) -> int:
    from arbfinder import oos

    kw: dict[str, Any] = {}
    if args.candidates:
        kw["candidates"] = tuple(args.candidates)
    if args.uncertain is not None:
        kw["uncertain"] = tuple(args.uncertain)

    wf = args.walk_forward
    # Default-Ausgabepfade je Modus (nur, wenn nicht explizit gesetzt).
    out_path = args.out_json or ("results/walkforward.json" if wf else "results/oos_clv.json")
    sum_path = args.summary_json or ("results/walkforward_summary.json" if wf else "results/oos_summary.json")

    try:
        if wf:
            report, plotdata = oos.run_walkforward(
                args.csv_dir, bet_source=args.bet_source, min_edge=args.min_edge,
                odds_min=args.odds_min, odds_max=args.odds_max, min_train=args.min_train,
                min_oos=args.min_oos, min_samples=args.min_samples, **kw)
            summary = oos.walkforward_summary(report)
            text = oos.walkforward_summary_text(report)
            make = oos.make_walkforward_plots
        else:
            report, plotdata = oos.run(
                args.csv_dir, bet_source=args.bet_source, min_edge=args.min_edge,
                odds_min=args.odds_min, odds_max=args.odds_max,
                min_oos=args.min_oos, min_samples=args.min_samples, **kw)
            summary = oos.summary_report(report)
            text = oos.summary_text(report)
            make = oos.make_plots
    except (ValueError, OSError) as exc:
        print(f"OOS-Test fehlgeschlagen: {exc}")
        return 1

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"JSON -> {out_path}")

    sout = Path(sum_path)
    sout.parent.mkdir(parents=True, exist_ok=True)
    sout.write_text(json.dumps(summary, indent=2))
    print(f"Urteile -> {sum_path}")

    if args.plots:
        try:
            for pth in make(report, plotdata, args.plots):
                print(f"Plot -> {pth}")
        except Exception as exc:                       # noqa: BLE001 - z.B. matplotlib fehlt
            print(f"Plots uebersprungen: {exc}")
    print("\n" + text)
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
    dg.add_argument("--out", default=None, help="optional: Pfad fuer den JSON-Report")
    dg.add_argument("--plot", default=None, help="optional: Pfad fuer den Bankroll-Plot (matplotlib)")
    dg.set_defaults(func=_cmd_diagnose)

    pr = sub.add_parser("pinnacle-run",
                        help="Pinnacle-Anker + Closing Line Value (CLV) auf E0-CSVs")
    pr.add_argument("--csv", nargs="+", required=True, help="football-data E0 CSV-Datei(en)")
    pr.add_argument("--out-json", dest="out_json", default="results/pinnacle_clv_run.json")
    pr.add_argument("--plots", default=None, help="Ordner fuer die PNG-Plots (matplotlib)")
    pr.add_argument("--bet-source", dest="bet_source", default="Max", help="Bet-Quelle (Max|B365|...)")
    pr.add_argument("--anchor", default="open", choices=["open", "close"],
                    help="Pinnacle-Anker: Eroeffnung (default) oder Schluss")
    pr.add_argument("--min-edge", dest="min_edge", type=float, default=2.0)
    pr.set_defaults(func=_cmd_pinnacle_run)

    ls = sub.add_parser("league-scan",
                        help="Pinnacle-Anker + devigtes CLV ueber mehrere (weniger liquide) Ligen")
    ls.add_argument("--csv-dir", dest="csv_dir", required=True,
                    help="Ordner mit football-data CSVs (je Liga eine/mehrere Dateien)")
    ls.add_argument("--out-json", dest="out_json", default="results/league_scan.json")
    ls.add_argument("--best-json", dest="best_json", default="results/best_league.json")
    ls.add_argument("--plots", default=None, help="Ordner fuer die PNG-Plots (matplotlib)")
    ls.add_argument("--bet-source", dest="bet_source", default="B365",
                    help="EINE realistisch erreichbare Quelle (Default B365, NICHT Max)")
    ls.add_argument("--min-edge", dest="min_edge", type=float, default=2.0,
                    help="Mindest-Edge (%%) am Eroeffnungs-Anker fuer die Selektion")
    ls.add_argument("--odds-min", dest="odds_min", type=float, default=2.0,
                    help="untere Quotengrenze (moderate Quoten, Default 2.0)")
    ls.add_argument("--odds-max", dest="odds_max", type=float, default=4.0,
                    help="obere Quotengrenze (moderate Quoten, Default 4.0)")
    ls.add_argument("--min-mean-clv", dest="min_mean_clv", type=float, default=None,
                    help="Robust-Schwelle: Mindest-mean-CLV %% (Default 0.5)")
    ls.add_argument("--min-share", dest="min_share", type=float, default=None,
                    help="Robust-Schwelle: Mindest-Anteil positiver CLV %% (Default 55)")
    ls.add_argument("--min-bets", dest="min_bets", type=int, default=None,
                    help="Robust-Schwelle: Mindest-Stichprobe (Default 50)")
    ls.set_defaults(func=_cmd_league_scan)

    oo = sub.add_parser("oos-test",
                        help="Out-of-Sample-CLV-Holdout fuer die Kandidaten-Ligen (EC/I2/F2; SC3 unsicher)")
    oo.add_argument("--csv-dir", dest="csv_dir", default="data/leagues",
                    help="Ordner mit den Liga-CSVs (Train- + Holdout-Saisons)")
    oo.add_argument("--walk-forward", dest="walk_forward", action="store_true",
                    help="rollende Holdouts ueber mehrere Saisons + Pooling + 95%%-KI")
    oo.add_argument("--min-train", dest="min_train", type=int, default=3,
                    help="Mindest-Trainingsfenster (Saisons) vor einem Holdout (Walk-Forward)")
    oo.add_argument("--out-json", dest="out_json", default=None,
                    help="Default: results/oos_clv.json bzw. results/walkforward.json")
    oo.add_argument("--summary-json", dest="summary_json", default=None,
                    help="Default: results/oos_summary.json bzw. results/walkforward_summary.json")
    oo.add_argument("--plots", default=None, help="Ordner fuer die PNG-Plots (matplotlib)")
    oo.add_argument("--bet-source", dest="bet_source", default="B365")
    oo.add_argument("--min-edge", dest="min_edge", type=float, default=2.0)
    oo.add_argument("--odds-min", dest="odds_min", type=float, default=2.0)
    oo.add_argument("--odds-max", dest="odds_max", type=float, default=4.0)
    oo.add_argument("--min-oos", dest="min_oos", type=float, default=0.5,
                    help="OOS 'robust positiv': Mindest-mean-CLV %% (Default 0.5)")
    oo.add_argument("--min-samples", dest="min_samples", type=int, default=30,
                    help="OOS-Mindeststichprobe; darunter -> parked statt confirmed (Default 30)")
    oo.add_argument("--candidates", nargs="*", default=None,
                    help="Liga-Codes als Kandidaten (Default EC I2 F2)")
    oo.add_argument("--uncertain", nargs="*", default=None,
                    help="Liga-Codes, die als unsicher markiert werden (Default SC3)")
    oo.set_defaults(func=_cmd_oos_test)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
