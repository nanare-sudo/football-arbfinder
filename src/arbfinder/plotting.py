"""
Plotting — damit der Agent Ergebnisse visuell vergleichen kann.

Nimmt eine oder mehrere BacktestResult-Dicts (oder JSON-Dateien) und zeichnet
Vergleichs-Charts. Erfordert matplotlib (optional dependency).

Typische Nutzung durch den Agenten: nach mehreren Backtests verschiedener
Strategien/Parameter die avg_edge_pct und realized_pnl nebeneinander plotten.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def _load(results: list[Any]) -> list[dict]:
    out = []
    for r in results:
        if isinstance(r, (str, Path)):
            out.append(json.loads(Path(r).read_text()))
        elif isinstance(r, dict):
            out.append(r)
        else:
            out.append(r.to_dict())
    return out


def plot_comparison(results: list[Any], metric: str = "avg_edge_pct",
                    out_path: str = "results/comparison.png") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = _load(results)
    labels = [d.get("strategy", f"run{i}") for i, d in enumerate(data)]
    values = [d.get(metric) or 0 for d in data]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, values, color="#3b6ea5")
    ax.set_title(f"Strategie-Vergleich — {metric}")
    ax.set_ylabel(metric)
    ax.grid(axis="y", alpha=0.3)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def _save(fig, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    return out_path


def plot_clv_histogram(clv_values: list[float], *, mean_clv: float | None = None,
                       title: str = "Closing Line Value (CLV)",
                       out_path: str = "results/clv_hist.png") -> str:
    """CLV-Histogramm mit Linie bei 0 (Schlusslinie) und beim Mittelwert."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if clv_values:
        ax.hist(clv_values, bins=30, color="#3b6ea5", alpha=0.8)
        if mean_clv is None:
            mean_clv = sum(clv_values) / len(clv_values)
    ax.axvline(0.0, color="black", linestyle="-", linewidth=1.2, label="Pinnacle-Schluss (0%)")
    if mean_clv is not None:
        ax.axvline(mean_clv, color="#c0392b", linestyle="--", linewidth=1.4,
                   label=f"Mittel {mean_clv:.2f}%")
    ax.set_title(title)
    ax.set_xlabel("CLV pro Wette (%)  — positiv = bessere Quote als der scharfe Schluss")
    ax.set_ylabel("Anzahl Wetten")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_clv_compare(clv_a: list[float], clv_b: list[float], *,
                     label_a: str = "Pinnacle-Anker", label_b: str = "Konsens-Anker",
                     out_path: str = "results/clv_compare.png") -> str:
    """CLV-Verteilungen zweier Anker ueberlagert (der Schluessel-Chart)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    allv = (clv_a or []) + (clv_b or [])
    rng = (min(allv), max(allv)) if allv else (-10, 10)
    if clv_a:
        ax.hist(clv_a, bins=30, range=rng, alpha=0.55, color="#2e8b57",
                label=f"{label_a} (Mittel {sum(clv_a)/len(clv_a):.2f}%)")
    if clv_b:
        ax.hist(clv_b, bins=30, range=rng, alpha=0.55, color="#c0392b",
                label=f"{label_b} (Mittel {sum(clv_b)/len(clv_b):.2f}%)")
    ax.axvline(0.0, color="black", linewidth=1.2)
    ax.set_title("CLV-Vergleich: Pinnacle-Anker vs. Konsens-Anker")
    ax.set_xlabel("CLV pro Wette (%)")
    ax.set_ylabel("Anzahl Wetten")
    if clv_a or clv_b:                               # sonst: 'No artists with labels' Warnung
        ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_season_roi(roi_a: dict[str, float], roi_b: dict[str, float], *,
                    label_a: str = "Pinnacle-Anker", label_b: str = "Konsens-Anker",
                    out_path: str = "results/season_roi.png") -> str:
    """ROI je Saison als gruppierte Balken (visualisiert den Vorzeichenwechsel)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seasons = sorted(set(roi_a) | set(roi_b))
    x = range(len(seasons))
    a = [roi_a.get(s) or 0.0 for s in seasons]
    b = [roi_b.get(s) or 0.0 for s in seasons]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([i - 0.2 for i in x], a, width=0.4, color="#2e8b57", label=label_a)
    ax.bar([i + 0.2 for i in x], b, width=0.4, color="#c0392b", label=label_b)
    ax.axhline(0.0, color="black", linewidth=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels(seasons)
    ax.set_title("ROI je Saison — Vorzeichenwechsel behoben?")
    ax.set_ylabel("ROI auf Umsatz (%)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_odds_buckets(buckets: list[dict], *, out_path: str = "results/odds_buckets.png") -> str:
    """PnL/ROI je Quoten-Bucket (Pinnacle-Anker)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [b.get("bucket", "?") for b in buckets]
    rois = [b.get("roi_pct") or 0.0 for b in buckets]
    ns = [b.get("n_bets") or 0 for b in buckets]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(names, rois, color="#3b6ea5")
    ax.axhline(0.0, color="black", linewidth=1.0)
    for bar, n in zip(bars, ns):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"n={n}",
                ha="center", va="bottom", fontsize=8)
    ax.set_title("ROI je Quoten-Bucket (Pinnacle-Anker)")
    ax.set_xlabel("Quoten-Bereich")
    ax.set_ylabel("ROI auf Umsatz (%)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_haircut(haircut_rows: list[dict], *, out_path: str = "results/haircut.png") -> str:
    """ROI gegen Preis-Abschlag (Ausfuehrbarkeit)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in haircut_rows if r.get("haircut_pct") is not None]
    hs = [r["haircut_pct"] for r in rows]
    rois = [r.get("roi_pct") or 0.0 for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(hs, rois, marker="o", color="#8e44ad", linewidth=1.5)
    ax.axhline(0.0, color="black", linewidth=1.0)
    ax.set_title("Preis-Abschlag-Sensitivitaet (Ausfuehrbarkeit)")
    ax.set_xlabel("Abschlag je Quote (%)")
    ax.set_ylabel("ROI auf Umsatz (%)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_bankroll(curves: dict[str, list[float]], start_capital: float = 100.0,
                  out_path: str = "results/bankroll.png") -> str:
    """Zeichnet die Bankroll-Kurve(n) der Diagnose (eine Linie je Einsatzregel)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for rule, curve in curves.items():
        ax.plot(range(1, len(curve) + 1), curve, label=rule, linewidth=1.3)
    ax.axhline(start_capital, color="grey", linestyle="--", alpha=0.6, label="Start")
    ax.set_title("Bankroll-Kurve (chronologisch)")
    ax.set_xlabel("Wett-Nr.")
    ax.set_ylabel("Kapital (EUR)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def plot_league_clv(ranking: list[dict], *,
                    out_path: str = "results/league_clv.png") -> str:
    """Mittleres CLV je Liga als sortierte Balken (gegen die devigte Pinnacle-Schluss).

    Erwartet die ``ranking``-Liste aus leaguescan (bereits nach mean_clv sortiert):
    je Eintrag ``{league, mean_clv_pct, n_with_clv, robust}``. Gruen = robust,
    blau = positiv aber nicht robust, rot = negativ. n wird annotiert (Klein-N
    bleibt sichtbar).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in ranking if r.get("mean_clv_pct") is not None]
    labels = [r["league"] for r in rows]
    vals = [r["mean_clv_pct"] for r in rows]
    ns = [r.get("n_with_clv") or 0 for r in rows]

    def color(r: dict, v: float) -> str:
        if r.get("robust"):
            return "#2e8b57"          # robust positiv
        return "#3b6ea5" if v > 0 else "#c0392b"

    colors = [color(r, v) for r, v in zip(rows, vals)]
    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 0.9), 4.6))
    bars = ax.bar(labels, vals, color=colors)
    ax.axhline(0.0, color="black", linewidth=1.0)
    for b, n in zip(bars, ns):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"n={n}",
                ha="center", va="bottom" if b.get_height() >= 0 else "top", fontsize=8)
    ax.set_title("Mittleres CLV je Liga — gegen DEVIGTE Pinnacle-Schluss "
                 "(gruen=robust, blau=+ nicht robust, rot=−)")
    ax.set_ylabel("mean CLV pro Wette (%)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_oos_league(league: str, in_stats: dict, out_stats: dict, *,
                    status: str = "", uncertain: bool = False,
                    out_path: str = "results/oos_league.png") -> str:
    """In-Sample vs. Out-of-Sample je Liga: links mean CLV, rechts share_positive.

    ``in_stats``/``out_stats`` haben ``mean_clv_pct``, ``share_positive_clv_pct``,
    ``n_with_clv``. None-Werte (leeres Holdout) werden als 0 mit n-Annotation
    gezeichnet — kein stiller Platzhalter.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def val(d: dict, key: str) -> float:
        v = d.get(key)
        return float(v) if v is not None else 0.0

    labels = ["In-Sample", "Out-of-Sample"]
    colors = ["#3b6ea5", "#2e8b57"]
    ns = [in_stats.get("n_with_clv") or 0, out_stats.get("n_with_clv") or 0]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.6))
    clv = [val(in_stats, "mean_clv_pct"), val(out_stats, "mean_clv_pct")]
    b1 = ax1.bar(labels, clv, color=colors)
    ax1.axhline(0.0, color="black", linewidth=1.0)
    for bar, n in zip(b1, ns):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"n={n}",
                 ha="center", va="bottom" if bar.get_height() >= 0 else "top", fontsize=9)
    ax1.set_title("mean CLV (%) vs DEVIGTE Pinnacle-Schluss")
    ax1.set_ylabel("mean CLV pro Wette (%)")
    ax1.grid(axis="y", alpha=0.3)

    share = [val(in_stats, "share_positive_clv_pct"), val(out_stats, "share_positive_clv_pct")]
    ax2.bar(labels, share, color=colors)
    ax2.axhline(50.0, color="grey", linestyle="--", alpha=0.7, label="50 %")
    ax2.set_title("Anteil positiver CLV (%)")
    ax2.set_ylabel("share positiv (%)")
    ax2.set_ylim(0, 100)
    ax2.legend()
    ax2.grid(axis="y", alpha=0.3)

    suffix = " — UNSICHER" if uncertain else ""
    fig.suptitle(f"OOS-CLV {league}: Urteil = {status or 'n/a'}{suffix}", fontsize=12)
    fig.tight_layout()
    return _save(fig, out_path)


def plot_oos_overview(rows: list[dict], *, out_path: str = "results/oos_overview.png") -> str:
    """Uebersicht: mean CLV In-Sample vs. Out-of-Sample je Liga (gruppierte Balken)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [f"{r['league']}{'*' if r.get('uncertain') else ''}" for r in rows]
    in_v = [float(r["in_mean"]) if r.get("in_mean") is not None else 0.0 for r in rows]
    out_v = [float(r["out_mean"]) if r.get("out_mean") is not None else 0.0 for r in rows]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 1.1), 4.6))
    ax.bar([i - 0.2 for i in x], in_v, width=0.4, color="#3b6ea5", label="In-Sample")
    ax.bar([i + 0.2 for i in x], out_v, width=0.4, color="#2e8b57", label="Out-of-Sample (2024/25)")
    ax.axhline(0.0, color="black", linewidth=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_title("CLV In-Sample vs. Out-of-Sample je Liga (* = unsicher)")
    ax.set_ylabel("mean CLV pro Wette (%)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_path)
