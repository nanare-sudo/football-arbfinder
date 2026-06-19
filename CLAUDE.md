# arbfinder — Operating Manual fuer Claude Code

## Auftrag
Dieses Projekt erkennt Sportwetten-Arbitrage (und spaeter weitere Strategien)
und MELDET sie. Du, Claude Code, darfst eigenstaendig implementieren,
Verbesserungen finden, neue Strategien bauen, sie backtesten und plotten —
INNERHALB der Leitplanken unten.

## Wie du autonom arbeitest (die Schleife)
1. Baseline messen:  `python -m arbfinder.backtest --strategy <name>`
2. Hypothese bilden: was koennte die Metriken verbessern? (context.md lesen)
3. Aenderung/neue Strategie implementieren (Muster: strategies/arbitrage.py)
4. ERNEUT backtesten und mit der Baseline vergleichen.
5. Plotten (plotting.plot_comparison) und Ergebnis nach results/ schreiben.
6. Nur behalten, was eine Metrik VERBESSERT, ohne eine andere unzulaessig zu
   verschlechtern (siehe context.md "Metriken"). Sonst zuruecknehmen.
7. Kurz dokumentieren, was du probiert hast (auch was NICHT geklappt hat).

Verfuegbare Slash-Commands: /backtest, /new-strategy, /experiment, /plot
(siehe .claude/commands/).

## Was du selbststaendig darfst
- Module implementieren und refaktorieren.
- Neue Strategy-Klassen anlegen (registrieren via @register).
- Tests schreiben/erweitern (pytest), Backtests laufen lassen, Plots erzeugen.
- Parameter-Sweeps (z.B. min_profit_pct) und sie vergleichen.

## Was du NICHT darfst (harte Leitplanken)
- KEINE automatische Platzierung von Wetten. Erkennen & melden, nie setzen.
- KEINE Erkennungs-Umgehung, kein Multi-Accounting, kein AGB-widriges Scraping,
  kein undetected-chromedriver o.ae. Echte Daten nur ueber lizenzierte APIs.
- Metriken NICHT "schoenfrisieren", indem Schutzmechanismen (Vollstaendigkeits-
  pruefung, Markt-Trennung) aufgeweicht werden. Das ist kein Fortschritt.
- Keine stillen Platzhalter, die falsche Ergebnisse liefern.

## Architektur
- arbitrage.py            Mathe-Kern (fertig, nicht umbauen)
- strategies/base.py      Strategy-Interface + Signal
- strategies/registry.py  Strategien per Name auffindbar
- strategies/arbitrage.py REFERENZ-Strategie = dein Vorbild fuer neue
- backtest.py             Eval-Harness (Metriken, optional PnL mit 'result')
- plotting.py             Vergleichs-Charts
- providers/ (TODO)       echte/mock Datenquellen — defensiv parsen
- normalize.py (TODO)     3-Stufen Team-Matching (Alias->kanonisch->Fuzzy);
                          hier liegt der echte Vorteil ggue. anderen Tools

## Lessons aus zwei oeffentlichen Repos (eingebaut halten)
- NIE markets[0] blind; nach Markttyp filtern.
- Vollstaendigkeit pruefen -> Phantom-Arbs vermeiden.
- Verworfene Events zaehlen (skipped_incomplete), nicht still schlucken.
- Normalisierung > lower(): "Man City" == "Manchester City".
- Event-Identitaet = Teams UND Anstosszeit.

## Definition of Done
Laeuft, pytest gruen, getypt, Docstrings; jede behauptete Verbesserung ist
durch einen Backtest-Vergleich BELEGT (Zahlen in results/), nicht behauptet.

## Realismus (nicht vergessen, auch im README ehrlich halten)
Edge kommt aus Daten/Tempo/Normalisierung, nicht aus Mathe. Arbs leben
Sekunden-Minuten. Ohne historische Quoten+Ergebnisse misst der Backtest nur
Detektion, nicht Profit. Margen real meist 1-3 %.

## Validierung (validation.py) — gegen Selbstbetrug, ohne Gutes zu verwerfen
Urteile dreistufig, NIE per hartem Fallbeil:
- "confirmed": in-sample Signal UND out-of-sample robust (oder reine Arbitrage,
  die keine Validierung braucht -> requires_validation=False).
- "parked": vielversprechend, aber zu wenig Belege -> NICHT verwerfen, mehr
  Daten holen. Das ist das Sicherheitsnetz gegen vorschnelles Aussortieren.
- "rejected": auch in-sample kein Signal -> raus.
Deflationierung (Mehrfachtests) ist nur INFORMATIV, nie alleiniger Grund zum
Verwerfen. Reine Arbitrage immer requires_validation=False (mathematische
Tatsache, kein Overfitting). purged_split() erst noetig bei lernenden Strategien.
