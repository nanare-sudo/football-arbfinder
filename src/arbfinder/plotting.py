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
