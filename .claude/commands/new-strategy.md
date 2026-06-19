Baue eine neue Strategie nach dem Muster von strategies/arbitrage.py.

Argument: kurzer Name + Idee, z.B. "value: devigte faire Quote unterbieten".

Schritte:
1. Lies context.md (Abschnitt "Ideen-Backlog") und strategies/arbitrage.py.
2. Schlage mir kurz den Ansatz vor (1 Absatz) und welche Metrik er verbessern soll.
3. Implementiere strategies/$NAME.py, registriere via @register, importiere sie
   in strategies/__init__.py.
4. Schreib mindestens einen pytest-Test gegen einen konstruierten Fall.
5. Backtest gegen Baseline, plotte den Vergleich, schreib Ergebnis nach results/.
6. Sag ehrlich, ob es besser ist. Wenn nicht: behalte die Baseline, dokumentiere
   den Fehlversuch in results/experiments.md.

Leitplanke: neue Strategien dürfen nur erkennen/melden, nie automatisch setzen.
