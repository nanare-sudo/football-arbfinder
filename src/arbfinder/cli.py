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
from arbfinder.strategies import Signal, all_strategies


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
    """Vergleicht mit dem letzten Lauf und warnt vor aufgeweichtem Schutz."""

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

    res = backtest.run(args.strategy, args.data)
    verdict = backtest.make_verdict(args.strategy, res)
    data = res.to_dict()
    data["verdict"] = verdict.to_dict()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))
    print(json.dumps(data, indent=2))
    print(f"\nUrteil ({args.strategy}): {verdict.status.upper()} — {verdict.reason}")
    if old:
        _compare_and_warn(old, data)
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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
