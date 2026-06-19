Führe einen Backtest aus und berichte die Metriken.

Schritte:
1. `python -m arbfinder.backtest --strategy $ARGUMENTS --data fixtures/recorded_odds.jsonl`
   (wenn keine Strategie angegeben: "arbitrage")
2. Zeige die JSON-Metriken.
3. Vergleiche mit dem letzten Lauf in results/, falls vorhanden, und sag klar,
   ob sich etwas VERBESSERT oder VERSCHLECHTERT hat — mit Zahlen.
4. Warne, wenn signals nur deshalb stiegen, weil skipped_incomplete fiel
   (das wäre aufgeweichter Schutz, kein echter Fortschritt — siehe CLAUDE.md).
