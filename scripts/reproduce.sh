#!/usr/bin/env bash
#
# reproduce.sh — regenerate the core results end-to-end from raw data.
#
# Data is downloaded from KNOWN, FIXED football-data.co.uk file URLs only
# (no scraping, no HTML parsing — see download_data.py). All derived JSON and
# plots are written to results/ (gitignored scratch). The curated copies the
# README embeds live under docs/ and are committed.
#
# Usage:   bash scripts/reproduce.sh
# Needs:   pip install -e ".[plot]"   (matplotlib only required for the plots)
#
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"
mkdir -p results

echo "=============================================================="
echo " [0/4] EPL baseline (the efficient market — expected: no edge)"
echo "=============================================================="
# Fetch the three E0 seasons used for the baseline into data/epl/ (gitignored).
python - <<'PY'
from pathlib import Path
from download_data import download_all, print_overview
print_overview(download_all(out_dir=Path("data/epl"),
                            leagues=("E0",), seasons=("2223", "2324", "2425")))
PY
python -m arbfinder.cli league-scan --csv-dir data/epl \
  --out-json results/epl_baseline.json --best-json results/epl_best.json

echo "=============================================================="
echo " [1/4] Download the less-liquid leagues  ->  data/leagues/"
echo "=============================================================="
python download_data.py

echo "=============================================================="
echo " [2/4] Less-liquid league scan (B365, devigged-close CLV, 2.0-4.0)"
echo "=============================================================="
python -m arbfinder.cli league-scan --csv-dir data/leagues \
  --out-json results/league_scan.json --best-json results/best_league.json --plots results

echo "=============================================================="
echo " [3/4] Single-season out-of-sample holdout (train ->2023/24, test 2024/25)"
echo "=============================================================="
python -m arbfinder.cli oos-test --csv-dir data/leagues \
  --out-json results/oos_clv.json --summary-json results/oos_summary.json --plots results

echo "=============================================================="
echo " [4/4] Walk-forward over rolling holdouts + pooling + 95% CI"
echo "=============================================================="
python -m arbfinder.cli oos-test --walk-forward --csv-dir data/leagues \
  --out-json results/walkforward.json --summary-json results/walkforward_summary.json --plots results

echo
echo "Done. Machine-readable verdicts:"
echo "  results/epl_baseline.json         (EPL: no robust moderate-odds edge)"
echo "  results/league_scan.json          (17 leagues ranked by mean CLV)"
echo "  results/oos_summary.json          (single-holdout verdicts)"
echo "  results/walkforward_summary.json  (pooled walk-forward verdicts + 95% CI)"
echo
echo "To refresh the committed evidence the README embeds:"
echo "  cp results/{league_scan,best_league,oos_clv,oos_summary,walkforward,walkforward_summary,epl_baseline}.json docs/results/"
echo "  cp results/{league_clv,oos_overview,walkforward_overview,walkforward_EC,walkforward_I2}.png docs/img/"
