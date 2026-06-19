# arbfinder — autonomes Setup

Erkennt Sportwetten-Arbitrage und meldet sie. Gebaut so, dass Claude Code
eigenständig Strategien implementieren, backtesten und plotten kann.

## Start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,plot]"
pytest
python -m arbfinder.backtest --strategy arbitrage   # Baseline
```

## Die autonome Schleife (siehe CLAUDE.md)
messen -> Hypothese -> implementieren -> erneut messen -> plotten -> behalten,
wenn belegt besser. Slash-Commands: /backtest /new-strategy /experiment /plot

## Ehrliche Grenzen
Ohne historische Quoten UND Ergebnisse misst der Backtest nur Detektion, nicht
Profit. Edge kommt aus Daten/Tempo/Normalisierung, nicht aus Mathe. Margen real 1-3 %.

## Leitplanken
Nur erkennen/melden, nie automatisch setzen. Keine Erkennungs-Umgehung, kein
AGB-widriges Scraping. Daten nur über lizenzierte APIs.
